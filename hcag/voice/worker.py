"""LiveKit worker entry point (§5.9 `serve`).

Joins the LiveKit dispatcher, and for every room-join event materializes:

- an `AgentRuntime` (with the shared `AgentConfig`),
- a `TranscriptionPublisher` bound to the room's local participant,
- STT and TTS adapters from `adapters.build_stt` / `build_tts`,
- a `VoiceSession` orchestrating the above.

Startup (§5.4) runs to completion — `preload_initial_packets` then
`warmup_prompt_cache` — before `VoiceSession.start()` publishes `system.ready`.

The livekit-agents SDK is imported inside `serve` so `--help` and `dry-run`
work without the plugin installed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..config import AgentConfig
from ..logger import HcagLogger, build_logger
from ..runtime.agent import AgentRuntime
from .adapters import build_stt, build_tts, describe
from .config import VoiceAgentConfig
from .session import VoiceSession
from .startup import preload_initial_packets, warmup_prompt_cache
from .transcription import TOPIC, TranscriptionPublisher


def build_agent_runtime(cfg: VoiceAgentConfig) -> AgentRuntime:
    """Construct the shared HCAG AgentRuntime from a voice config."""
    agent_cfg = AgentConfig(
        kb_root=cfg.kb_root,
        max_active_tokens=cfg.max_active_tokens,
        llm=cfg.llm,
        observability=cfg.observability,
        system_prompt_prefix=cfg.system_prompt_prefix,
    )
    return AgentRuntime(cfg=agent_cfg)


def prepare_runtime(cfg: VoiceAgentConfig, logger: HcagLogger) -> tuple[AgentRuntime, Any, Any]:
    """Bootstrap runtime, run preload + warmup, and return (runtime, preload, warmup).

    Raises RuntimeError on budget-exceeded preload. Called by both the
    `serve` worker and the `dry-run` CLI subcommand.
    """
    logger.info(
        "voice.startup.resolved",
        kb_root=cfg.kb_root,
        max_active_tokens=cfg.max_active_tokens,
        initial_packet_ids=cfg.initial_packet_ids,
        llm_provider=cfg.llm.provider,
        llm_model=cfg.llm.model,
        stt=describe(cfg.stt),
        tts=describe(cfg.tts),
        warmup_enabled=cfg.warmup.enabled,
    )
    runtime = build_agent_runtime(cfg)
    preload = preload_initial_packets(runtime, cfg.initial_packet_ids, logger)
    if preload.budget_exceeded:
        raise RuntimeError(
            "voice.startup.budget_exceeded — initial_packet_ids exceed max_active_tokens"
        )
    warmup = warmup_prompt_cache(
        runtime,
        logger,
        enabled=cfg.warmup.enabled,
        prompt=cfg.warmup.prompt,
    )
    return runtime, preload, warmup


class _LiveKitPublishSink:
    """Adapter from `TranscriptionPublisher._Sink` protocol to LiveKit's local
    participant `publish_data` API."""

    def __init__(self, room: Any) -> None:
        self._room = room

    async def publish(self, topic: str, payload: bytes) -> None:
        # LiveKit's Python SDK exposes `room.local_participant.publish_data(payload, topic=...)`
        # (or `publish_text` in newer versions). We prefer publish_data since it's
        # the lowest-common-denominator on the JS SDK side too.
        lp = self._room.local_participant
        method = getattr(lp, "publish_data", None) or getattr(lp, "publish_text", None)
        if method is None:
            raise RuntimeError("LiveKit local participant has no publish_data/publish_text method.")
        result = method(payload, topic=topic)
        if hasattr(result, "__await__"):
            await result


async def serve(cfg: VoiceAgentConfig, *, verbose: bool = False) -> None:
    """Run the LiveKit worker. Blocks until SIGTERM / SIGINT.

    This function imports `livekit.agents` lazily so environments without
    the plugin can still exercise the rest of the package.
    """
    try:
        from livekit import agents  # type: ignore
        from livekit.agents import JobContext, WorkerOptions, cli  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "livekit-agents is not installed. Install with `pip install hcag[voice]`."
        ) from e

    logger = build_logger(cfg.observability.log, name="hcag.voice", console=verbose)

    async def entrypoint(ctx: "JobContext") -> None:  # noqa: UP037
        await ctx.connect()
        publisher = TranscriptionPublisher(sink=_LiveKitPublishSink(ctx.room))
        try:
            runtime, _preload, _warmup = prepare_runtime(cfg, logger)
        except RuntimeError as e:
            await publisher.emit("system.error", reason=str(e))
            await ctx.shutdown(reason=str(e))
            return

        try:
            stt = build_stt(cfg.stt)
            tts = build_tts(cfg.tts)
        except Exception as e:  # noqa: BLE001
            logger.error("voice.adapters.failed", error=str(e))
            await publisher.emit("system.error", reason=str(e))
            await ctx.shutdown(reason=str(e))
            return

        session = VoiceSession(
            cfg=cfg,
            runtime=runtime,
            publisher=publisher,
            logger=logger,
            stt=stt,
            tts=tts,
        )
        await session.start()

        # The livekit-agents SDK exposes higher-level constructs (VoicePipelineAgent,
        # AgentSession, etc.) that wire STT/LLM/TTS events into a single loop. We
        # delegate to that here — the plugin runtime calls our session hooks as
        # events arrive.
        pipeline_cls = getattr(agents, "VoicePipelineAgent", None) or getattr(
            agents, "AgentSession", None
        )
        if pipeline_cls is None:
            raise RuntimeError(
                "This livekit-agents version does not expose VoicePipelineAgent / AgentSession."
            )
        pipeline = pipeline_cls(stt=stt, tts=tts, llm=_HcagLLMBridge(session), vad=None)
        pipeline.on("user_speech_partial", lambda ev: asyncio.create_task(session.on_user_partial(ev.text)))
        pipeline.on("user_speech_committed", lambda ev: asyncio.create_task(session.on_user_final(ev.text)))
        pipeline.start(ctx.room)

        # Block until the room closes.
        await ctx.wait_for_disconnect()
        await session.stop(reason="room_disconnected")

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


class _HcagLLMBridge:
    """Adapter that lets the livekit-agents pipeline call HCAG for LLM turns.

    We do NOT expose the raw `AgentRuntime` to the pipeline — the pipeline
    expects a streaming, provider-neutral LLM interface. This bridge routes
    `chat(messages)` into `session._drive_llm_turn`, which owns the HCAG
    active-set protocol and per-turn tool loop.
    """

    def __init__(self, session: VoiceSession) -> None:
        self._session = session

    async def chat(self, user_text: str) -> str:
        await self._session._drive_llm_turn(user_text)  # noqa: SLF001
        turn = (
            self._session.state.completed_turns[-1]
            if self._session.state.completed_turns
            else None
        )
        return turn.assistant_final if turn else ""
