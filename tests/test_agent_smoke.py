"""Smoke test the AgentRuntime end-to-end with a FakeLLM (no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hcag.config import AgentConfig
from hcag.runtime import AgentRuntime
from hcag.runtime.llm import LLM, LLMResponse, Message, ToolCall


CATALOG = """<!-- HCAG:ROOT_CATALOG -->

# Knowledge Catalog

## Packets

### `billing.refunds`
- **path**: `billing/refunds/`
- **breadcrumb**: billing → refunds
- **title**: Refund Processing
- **short**: How refunds are issued.
- **long**: Full lifecycle of refund processing.
- **tokens**: 100
"""


class FakeLLM:
    """Deterministic script: first call issues check_and_load_kb, second returns final text."""

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []
        self.step = 0

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls.append(messages)
        self.step += 1
        if self.step == 1:
            return LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="tc-1",
                        name="check_and_load_kb",
                        arguments={
                            "context": "need refunds",
                            "requested_packet_ids": ["billing.refunds"],
                            "active_packet_ids": [],
                        },
                    )
                ],
            )
        return LLMResponse(text="Refunds work as described.", tool_calls=[])


def _setup_kb(tmp_path: Path) -> Path:
    (tmp_path / "catalog.md").write_text(CATALOG, encoding="utf-8")
    refunds = tmp_path / "billing" / "refunds"
    refunds.mkdir(parents=True)
    (refunds / "packet.md").write_text("# Refunds\n\nRefunds work like this.\n", encoding="utf-8")
    return tmp_path


def test_agent_bootstrap_and_turn(tmp_path: Path) -> None:
    root = _setup_kb(tmp_path)
    cfg = AgentConfig(kb_root=str(root), max_active_tokens=1000)
    cfg.observability.log.file_path = str(tmp_path / "hcag.log")
    fake = FakeLLM()
    agent = AgentRuntime(cfg=cfg, llm=fake)

    reply = agent.run_turn("How do refunds work?")

    assert "Refunds" in reply
    assert fake.step == 2
    # First LLM call must include the catalog in the system message
    first_call_system = fake.calls[0][0]
    assert first_call_system.role == "system"
    assert "billing.refunds" in first_call_system.content
