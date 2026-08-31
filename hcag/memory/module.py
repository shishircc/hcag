"""Memory module — per-call stateless, sole KB accessor (§2, D4a, D7).

Bootstrap reads the root ``compiled.md`` and returns its ``## Sub-topics``
section as the catalog injected into the system prompt — the top-level
branches of the KB (§2.7). Deeper folders are resolved lazily on
``check_and_load_kb`` by reading their own ``compiled.md`` front-matter;
loading a taxonomy node also indexes that node's ``## Sub-topics`` so the
agent's next drill-down finds the metadata it needs for budget-checking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..compiled_io import parse_subtopics
from ..logger import HcagLogger
from ..models import (
    Catalog,
    CatalogEntry,
    CheckAndLoadRequest,
    Delta,
    LoadError,
    Packet,
)
from .eviction import EvictionPolicy, LRUEvictionPolicy, TokenBudget
from .packet_loader import assemble_packet
from .storage import KBStorage


class MemoryModule(Protocol):
    def get_catalog(self) -> Catalog: ...
    def check_and_load_kb(self, request: CheckAndLoadRequest) -> Delta: ...


# --- Helpers ---------------------------------------------------------------


def _id_to_relpath(packet_id: str) -> str:
    """Dotted packet ID to POSIX-relative KB path (§3.4.5)."""
    return packet_id.replace(".", "/")


def _child_entry_to_catalog_entry(child, parent_relpath: str) -> CatalogEntry:
    """Absolute-in-KB path stitched together from parent's path + child's path."""
    child_path = child.path.strip("/")
    if parent_relpath and child_path:
        combined = f"{parent_relpath}/{child_path}"
    else:
        combined = parent_relpath or child_path
    return CatalogEntry(
        id=child.id,
        path=combined.strip("/"),
        title=child.title,
        short_description=child.short,
        long_description=child.long,
        token_size_estimate=child.tokens,
    )


def _render_subtopics_as_catalog(entries: list[CatalogEntry]) -> str:
    """Render top-level entries as the ``## Packets`` block the agent sees.

    Matches the shape §2.2 documents so the LLM's parser expectations don't
    depend on whether the source was a freshly-generated root or a cached one.
    """
    lines: list[str] = ["# Knowledge Catalog", "", "## Packets", ""]
    for e in entries:
        lines.append(f"### `{e.id}`")
        lines.append(f"- **path**: `{e.path}/`")
        lines.append(f"- **title**: {e.title}")
        lines.append(f"- **short**: {e.short_description}")
        lines.append(f"- **long**: {e.long_description}")
        lines.append(f"- **tokens**: {e.token_size_estimate}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- FileSystemMemoryModule -------------------------------------------------


@dataclass
class _ResolvedFolder:
    """Cache entry for a folder we've already touched — its metadata + path."""

    entry: CatalogEntry
    path: str  # KB-relative POSIX


