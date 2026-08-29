"""Transcription publisher — schema, seq monotonicity, sink round-trip (§5.7)."""

from __future__ import annotations

import json

import pytest

from hcag.voice.transcription import (
    TOPIC,
    CapturingSink,
    TranscriptionPublisher,
    make_message,
)


async def test_seq_is_monotonic_across_kinds() -> None:
    pub = TranscriptionPublisher(sink=CapturingSink())
    m1 = await pub.emit("user.partial", turn_id="t1", text="hel")
    m2 = await pub.emit("user.final", turn_id="t1", text="hello")
    m3 = await pub.emit("assistant.delta", turn_id="t1", text="hi ")
    assert (m1.seq, m2.seq, m3.seq) == (1, 2, 3)


async def test_payload_shape_matches_spec() -> None:
    sink = CapturingSink()
    pub = TranscriptionPublisher(sink=sink)
    await pub.emit("user.final", turn_id="t_9", text="how do partial refunds work")
    assert len(sink.records) == 1
    topic, payload = sink.records[0]
    assert topic == TOPIC
    d = json.loads(payload)
    assert d == {
        "seq": 1,
        "kind": "user.final",
        "turn_id": "t_9",
        "text": "how do partial refunds work",
    }


async def test_system_ready_has_no_turn_id_or_text() -> None:
    sink = CapturingSink()
    pub = TranscriptionPublisher(sink=sink)
    await pub.emit("system.ready", session_id="abc")
    d = sink.decoded()[0]
    assert d["kind"] == "system.ready"
    assert "turn_id" not in d
    assert "text" not in d
    assert d["session_id"] == "abc"


async def test_assistant_interrupted_carries_turn_id_only() -> None:
    sink = CapturingSink()
    pub = TranscriptionPublisher(sink=sink)
    await pub.emit("assistant.interrupted", turn_id="t_42")
    d = sink.decoded()[0]
    assert d == {"seq": 1, "kind": "assistant.interrupted", "turn_id": "t_42"}


async def test_bind_after_construction() -> None:
    pub = TranscriptionPublisher()  # no sink
    await pub.emit("system.ready")  # tolerated
    assert len(pub.emitted) == 1
    sink = CapturingSink()
    pub.bind(sink)
    await pub.emit("user.partial", text="hi")
    # Only messages after bind reach the sink
    assert len(sink.records) == 1


def test_make_message_matches_dataclass_shape() -> None:
    m = make_message(7, "assistant.delta", turn_id="t_1", text="hi", latency_ms=42)
    d = m.to_dict()
    assert d["seq"] == 7
    assert d["kind"] == "assistant.delta"
    assert d["turn_id"] == "t_1"
    assert d["text"] == "hi"
    assert d["latency_ms"] == 42
