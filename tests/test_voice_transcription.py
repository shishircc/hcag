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


# --- Streaming voice turn (§2.14, §5.7) ------------------------------------


def _session(runtime, sink):
    """A VoiceSession with the STT/TTS surfaces stubbed out."""
    import asyncio

    from hcag.logger import build_logger
    from hcag.config import LogConfig
    from hcag.voice.config import VoiceAgentConfig
    from hcag.voice.session import VoiceSession

    logger = build_logger(LogConfig(file_path="/tmp/hcag-voice-test.log"), name="test.voice")
    cfg = VoiceAgentConfig(kb_root="/tmp/kb")
    return VoiceSession(cfg, runtime, TranscriptionPublisher(sink=sink), logger)


def _kinds(sink) -> list[str]:
    return [json.loads(payload.decode())["kind"] for _topic, payload in sink.records]


def test_voice_turn_streams_deltas_and_publishes_tool_events() -> None:
    """Voice consumes `run_turn_stream` — the same iterator the HTTP route
    serves — so the two transports cannot drift in what a turn emits."""
    import asyncio

    from hcag.runtime.events import EventStream

    class _Runtime:
        def run_turn_stream(self, user_text):  # noqa: ARG002
            s = EventStream(turn_id="t_1")
            yield s.emit("tool.start", tool="check_and_load_kb", requested=["billing"])
            yield s.emit("tool.end", tool="check_and_load_kb", loaded=["billing"])
            yield s.emit("assistant.delta", text="Refunds ")
            yield s.emit("assistant.delta", text="settle in 5 days.")
            yield s.emit("assistant.final", text="Refunds settle in 5 days.")

    sink = CapturingSink()
    session = _session(_Runtime(), sink)
    asyncio.run(session.on_user_final("how long do refunds take?"))

    kinds = _kinds(sink)
    # The packet load is the longest silence in a voice turn, and now the one
    # the client can name rather than merely fill (§5.7).
    assert "tool.start" in kinds and "tool.end" in kinds
    assert kinds.count("assistant.delta") == 2
    assert kinds[-1] == "assistant.final"


def test_voice_turn_reports_a_stream_that_ends_early() -> None:
    """Never speak a truncated reply as if it were complete (§2.14.3)."""
    import asyncio

    from hcag.runtime.events import EventStream

    class _Runtime:
        def run_turn_stream(self, user_text):  # noqa: ARG002
            s = EventStream(turn_id="t_1")
            yield s.emit("assistant.delta", text="partial")

    sink = CapturingSink()
    session = _session(_Runtime(), sink)
    asyncio.run(session.on_user_final("q"))

    kinds = _kinds(sink)
    assert kinds[-1] == "system.error"
    assert "assistant.final" not in kinds
