"""Voice startup phases — preload and prompt-cache warm-up (§5.4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hcag.config import AgentConfig
from hcag.logger import build_logger, LogConfig
from hcag.runtime.agent import AgentRuntime
from hcag.runtime.llm import LLMResponse, Message
from hcag.voice.startup import preload_initial_packets, warmup_prompt_cache


CATALOG = """<!-- HCAG:ROOT_CATALOG -->

# Knowledge Catalog

## Packets

### `billing.refunds`
- **path**: `billing/refunds/`
- **title**: Refund Processing
- **short**: How refunds are issued.
- **long**: Full lifecycle.
- **tokens**: 100

### `billing.invoices`
- **path**: `billing/invoices/`
- **title**: Invoices
- **short**: How invoices work.
- **long**: Invoice generation.
- **tokens**: 100
"""


def _setup_kb(tmp_path: Path) -> Path:
    (tmp_path / "catalog.md").write_text(CATALOG, encoding="utf-8")
    (tmp_path / "billing" / "refunds").mkdir(parents=True)
    (tmp_path / "billing" / "refunds" / "packet.md").write_text(
        "# Refunds\n\nDetails.\n", encoding="utf-8"
    )
    (tmp_path / "billing" / "invoices").mkdir(parents=True)
    (tmp_path / "billing" / "invoices" / "packet.md").write_text(
        "# Invoices\n\nDetails.\n", encoding="utf-8"
    )
    return tmp_path


class ScriptedLLM:
    def __init__(self, response_text: str = "ok") -> None:
        self.calls: list[list[Message]] = []
        self.response_text = response_text

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls.append(list(messages))
        return LLMResponse(text=self.response_text, tool_calls=[])


def _runtime(tmp_path: Path, max_tokens: int = 1000) -> AgentRuntime:
    root = _setup_kb(tmp_path)
    cfg = AgentConfig(kb_root=str(root), max_active_tokens=max_tokens)
    cfg.observability.log.file_path = str(tmp_path / "voice.log")
    return AgentRuntime(cfg=cfg, llm=ScriptedLLM())


def _logger(tmp_path: Path):
    return build_logger(LogConfig(file_path=str(tmp_path / "voice.log"), level="DEBUG"), name=f"v{tmp_path.name}")


def test_preload_loads_configured_packets(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    result = preload_initial_packets(runtime, ["billing.refunds", "billing.invoices"], _logger(tmp_path))
    assert set(result.loaded_ids) == {"billing.refunds", "billing.invoices"}
    assert result.budget_exceeded is False
    assert result.tokens_used == 200  # 100 + 100 from catalog


def test_preload_unknown_id_is_warned_and_skipped(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    result = preload_initial_packets(runtime, ["billing.refunds", "no.such.id"], _logger(tmp_path))
    assert result.loaded_ids == ["billing.refunds"]
    assert result.skipped_unknown == ["no.such.id"]


def test_preload_empty_list_is_a_no_op(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    result = preload_initial_packets(runtime, [], _logger(tmp_path))
    assert result.loaded_ids == []
    assert result.budget_exceeded is False
    # History has only the system message; no synthetic tool_call was appended.
    assert len(runtime._history) == 1


def test_preload_appends_tool_result_to_history(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    preload_initial_packets(runtime, ["billing.refunds"], _logger(tmp_path))
    # Expected history: [system, assistant(tool_call), tool(result)]
    roles = [m.role for m in runtime._history]
    assert roles == ["system", "assistant", "tool"]
    # The tool message content includes the delta metadata
    tool_msg = runtime._history[-1]
    content = tool_msg.content if isinstance(tool_msg.content, str) else ""
    assert "DELTA-METADATA" in content
    assert "billing.refunds" in content


def test_preload_budget_exceeded_flags_result(tmp_path: Path) -> None:
    # Budget = 50 tokens; each packet is 100 → BudgetExceeded.
    runtime = _runtime(tmp_path, max_tokens=50)
    result = preload_initial_packets(runtime, ["billing.refunds"], _logger(tmp_path))
    assert result.budget_exceeded is True


def test_warmup_runs_and_does_not_leak_into_history(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    preload_initial_packets(runtime, ["billing.refunds"], _logger(tmp_path))
    before = list(runtime._history)
    result = warmup_prompt_cache(runtime, _logger(tmp_path), enabled=True, prompt="ready")
    assert result.ran is True
    assert result.elapsed_ms >= 0
    # History unchanged by the warm-up call.
    assert runtime._history == before


def test_warmup_disabled_short_circuits(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    result = warmup_prompt_cache(runtime, _logger(tmp_path), enabled=False)
    assert result.ran is False


def test_warmup_ordering_sees_preloaded_history(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    llm = ScriptedLLM()
    runtime.llm = llm
    preload_initial_packets(runtime, ["billing.refunds"], _logger(tmp_path))
    warmup_prompt_cache(runtime, _logger(tmp_path), enabled=True, prompt="ready")
    # The warm-up call must include the preloaded tool-result in its message list;
    # this is what makes the cache write cover the same prefix as real turns.
    warmup_messages = llm.calls[-1]
    roles = [m.role for m in warmup_messages]
    assert roles == ["system", "assistant", "tool", "user"]
    assert warmup_messages[-1].content == "ready"
