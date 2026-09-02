"""Turn events — the vocabulary of a streaming turn (§2.14.1).

Deliberately the same schema the voice transcription channel publishes
(§5.7): one event model, two transports. A LiveKit data channel carries it
for voice, SSE for chat, and the client renders both from one reducer.

An HCAG turn does a tool round trip before its first token, so tool activity
is a first-class event rather than an implementation detail — which packets a
turn chose is the most interesting thing about it, for a user watching and for
anyone debugging a wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EventKind = Literal[
    "assistant.start",
    "assistant.delta",
    "assistant.final",
    "tool.start",
    "tool.end",
    "error",
]


@dataclass
class Event:
    kind: EventKind
    turn_id: str
    seq: int = 0
    #: kind-specific payload — `text`, `tool`, `requested`, `loaded`, `detail`…
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "kind": self.kind, "turn_id": self.turn_id, **self.data}


class EventStream:
    """Assigns the monotonic `seq` every event carries (§2.14.1)."""

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self._seq = 0

    def emit(self, kind: EventKind, **data: Any) -> Event:
        self._seq += 1
        return Event(kind=kind, turn_id=self.turn_id, seq=self._seq, data=data)
