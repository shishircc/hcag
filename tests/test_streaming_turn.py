"""Turn API — streaming primitive, synchronous derived (§2.14)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hcag.config import AgentConfig
from hcag.runtime.agent import AgentRuntime
from hcag.runtime.events import EventStream
from hcag.runtime.llm import (
    Final,
    LLMResponse,
    TextDelta,
    ToolCall,
    stream_or_buffer,
)

ROOT = """<!-- HCAG:COMPILED id=_root -->
---
id: ''
title: R
short_description: r
long_description: r
token_size_estimate: 10
kind: node
source_files: []
children: [billing]
---

# R

## Sub-topics

#### `billing`
- **path**: `billing/`
- **depth**: 1
- **parent**: `_root`
- **kind**: leaf
- **title**: Billing
- **short**: money
- **long**: money
- **tokens**: 50
"""

LEAF = """<!-- HCAG:COMPILED id=billing -->
---
id: billing
title: Billing
short_description: money
long_description: money
token_size_estimate: 50
kind: leaf
source_files: [x.md]
children: []
---

# Billing

## Content

<!-- source: x.md -->
Refunds settle in 5 business days.
"""

ANSWER = "Refunds settle in 5 business days."


class _StreamingLLM:
    """Calls the tool, then streams the answer in three pieces."""

    def chat_stream(self, messages, tools=None):  # noqa: ARG002
        if not any(getattr(m, "role", "") == "tool" for m in messages):
            yield Final(
                LLMResponse(
                    text="",
                    model="m",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="check_and_load_kb",
                            arguments={
                                "context": "refund timing",
                                "requested_packet_ids": ["billing"],
                                "active_packet_ids": [],
                            },
                        )
                    ],
                )
            )
            return
        for piece in ["Refunds ", "settle in ", "5 business days."]:
            yield TextDelta(piece)
        yield Final(LLMResponse(text=ANSWER, tool_calls=[], model="m"))


class _NonStreamingLLM:
    """A binding with no `chat_stream` at all."""

    def chat(self, messages, tools=None):  # noqa: ARG002
        return LLMResponse(text=ANSWER, tool_calls=[], model="m")


@pytest.fixture
def runtime(tmp_path: Path):
    def _make(llm):
        (tmp_path / "compiled.md").write_text(ROOT, encoding="utf-8")
        leaf = tmp_path / "billing"
        leaf.mkdir(exist_ok=True)
        (leaf / "compiled.md").write_text(LEAF, encoding="utf-8")
        cfg = AgentConfig(kb_root=str(tmp_path))
        cfg.observability.log.file_path = str(tmp_path / "a.log")
        return AgentRuntime(cfg=cfg, llm=llm, session_id="s1")

    return _make


# --- Event vocabulary ------------------------------------------------------


def test_stream_emits_the_documented_event_sequence(runtime) -> None:
    events = list(runtime(_StreamingLLM()).run_turn_stream("how long do refunds take?"))
    assert [e.kind for e in events] == [
        "assistant.start",
        "tool.start",
        "tool.end",
        "assistant.delta",
        "assistant.delta",
        "assistant.delta",
        "assistant.final",
    ]
    # seq is monotonic from 1, and every event carries its turn_id.
    assert [e.seq for e in events] == list(range(1, 8))
    assert {e.turn_id for e in events} == {"t_1"}


def test_tool_events_name_the_packets(runtime) -> None:
    """Which packets a turn chose is the point of streaming tool activity —
    a client can say 'consulting Billing…' rather than showing a spinner."""
    events = list(runtime(_StreamingLLM()).run_turn_stream("q"))
    start = next(e for e in events if e.kind == "tool.start")
    end = next(e for e in events if e.kind == "tool.end")

    assert start.data["tool"] == "check_and_load_kb"
    assert start.data["requested"] == ["billing"]
    assert start.data["context"] == "refund timing"
    assert end.data["loaded"] == ["billing"]
    assert end.data["active_after"] == ["billing"]
    assert end.data["redundant"] is False


def test_final_carries_the_whole_answer_and_the_active_set(runtime) -> None:
    events = list(runtime(_StreamingLLM()).run_turn_stream("q"))
    final = events[-1]
    assert final.data["text"] == ANSWER
    assert final.data["active_after"] == ["billing"]
    # Deltas concatenate to the same thing — a client may use either.
    deltas = "".join(e.data["text"] for e in events if e.kind == "assistant.delta")
    assert deltas == ANSWER


# --- Synchronous is derived ------------------------------------------------


def test_run_turn_returns_the_same_answer(runtime) -> None:
    assert runtime(_StreamingLLM()).run_turn("q") == ANSWER


def test_both_paths_produce_identical_history(runtime) -> None:
    """One turn implementation, so the two cannot grow different behaviour
    around tool loops, eviction, or history (§2.14.2) — and §2.12's cache
    alignment holds whichever path a turn took."""
    a = runtime(_StreamingLLM())
    list(a.run_turn_stream("q"))
    b = runtime(_StreamingLLM())
    b.run_turn("q")

    def shape(rt):
        return [(m.role, m.content, [t.name for t in m.tool_calls]) for m in rt._history]

    assert shape(a) == shape(b)


def test_a_non_streaming_binding_still_streams(runtime) -> None:
    """§2.14: a provider that cannot stream surfaces its answer as one delta,
    so the contract holds everywhere rather than being a caller's problem."""
    events = list(runtime(_NonStreamingLLM()).run_turn_stream("q"))
    assert [e.kind for e in events] == ["assistant.start", "assistant.delta", "assistant.final"]
    assert events[1].data["text"] == ANSWER


def test_stream_or_buffer_passes_a_streamer_through() -> None:
    kinds = [type(c).__name__ for c in stream_or_buffer(_StreamingLLM(), [], [])]
    assert kinds == ["Final"]


# --- Errors after the first byte (§2.14.3) ---------------------------------


class _ExplodingLLM:
    def chat_stream(self, messages, tools=None):  # noqa: ARG002
        yield TextDelta("partial ")
        raise RuntimeError("provider died")


def test_failure_mid_stream_is_an_in_band_error_event(runtime) -> None:
    """Past the first event the HTTP status is already sent, so a failure
    cannot become a 500 — it has to travel in the stream."""
    events = list(runtime(_ExplodingLLM()).run_turn_stream("q"))

    assert events[-1].kind == "error"
    assert "provider died" in events[-1].data["detail"]
    # And no assistant.final: a stream that ends without one is a failed turn.
    assert not any(e.kind == "assistant.final" for e in events)


def test_run_turn_raises_on_a_failed_stream(runtime) -> None:
    """The synchronous path has no way to express a partial answer, so an
    in-band error becomes an exception rather than a truncated string."""
    with pytest.raises(RuntimeError, match="provider died"):
        runtime(_ExplodingLLM()).run_turn("q")


# --- Event serialization ---------------------------------------------------


def test_event_dict_shape_matches_the_wire_format() -> None:
    stream = EventStream(turn_id="t_9")
    event = stream.emit("assistant.delta", text="hi")
    assert event.to_dict() == {"seq": 1, "kind": "assistant.delta", "turn_id": "t_9", "text": "hi"}
    json.dumps(event.to_dict())  # must be serializable as-is
