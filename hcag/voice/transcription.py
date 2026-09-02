"""Transcription channel — the `hcag.transcription` data channel (§5.7).

Wire format is JSON, one message per event, with a monotonically increasing
`seq`. The `kind` field is namespaced (`user.*`, `assistant.*`, `system.*`)
so the web client can route messages to the correct UI slot without parsing
free text.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

TOPIC = "hcag.transcription"


TranscriptionKind = Literal[
    "user.partial",
    "user.final",
    "assistant.delta",
    "assistant.final",
    "assistant.interrupted",
    # Tool activity, shared with the HTTP stream (§2.14.1). The packet load is
    # the longest silence in a voice turn and the one the client can name.
    "tool.start",
    "tool.end",
    "system.ready",
    "system.error",
]


@dataclass
class TranscriptionMessage:
    seq: int
    kind: TranscriptionKind
    turn_id: str | None = None
    text: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"seq": self.seq, "kind": self.kind}
        if self.turn_id is not None:
            d["turn_id"] = self.turn_id
        if self.text is not None:
            d["text"] = self.text
        d.update(self.extras)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class _Sink(Protocol):
    async def publish(self, topic: str, payload: bytes) -> None: ...


class TranscriptionPublisher:
    """Serializes and publishes transcription events on the `hcag.transcription` topic.

    The sink is any awaitable object with a `publish(topic, payload)` method —
    in production this is a LiveKit local-participant text/data publisher; in
    tests it's a `CapturingSink` that records messages in memory.
    """

    def __init__(self, sink: _Sink | None = None) -> None:
        self._sink = sink
        self._seq = 0
        self.emitted: list[TranscriptionMessage] = []

    def bind(self, sink: _Sink) -> None:
        """Attach a sink after construction.

        The publisher can be created before the LiveKit room exists (e.g., in
        tests, or during warm-up) and bound to a real sink when the room joins.
        """
        self._sink = sink

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _build(
        self,
        kind: TranscriptionKind,
        *,
        turn_id: str | None = None,
        text: str | None = None,
        **extras: Any,
    ) -> TranscriptionMessage:
        return TranscriptionMessage(
            seq=self._next_seq(),
            kind=kind,
            turn_id=turn_id,
            text=text,
            extras=extras,
        )

    async def emit(
        self,
        kind: TranscriptionKind,
        *,
        turn_id: str | None = None,
        text: str | None = None,
        **extras: Any,
    ) -> TranscriptionMessage:
        msg = self._build(kind, turn_id=turn_id, text=text, **extras)
        self.emitted.append(msg)
        if self._sink is not None:
            await self._sink.publish(TOPIC, msg.to_json().encode("utf-8"))
        return msg


def make_message(
    seq: int,
    kind: TranscriptionKind,
    *,
    turn_id: str | None = None,
    text: str | None = None,
    **extras: Any,
) -> TranscriptionMessage:
    """Build a TranscriptionMessage — handy for tests and doc examples."""
    return TranscriptionMessage(seq=seq, kind=kind, turn_id=turn_id, text=text, extras=extras)


class CapturingSink:
    """In-memory sink for tests. Records raw published payloads."""

    def __init__(self) -> None:
        self.records: list[tuple[str, bytes]] = []

    async def publish(self, topic: str, payload: bytes) -> None:
        self.records.append((topic, payload))

    def decoded(self) -> list[dict[str, Any]]:
        return [json.loads(p.decode("utf-8")) for _, p in self.records]
