"""Domain models — the classes shown in the §2.9 class diagram.

These are the DTOs and value objects that flow through the memory module and
across the LLM tool boundary. They are intentionally provider-agnostic; runtime
bindings translate them to whatever content-block format their SDK expects.
"""

from __future__ import annotations

import re
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


def _ids_from_text(raw: str) -> list[str]:
    """Recover packet ids from a single string that should have been a list.

    Handles the shapes a model actually emits when it stringifies the array
    argument: ``'["a.b"], '``, ``'"a.b"'``, ``'a.b, c.d'``. Packet ids never
    contain whitespace or commas, so splitting on those is safe; dots are the
    id separator and are left alone.
    """
    import json

    text = raw.strip().strip(",").strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [pid for item in parsed for pid in _ids_from_text(str(item))]
        if isinstance(parsed, str):
            return _ids_from_text(parsed)
        if isinstance(parsed, dict):
            # No sane reading of an object as a list of ids — drop it rather
            # than shred it into punctuation.
            return []
    out: list[str] = []
    for part in re.split(r"[,\s]+", text):
        pid = part.strip().strip("[]").strip("\"'").strip()
        if pid:
            out.append(pid)
    return out


def coerce_packet_ids(value: object) -> list[str]:
    """Normalize a tool-call argument into a de-duplicated list of packet ids.

    The schema says ``array of string``, but models sometimes send the array as
    JSON *text* instead (``'["www.example.a-b"],'``). Plain ``list()`` over that
    string yields one entry per character, and every character then comes back
    as ``unknown_packet_id`` while the packet the model actually wanted is never
    loaded. Coercing here keeps that model-side slip from becoming a failed
    retrieval.
    """
    if value is None:
        return []
    if isinstance(value, str):
        candidates = _ids_from_text(value)
    elif isinstance(value, (list, tuple, set)):
        candidates = [pid for item in value for pid in coerce_packet_ids(item)]
    else:
        candidates = _ids_from_text(str(value))

    seen: set[str] = set()
    ids: list[str] = []
    for pid in candidates:
        if pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


def is_well_formed_id_list(value: object) -> bool:
    """True when the raw argument already arrived as the schema promises."""
    return value is None or (
        isinstance(value, list) and all(isinstance(item, str) for item in value)
    )


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
