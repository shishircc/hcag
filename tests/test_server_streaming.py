"""`POST /chat/stream` — SSE framing and the 501 for non-streaming agents (§9.5)."""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from hcag.runtime.events import EventStream  # noqa: E402


class _StreamingAgent:
    def run_turn(self, user_message: str) -> str:  # noqa: ARG002
        return "hello"

    def run_turn_stream(self, user_message: str):  # noqa: ARG002
        s = EventStream(turn_id="t_1")
        yield s.emit("assistant.start")
        yield s.emit("assistant.delta", text="hel")
        yield s.emit("assistant.delta", text="lo")
        yield s.emit("assistant.final", text="hello", active_after=["billing"])


class _SyncOnlyAgent:
    """The RAG baseline's shape: no tool loop, so no stream (§9.5)."""

    def run_turn(self, user_message: str) -> str:  # noqa: ARG002
        return "hello"


class _ExplodingAgent:
    def run_turn(self, user_message: str) -> str:  # noqa: ARG002
        return ""

    def run_turn_stream(self, user_message: str):  # noqa: ARG002
        s = EventStream(turn_id="t_1")
        yield s.emit("assistant.delta", text="partial ")
        raise RuntimeError("provider died")


def _client(agent) -> TestClient:
    """Build the real app with a stub agent behind the session factory."""
    from unittest.mock import patch

    from hcag.server import app as server_app

    factory = ({"agent": "test"}, lambda session_id=None: agent)  # noqa: ARG005
    with patch.object(server_app, "_make_hcag_factory", return_value=factory):
        return TestClient(server_app.create_app(agent_type="hcag"))


def _events(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_stream_returns_sse_frames() -> None:
    r = _client(_StreamingAgent()).post("/chat/stream", json={"session_id": "s", "message": "hi"})

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    # A buffering proxy would turn the stream back into a slow sync response.
    assert r.headers["x-accel-buffering"] == "no"

    events = _events(r.text)
    assert [e["kind"] for e in events] == [
        "assistant.start", "assistant.delta", "assistant.delta", "assistant.final",
    ]
    assert "".join(e["text"] for e in events if e["kind"] == "assistant.delta") == "hello"
    assert events[-1]["active_after"] == ["billing"]


def test_sync_route_still_works_on_the_same_session() -> None:
    """`/chat` is unchanged — `eval` and the RAG comparison depend on it."""
    c = _client(_StreamingAgent())
    r = c.post("/chat", json={"session_id": "s", "message": "hi"})
    assert r.status_code == 200 and r.json()["text"] == "hello"


def test_non_streaming_agent_gets_501_not_a_fake_stream() -> None:
    r = _client(_SyncOnlyAgent()).post("/chat/stream", json={"session_id": "s", "message": "hi"})
    assert r.status_code == 501
    assert "POST /chat" in r.json()["detail"]


def test_failure_after_the_first_frame_is_an_in_band_error() -> None:
    """The 200 is already committed, so this cannot be a 500 (§2.14.3)."""
    r = _client(_ExplodingAgent()).post("/chat/stream", json={"session_id": "s", "message": "hi"})

    assert r.status_code == 200
    events = _events(r.text)
    assert events[0]["kind"] == "assistant.delta"
    assert events[-1]["kind"] == "error"
    assert "provider died" in events[-1]["detail"]
    assert not any(e["kind"] == "assistant.final" for e in events)
