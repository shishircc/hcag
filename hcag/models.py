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
    """Metadata for one folder, as it appears in a `## Sub-topics` index (§2.2).

    Since catalogs roll up the whole subtree (D3a), the root catalog yields one
    of these per folder in the KB — `depth`/`parent` reconstruct the tree, and
    `kind` tells the agent whether the entry holds content or is a pure
    taxonomy waypoint.
    """

    id: str
    path: str
    title: str
    short_description: str
    long_description: str
    token_size_estimate: int
    depth: int = 1
    parent: str = ""
    kind: str = "leaf"
    #: `## Content` + images only. `None` for KBs built before the split, where
    #: the total is the only figure available.
    content_token_estimate: int | None = None

    @property
    def budget_tokens(self) -> int:
        """The figure the active-set budget is enforced against (§2.5).

        The `## Sub-topics` section is elided when a non-root packet is served
        (§2.6), so it never occupies budget.
        """
        if self.content_token_estimate is None:
            return self.token_size_estimate
        return self.content_token_estimate


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

    def children_of(self, packet_id: str) -> list[CatalogEntry]:
        """Immediate children of `packet_id` — the tree is reconstructible from
        the flat index because every entry names its `parent` (§2.2)."""
        return [e for e in self.entries if e.parent == packet_id]

    def root_children(self) -> list[CatalogEntry]:
        """The top-level branches.

        Prefer this over `children_of(<root id>)`: the root is named `_root` in
        `parent` fields whether or not the KB configured a non-empty
        `[compiled] root_id`, so matching on depth avoids caring which.
        """
        return [e for e in self.entries if e.depth == 1]


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

    redundant: bool = False
    """True when every requested id was already active — the call bought nothing.

    Surfaced rather than silently swallowed (§2.7.1): `note` tells the model so
    in-conversation, and the runtime counts it toward `reload.redundant_rate`.
    """

    note: str | None = None
    """One line for the model explaining an otherwise-empty delta."""


@dataclass
class EvictionPlan:
    """Result of an eviction pass. Consumed by the memory module."""

    ordered_active_after: list[str]
    evicted: list[str]
    to_load: list[str]
    error: LoadError | None = None