class FileSystemMemoryModule:
    def __init__(
        self,
        storage: KBStorage,
        budget: TokenBudget,
        eviction: EvictionPolicy | None = None,
        logger: HcagLogger | None = None,
        tracer=None,
    ) -> None:
        self.storage = storage
        self.budget = budget
        self.eviction = eviction or LRUEvictionPolicy()
        self.logger = logger
        self.tracer = tracer
        self._catalog: Catalog | None = None
        # Grows lazily as the agent drills into deeper folders.
        self._index: dict[str, _ResolvedFolder] = {}

    # ---- get_catalog -----------------------------------------------------

    def get_catalog(self) -> Catalog:
        if self._catalog is None:
            raw = self.storage.read_compiled("")  # root
            children = parse_subtopics(raw)
            entries = [_child_entry_to_catalog_entry(c, parent_relpath="") for c in children]
            for e in entries:
                self._index[e.id] = _ResolvedFolder(entry=e, path=e.path)
            rendered = _render_subtopics_as_catalog(entries)
            self._catalog = Catalog(entries=entries, raw_markdown=rendered)
            if self.logger:
                self.logger.info(
                    "catalog.loaded",
                    top_level_entries=len(entries),
                    bytes=len(raw),
                )
        return self._catalog

    # ---- Lazy packet-index population -----------------------------------

    def _resolve(self, packet_id: str) -> CatalogEntry | None:
        """Return the metadata for ``packet_id``, reading its ``compiled.md``
        front-matter on first touch. On any I/O failure, returns None (caller
        turns it into a ``LoadError``).
        """
        hit = self._index.get(packet_id)
        if hit is not None:
            return hit.entry
        relpath = _id_to_relpath(packet_id)
        if not self.storage.has_compiled(relpath):
            return None
        try:
            raw = self.storage.read_compiled(relpath)
        except Exception:
            return None
        # Import here to keep top-level cheap; parses front-matter only.
        import frontmatter as _fm

        from ..compiled_io import HCAG_COMPILED_MARKER

        text = raw
        lines = text.splitlines()
        if lines and lines[0].startswith(HCAG_COMPILED_MARKER):
            text = "\n".join(lines[1:])
        post = _fm.loads(text)
        m = post.metadata
        entry = CatalogEntry(
            id=str(m.get("id", packet_id) or packet_id),
            path=relpath,
            title=str(m.get("title", packet_id)),
            short_description=str(m.get("short_description", "")),
            long_description=str(m.get("long_description", "")),
            token_size_estimate=int(m.get("token_size_estimate", 0) or 0),
        )
        self._index[entry.id] = _ResolvedFolder(entry=entry, path=relpath)
        # Also index this folder's sub-topics so the agent can drill deeper
        # without paying another front-matter read.
        for child in parse_subtopics(post.content):
            child_entry = _child_entry_to_catalog_entry(child, parent_relpath=relpath)
            self._index.setdefault(child_entry.id, _ResolvedFolder(entry=child_entry, path=child_entry.path))
        return entry

    def _catalog_view(self) -> Catalog:
        """Return a fresh Catalog reflecting every id we've resolved so far.

        The eviction policy consults ``Catalog.get(id)`` and ``Catalog.ids()``;
        those just need to see every id currently in play, which includes the
        top-level entries plus anything the agent has since drilled into.
        """
        entries = [rf.entry for rf in self._index.values()]
        base = self._catalog.raw_markdown if self._catalog else ""
        return Catalog(entries=entries, raw_markdown=base)

    # ---- check_and_load_kb ----------------------------------------------

    def check_and_load_kb(self, request: CheckAndLoadRequest) -> Delta:
        # Ensure the top-level catalog is loaded (also indexes top-level ids).
        self.get_catalog()

        if self.logger:
            self.logger.info(
                "check_and_load_kb.call",
                context=(request.context or "")[:512],
                requested=list(request.requested_packet_ids),
                active_in=list(request.active_packet_ids),
            )

        # Lazily resolve every requested id so its token estimate is known
        # before the eviction policy runs. Unknown ids produce a LoadError.
        prelim_errors: list[LoadError] = []
        for pid in request.requested_packet_ids:
            if self._resolve(pid) is None and pid not in self._index:
                prelim_errors.append(
                    LoadError(packet_id=pid, reason="unknown_packet_id")
                )
        # Active ids should already be in the index (they were loaded before)
        # but resolve defensively.
        for pid in request.active_packet_ids:
            if pid not in self._index:
                self._resolve(pid)

        catalog = self._catalog_view()

        plan = self.eviction.plan(
            active=list(request.active_packet_ids),
            incoming=[
                pid for pid in request.requested_packet_ids if pid in self._index
            ],
            budget=self.budget,
            catalog=catalog,
        )

        if plan.error is not None:
            delta = Delta(
                loaded=[],
                evicted=[],
                active_after=list(request.active_packet_ids),
                errors=[*prelim_errors, plan.error],
            )
            if self.logger:
                self.logger.error(
                    "check_and_load_kb.error",
                    error_packet=plan.error.packet_id,
                    reason=plan.error.reason,
                )
            return delta

        loaded: list[Packet] = []
        errors: list[LoadError] = list(prelim_errors)
        for pid in plan.to_load:
            hit = self._index.get(pid)
            if hit is None:
                errors.append(LoadError(packet_id=pid, reason="catalog_lookup_failed"))
                continue
            entry = hit.entry
            try:
                raw = self.storage.read_compiled(hit.path)
                asset_paths = self.storage.list_assets(hit.path)
                assets: list[tuple[str, bytes]] = []
                for ap in asset_paths:
                    try:
                        assets.append((ap, self.storage.read_asset(ap)))
                    except Exception as e:  # noqa: BLE001
                        errors.append(
                            LoadError(packet_id=pid, reason=f"asset_read_failed: {ap}: {e}")
                        )
                loaded.append(assemble_packet(entry, raw, assets))
                # Ensure this folder's own sub-topics are indexed post-load so
                # the agent's drill-down finds their metadata without extra I/O.
                for child in parse_subtopics(raw):
                    child_entry = _child_entry_to_catalog_entry(child, parent_relpath=hit.path)
                    self._index.setdefault(
                        child_entry.id, _ResolvedFolder(entry=child_entry, path=child_entry.path)
                    )
            except Exception as e:  # noqa: BLE001
                errors.append(LoadError(packet_id=pid, reason=f"packet_read_failed: {e}"))

        delta = Delta(
            loaded=loaded,
            evicted=plan.evicted,
            active_after=plan.ordered_active_after,
            errors=errors,
        )

        if self.logger:
            self.logger.info(
                "check_and_load_kb.result",
                loaded=[p.id for p in loaded],
                evicted=list(plan.evicted),
                active_after=list(plan.ordered_active_after),
                tokens_used=self.budget.sum_estimate(plan.ordered_active_after, catalog),
                tokens_budget=self.budget.max_active_tokens,
                errors=[{"id": e.packet_id, "reason": e.reason} for e in errors],
            )

        return delta
