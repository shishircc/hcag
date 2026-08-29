"""Session-startup phases for the voice agent (§5.4).

Two ordered phases, both run before the room opens to input:

1. `preload_initial_packets` — feed each configured packet ID through
   `memory.check_and_load_kb` so the very first user turn already has the
   relevant knowledge in the active set (§5.4.1). The AgentRuntime's history
   is extended with real tool-result blocks — byte-identical to what an
   in-turn call would produce — so §2.12 caching applies unchanged.

2. `warmup_prompt_cache` — send a synthetic LLM call with `cache_control`
   breakpoints on the system prompt (and each preload tool-result block) so
   the shared prefix is committed to the provider's prompt cache before the
   first real turn (§5.4.2).

Both functions accept an `AgentRuntime` and mutate its internal state; they
do not return the runtime. Callers hold the runtime instance already.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..logger import HcagLogger
from ..models import CheckAndLoadRequest, Delta, LoadError
from ..runtime.agent import AgentRuntime
from ..runtime.llm import LLM, Message


@dataclass
class PreloadResult:
    loaded_ids: list[str] = field(default_factory=list)
    skipped_unknown: list[str] = field(default_factory=list)
    errors: list[LoadError] = field(default_factory=list)
    tokens_used: int = 0
    elapsed_ms: int = 0
    budget_exceeded: bool = False


@dataclass
class WarmupResult:
    ran: bool = False
    elapsed_ms: int = 0
    prompt_tokens: int | None = None
    cache_write_tokens: int | None = None


# --- Preload -------------------------------------------------------------


def preload_initial_packets(
    runtime: AgentRuntime,
    initial_packet_ids: list[str],
    logger: HcagLogger,
) -> PreloadResult:
    """Load the configured initial packet IDs into the active set (§5.4.1).

    - Bootstraps the runtime if it hasn't been already, so the catalog is in
      the system prompt before any tool-result blocks are appended.
    - For each ID: verify it exists in the catalog. Unknown IDs are logged
      as WARN and skipped (they should not brick the session).
    - Known IDs are batched into ONE `check_and_load_kb` call so the delta
      lands as a single tool-result block — this matches the shape of a real
      turn's classification-then-load, and keeps the cache prefix compact.
    - If the batched load fails with BudgetExceeded, the session cannot
      start; the result records `budget_exceeded=True` and callers must
      abort startup.
    """
    if runtime._system_prompt is None:  # noqa: SLF001
        runtime.bootstrap()

    result = PreloadResult()
    start = time.perf_counter()

    if not initial_packet_ids:
        logger.info("voice.startup.preload_done", loaded=[], tokens=0, elapsed_ms=0)
        return result

    catalog = runtime.memory.get_catalog()
    known_ids = catalog.ids()
    to_load: list[str] = []
    for pid in initial_packet_ids:
        if pid in known_ids:
            to_load.append(pid)
        else:
            result.skipped_unknown.append(pid)
            logger.warn("voice.startup.unknown_packet", packet_id=pid)

    if not to_load:
        result.elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "voice.startup.preload_done",
            loaded=[],
            skipped_unknown=result.skipped_unknown,
            tokens=0,
            elapsed_ms=result.elapsed_ms,
        )
        return result

    # Synthesize the same shape as an in-turn tool call so history stays
    # byte-identical to a "classify then load" turn. We fabricate a stable
    # tool_call_id — the LLM never sees this call, only the tool result.
    tool_call_id = "call_voice_preload"
    runtime._history.append(  # noqa: SLF001
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                _synth_tool_call(
                    tool_call_id,
                    "check_and_load_kb",
                    {
                        "context": "voice session preload",
                        "requested_packet_ids": to_load,
                        "active_packet_ids": [],
                    },
                ),
            ],
        )
    )

    request = CheckAndLoadRequest(
        context="voice session preload",
        requested_packet_ids=to_load,
        active_packet_ids=[],
    )
    delta: Delta = runtime.memory.check_and_load_kb(request)

    runtime._append_tool_result(  # noqa: SLF001
        tool_call_id,
        AgentRuntime._serialize_delta(delta),  # noqa: SLF001
    )

    result.loaded_ids = [p.id for p in delta.loaded]
    result.errors = list(delta.errors)
    result.budget_exceeded = any(e.reason == "BudgetExceeded" for e in delta.errors)
    result.tokens_used = runtime.memory.budget.sum_estimate(delta.active_after, catalog)
    result.elapsed_ms = int((time.perf_counter() - start) * 1000)

    logger.info(
        "voice.startup.preload_done",
        loaded=result.loaded_ids,
        skipped_unknown=result.skipped_unknown,
        errors=[{"id": e.packet_id, "reason": e.reason} for e in result.errors],
        tokens=result.tokens_used,
        elapsed_ms=result.elapsed_ms,
    )

    if result.budget_exceeded:
        logger.error(
            "voice.startup.budget_exceeded",
            requested=to_load,
            budget=runtime.memory.budget.max_active_tokens,
        )

    return result


def _synth_tool_call(call_id: str, name: str, args: dict[str, Any]):
    """Build a ToolCall with stable id for the preload synthesis path."""
    from ..runtime.llm import ToolCall
    return ToolCall(id=call_id, name=name, arguments=args)


# --- Warm-up call --------------------------------------------------------


def warmup_prompt_cache(
    runtime: AgentRuntime,
    logger: HcagLogger,
    *,
    enabled: bool = True,
    prompt: str = "Ready. Await user turn.",
    llm: LLM | None = None,
) -> WarmupResult:
    """Commit the shared prefix to the provider's prompt cache (§5.4.2).

    Must run AFTER `preload_initial_packets` so the prefix is byte-stable
    across every subsequent real turn.

    The synthetic user prompt and its response are NOT appended to history;
    the call exists only to make the provider write a cache entry. The
    response is discarded.
    """
    result = WarmupResult()

    if not enabled:
        logger.info("voice.warmup.skipped", reason="disabled")
        return result

    if runtime._system_prompt is None:  # noqa: SLF001
        runtime.bootstrap()

    llm = llm or runtime.llm

    # Build a throw-away message list from a COPY of history plus the stub user turn.
    # We do not mutate runtime._history — the warm-up call must be invisible to real turns.
    messages: list[Message] = list(runtime._history) + [Message(role="user", content=prompt)]  # noqa: SLF001

    start = time.perf_counter()
    response = None
    try:
        response = llm.chat(messages, tools=[])
    except Exception as e:  # noqa: BLE001
        result.elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.warn("voice.warmup.failed", error=str(e), elapsed_ms=result.elapsed_ms)
        return result

    result.ran = True
    result.elapsed_ms = int((time.perf_counter() - start) * 1000)

    # Best-effort extraction of provider-reported cache metrics.
    raw = getattr(response, "raw", None)
    usage = getattr(raw, "usage", None) if raw is not None else None
    if usage is not None:
        result.prompt_tokens = (
            getattr(usage, "prompt_tokens", None)
            or getattr(usage, "input_tokens", None)
        )
        result.cache_write_tokens = (
            getattr(usage, "cache_creation_input_tokens", None)
            or getattr(usage, "cache_write_input_tokens", None)
        )

    logger.info(
        "voice.warmup.done",
        elapsed_ms=result.elapsed_ms,
        prompt_tokens=result.prompt_tokens,
        cache_write_tokens=result.cache_write_tokens,
    )
    return result
