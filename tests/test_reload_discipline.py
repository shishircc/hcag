"""Redundant `check_and_load_kb` calls are named, not silently swallowed (§2.7.1)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from hcag.memory import FileSystemMemoryModule, LocalFsStorage, TokenBudget
from hcag.models import CheckAndLoadRequest
from hcag.runtime.agent import TOOL_DEFS, AgentRuntime
from hcag.config import AgentConfig
from hcag.runtime.llm import LLMResponse, Message, ToolCall


ROOT = """<!-- HCAG:COMPILED id=_root -->
---
id: ''
title: Root
short_description: KB root
long_description: KB root
token_size_estimate: 10
kind: node
source_files: []
children: [billing]
---

# Root

## Sub-topics

#### `billing`
- **path**: `billing/`
- **depth**: 1
- **parent**: `_root`
- **kind**: leaf
- **title**: Billing
- **short**: money movement
- **long**: money movement
- **tokens**: 50

#### `auth`
- **path**: `auth/`
- **depth**: 1
- **parent**: `_root`
- **kind**: leaf
- **title**: Auth
- **short**: sign-in
- **long**: sign-in
- **tokens**: 50
"""


def _leaf(pid: str, body: str) -> str:
    return (
        f"<!-- HCAG:COMPILED id={pid} -->\n---\n"
        f"id: {pid}\ntitle: {pid}\nshort_description: s\nlong_description: l\n"
        "token_size_estimate: 50\nkind: leaf\nsource_files: [x.md]\nchildren: []\n---\n\n"
        f"# {pid}\n\n## Content\n\n<!-- source: x.md -->\n{body}\n"
    )


def _kb(tmp_path: Path) -> Path:
    (tmp_path / "compiled.md").write_text(ROOT, encoding="utf-8")
    for pid, body in (("billing", "Refunds settle in 5 days."), ("auth", "SSO uses SAML.")):
        d = tmp_path / pid
        d.mkdir()
        (d / "compiled.md").write_text(_leaf(pid, body), encoding="utf-8")
    return tmp_path


def _module(root: Path) -> FileSystemMemoryModule:
    return FileSystemMemoryModule(storage=LocalFsStorage(root), budget=TokenBudget(10_000))


# --- Module behavior -------------------------------------------------------


def test_requesting_an_already_active_packet_is_reported_as_redundant(tmp_path: Path) -> None:
    module = _module(_kb(tmp_path))
    module.check_and_load_kb(
        CheckAndLoadRequest(context="need billing", requested_packet_ids=["billing"],
                            active_packet_ids=[])
    )
    # Same packet, second turn — the reflex call §2.7.1 exists to suppress.
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(context="refresh", requested_packet_ids=["billing"],
                            active_packet_ids=["billing"])
    )

    assert delta.redundant is True
    assert delta.loaded == []
    assert delta.evicted == []
    # D7: the agent stays authoritative — the call is named, not rejected.
    assert delta.active_after == ["billing"]
    assert delta.errors == []
    assert delta.note and "already active" in delta.note
    assert "billing" in delta.note


def test_a_call_that_adds_even_one_packet_is_not_redundant(tmp_path: Path) -> None:
    module = _module(_kb(tmp_path))
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="need both",
            requested_packet_ids=["billing", "auth"],
            active_packet_ids=["billing"],
        )
    )
    assert delta.redundant is False
    assert [p.id for p in delta.loaded] == ["auth"]
    assert delta.note is None


def test_redundant_call_does_not_disturb_lru_order(tmp_path: Path) -> None:
    """A reflex call must not move a packet to the most-recently-used tail —
    that would change which packet gets evicted next (§2.7.1)."""
    module = _module(_kb(tmp_path))
    module.check_and_load_kb(
        CheckAndLoadRequest(context="a", requested_packet_ids=["billing", "auth"],
                            active_packet_ids=[])
    )
    before = ["billing", "auth"]
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(context="re-ask for billing", requested_packet_ids=["billing"],
                            active_packet_ids=before)
    )
    assert delta.active_after == before  # billing NOT promoted to the tail


def test_empty_request_is_not_treated_as_redundant(tmp_path: Path) -> None:
    """Requesting nothing is a different (also useless) call; don't mislabel it."""
    delta = _module(_kb(tmp_path)).check_and_load_kb(
        CheckAndLoadRequest(context="", requested_packet_ids=[], active_packet_ids=["billing"])
    )
    assert delta.redundant is False


# --- Tool + prompt wording (§2.7.1 enforcement layers 1 and 2) --------------


def test_tool_description_leads_with_the_negative_case() -> None:
    desc = next(
        t["function"]["description"]
        for t in TOOL_DEFS
        if t["function"]["name"] == "check_and_load_kb"
    )
    assert desc.lstrip().startswith("MOST TURNS NEED NO CALL")
    assert "already active is an error" in desc


def test_system_prompt_carries_the_decision_rule() -> None:
    prompt = AgentConfig(kb_root="/tmp/x").system_prompt_prefix
    assert "WHEN TO LOAD" in prompt
    assert "Most turns need NO tool call" in prompt
    assert "never needs re-requesting" in prompt
    # The chat UI renders Markdown (§10.3), so the model is told to use it.
    assert "Markdown" in prompt


# --- Runtime: the note reaches the model, and the rate is logged -----------


class _ScriptedLLM:
    """Calls the tool redundantly on turn 1, then answers on turn 2."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[Message], tools=None):  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="check_and_load_kb",
                        arguments={
                            "context": "refresh",
                            "requested_packet_ids": ["billing"],
                            "active_packet_ids": ["billing"],
                        },
                    )
                ],
            )
        return LLMResponse(text="Refunds settle in 5 days.", tool_calls=[])


class _Capture(logging.Handler):
    """Collect emitted records.

    `build_logger` attaches its file handler once per logger *name* and reuses
    it, so a log-file assertion only holds when the test runs first. Capture
    the records instead.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_runtime_surfaces_the_note_and_counts_the_call(tmp_path: Path) -> None:
    root = _kb(tmp_path)
    cfg = AgentConfig(kb_root=str(root))
    cfg.observability.log.file_path = str(tmp_path / "agent.log")
    llm = _ScriptedLLM()
    runtime = AgentRuntime(cfg=cfg, llm=llm)

    cap = _Capture()
    runtime_logger = logging.getLogger("hcag.runtime")
    runtime_logger.addHandler(cap)
    try:
        answer = runtime.run_turn("what about refunds")
    finally:
        runtime_logger.removeHandler(cap)
    assert answer == "Refunds settle in 5 days."

    # The model saw, in-conversation, that the call bought it nothing.
    tool_msgs = [m for m in runtime._history if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "NOTE:" in tool_msgs[0].content
    assert "already active" in tool_msgs[0].content
    # ...and no packet content was re-transmitted (D6).
    meta = json.loads(tool_msgs[0].content.split("DELTA-METADATA: ")[1].splitlines()[0])
    assert meta["loaded_ids"] == []

    assert runtime._reload_calls == 1
    assert runtime._redundant_reloads == 1

    # The module named the call redundant, and the turn reported the rate.
    assert any(getattr(r, "event", None) == "check_and_load_kb.redundant" for r in cap.records)
    ends = [r for r in cap.records if getattr(r, "event", None) == "turn.end"]
    assert ends and ends[-1].redundant_rate == 1.0
