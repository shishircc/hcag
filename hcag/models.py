"""Domain models — the classes shown in the §2.9 class diagram.

These are the DTOs and value objects that flow through the memory module and
across the LLM tool boundary. They are intentionally provider-agnostic; runtime
bindings translate them to whatever content-block format their SDK expects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class BlockKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"


@dataclass
class TextBlock:
    text: str
    kind: Literal[BlockKind.TEXT] = BlockKind.TEXT


@dataclass
class ImageBlock:
    data: bytes
    mime_type: str
    filename: str
    kind: Literal[BlockKind.IMAGE] = BlockKind.IMAGE


ContentBlock = TextBlock | ImageBlock


@dataclass
class CatalogEntry:
    id: str
    path: str
    title: str
    short_description: str
    long_description: str
    token_size_estimate: int


@dataclass
class Catalog:
    entries: list[CatalogEntry]
    raw_markdown: str

    def get(self, packet_id: str) -> CatalogEntry | None:
        for e in self.entries:
            if e.id == packet_id:
                return e
        return None

    def ids(self) -> set[str]:
        return {e.id for e in self.entries}


@dataclass
class Packet:
    id: str
    title: str
    content: list[ContentBlock]


@dataclass
class LoadError:
    packet_id: str
    reason: str


@dataclass
class CheckAndLoadRequest:
    context: str
    requested_packet_ids: list[str]
    active_packet_ids: list[str]


@dataclass
class Delta:
    loaded: list[Packet]
    evicted: list[str]
    active_after: list[str]
    errors: list[LoadError] = field(default_factory=list)


@dataclass
class EvictionPlan:
    """Result of an eviction pass. Consumed by the memory module."""

    ordered_active_after: list[str]
    evicted: list[str]
    to_load: list[str]
    error: LoadError | None = None
