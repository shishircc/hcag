"""Memory module — per-call stateless, sole KB accessor (§2, D4a, D7).

Bootstrap reads the root ``compiled.md`` and returns its ``## Sub-topics``
section as the catalog injected into the system prompt. Because catalogs roll
up the whole subtree (D3a), that section is the **complete index of every
folder in the KB at every depth** — so the agent resolves a question straight
to a leaf id in one hop instead of descending the tree one
``check_and_load_kb`` at a time (§2.7).

Loading a non-root packet therefore ships only its ``## Content``: its own
``## Sub-topics`` section is a verbatim subset of the catalog already in the
system prompt (§2.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..compiled_io import (
    CatalogRecord,
    extract_subtopics_section,
    parse_compiled,
    parse_subtopics,
    strip_compiled_frontmatter,
)
from ..logger import HcagLogger
from ..prompting import PromptLibrary, load_prompts
from ..models import (
    Catalog,
    CatalogEntry,
    CheckAndLoadRequest,
    Delta,
    LoadError,
    Packet,
    coerce_packet_ids,
)
from .eviction import EvictionPolicy, LRUEvictionPolicy, TokenBudget
from .packet_loader import assemble_packet
from .storage import KBStorage


class MemoryModule(Protocol):
    def get_catalog(self) -> Catalog: ...
    def check_and_load_kb(self, request: CheckAndLoadRequest) -> Delta: ...


# --- Helpers ---------------------------------------------------------------


def _id_to_relpath(packet_id: str) -> str:
    """Dotted packet ID to POSIX-relative KB path (§3.4.5).

    Lossy when a folder name itself contains a dot (`www.mom.gov.sg`), which is
    why it is only the last-resort guess in `_candidate_paths`. On a KB built
    with the subtree roll-up it is never reached: the root catalog carries an
    explicit `path` for every folder, so nothing has to be derived from ids.
    """
    return packet_id.replace(".", "/")


def _record_to_catalog_entry(record: CatalogRecord, owner_relpath: str) -> CatalogEntry:
    """Turn a catalog record into a KB-absolute entry.

    A record's ``path`` is relative to the folder that owns the catalog, so it
    is stitched onto that owner's own KB-relative path. For the root (owner
    path ``""``) the record's path is already KB-absolute.
    """
    record_path = record.path.strip("/")
    if owner_relpath and record_path:
        combined = f"{owner_relpath}/{record_path}"
    else:
        combined = owner_relpath or record_path
    return CatalogEntry(
        id=record.id,
        path=combined.strip("/"),
        title=record.title,
        short_description=record.short,
        long_description=record.long,
        token_size_estimate=record.tokens,
        depth=record.depth,
        parent=record.parent,
        kind=record.kind,
        # Catalog entries carry the descendant's content-only estimate (§2.2).
        content_token_estimate=record.tokens,
    )


def _render_catalog_for_prompt(section: str) -> str:
    """Wrap the root's ``## Sub-topics`` section for system-prompt injection.

    The section is emitted verbatim rather than re-rendered: it is already
    exactly the shape §2.2 documents, and passing the bytes straight through
    means the build tool's output and what the LLM sees cannot drift apart.
    """
    return "# Knowledge Catalog\n\n## Sub-topics\n\n" + section.strip() + "\n"


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
        strip_subtopics_on_load: bool = True,
        prompts: "PromptLibrary | None" = None,
    ) -> None:
        self.storage = storage
        self.budget = budget
        self.eviction = eviction or LRUEvictionPolicy()
        self.logger = logger
        self.tracer = tracer
        self.strip_subtopics_on_load = strip_subtopics_on_load
        # Model-facing text, so a file rather than a literal (D11).
        self.prompts = prompts or load_prompts()
        self._catalog: Catalog | None = None
        # Populated wholesale from the root catalog; only grows further for
        # KBs built with `catalog.max_depth` set, or by an older build.
        self._index: dict[str, _ResolvedFolder] = {}
        # Load order of the active set — the sequence packets were FIRST
        # loaded in, which is the sequence their blocks sit in the
        # conversation. The module keeps it so the model's bookkeeping cannot
        # reorder a prefix the provider is caching (§2.4, §2.12).
        self._active_order: list[str] = []

    # ---- get_catalog -----------------------------------------------------

    def get_catalog(self) -> Catalog:
        if self._catalog is None:
            raw = self.storage.read_compiled("")  # root
            _fm, records, body = parse_compiled(raw)
            section = extract_subtopics_section(body)
            entries = [_record_to_catalog_entry(r, owner_relpath="") for r in records]
            for e in entries:
                self._index[e.id] = _ResolvedFolder(entry=e, path=e.path)
            self._catalog = Catalog(
                entries=entries, raw_markdown=_render_catalog_for_prompt(section)
            )
            if self.logger:
                self.logger.info(
                    "catalog.loaded",
                    entries=len(entries),
                    max_depth=max((e.depth for e in entries), default=0),
                    leaves=sum(1 for e in entries if e.kind in ("leaf", "mixed")),
                    bytes=len(raw),
                )
        return self._catalog

    # ---- Packet-index population ----------------------------------------

    def _candidate_paths(self, packet_id: str) -> list[str]:
        """KB-relative paths to try for an id the index does not name.

        Preferred: hang the unknown tail off the longest ancestor whose path we
        already know — that survives folder names containing dots, which pure
        id arithmetic cannot. Falls back to the naive dotted-to-slash mapping.
        """
        candidates: list[str] = []
        best: tuple[str, str] | None = None
        for known_id, rf in self._index.items():
            if known_id and packet_id.startswith(known_id + "."):
                if best is None or len(known_id) > len(best[0]):
                    best = (known_id, rf.path)
        if best is not None:
            tail = packet_id[len(best[0]) + 1 :].replace(".", "/")
            candidates.append("/".join(p for p in (best[1], tail) if p))
        candidates.append(_id_to_relpath(packet_id))
        return list(dict.fromkeys(candidates))

    def _resolve(self, packet_id: str) -> CatalogEntry | None:
        """Return the metadata for ``packet_id``.

        The root catalog normally names every folder, so this is a dict hit.
        The fallback — reading the folder's own ``compiled.md`` front-matter —
        exists for KBs whose roll-up was capped by ``catalog.max_depth`` and
        for artifacts written before the roll-up existed. Returns ``None`` on
        any I/O failure; the caller turns that into a ``LoadError``.
        """
        hit = self._index.get(packet_id)
        if hit is not None:
            return hit.entry
        relpath = next(
            (p for p in self._candidate_paths(packet_id) if self.storage.has_compiled(p)),
            None,
        )
        if relpath is None:
            return None
        try:
            raw = self.storage.read_compiled(relpath)
        except Exception:
            return None
        fm, records, _body = parse_compiled(raw)
        entry = CatalogEntry(
            id=fm.id or packet_id,
            path=relpath,
            title=fm.title or packet_id,
            short_description=fm.short_description,
            long_description=fm.long_description,
            token_size_estimate=fm.token_size_estimate,
            kind=fm.kind,
            content_token_estimate=fm.content_token_estimate,
        )
        self._index[entry.id] = _ResolvedFolder(entry=entry, path=relpath)
        # Index whatever this folder's own catalog names, so a capped root
        # catalog still lets the agent reach the level below.
        self._index_records(records, relpath)
        return entry

    def _index_records(self, records: list[CatalogRecord], owner_relpath: str) -> None:
        for r in records:
            entry = _record_to_catalog_entry(r, owner_relpath=owner_relpath)
            self._index.setdefault(entry.id, _ResolvedFolder(entry=entry, path=entry.path))

    def _catalog_view(self) -> Catalog:
        """A Catalog reflecting every id resolved so far.

        The eviction policy consults ``Catalog.get(id)`` and ``Catalog.ids()``;
        those need to see every id currently in play, which is the root index
        plus anything resolved past a depth cap.
        """
        entries = [rf.entry for rf in self._index.values()]
        base = self._catalog.raw_markdown if self._catalog else ""
        return Catalog(entries=entries, raw_markdown=base)

    # ---- Active-set order ------------------------------------------------

    def _reconcile_active(self, claimed: list[str]) -> list[str]:
        """The effective active set, ordered by when each packet was loaded.

        Load order belongs to the module, not to the model: a packet keeps the
        position it was first loaded into, so `active_after` and the packet
        blocks already in the conversation stay in the same sequence turn after
        turn (§2.4). A caller's claim still decides membership for ids the
        module has never loaded — a resumed session, or a voice startup that
        preloaded elsewhere — and those append at the tail in the order given.
        """
        known = set(self._active_order)
        effective = [*self._active_order, *[pid for pid in claimed if pid not in known]]
        if self.logger and claimed and claimed != effective:
            drift = "membership" if set(claimed) != set(effective) else "order"
            self.logger.warn(
                "check_and_load_kb.active_drift",
                drift=drift,
                claimed=claimed,
                effective=effective,
            )
        return effective

    # ---- check_and_load_kb ----------------------------------------------

    def check_and_load_kb(self, request: CheckAndLoadRequest) -> Delta:
        # Ensure the catalog is loaded (this also indexes every known id).
        self.get_catalog()

        # Redundant call: every requested id is already in the active set, so
        # there is nothing to load. D7 keeps the agent authoritative over its
        # own active set, so this is not rejected — but a silent empty delta
        # teaches the model nothing, and the reflex call is the behavior
        # §2.7.1 exists to suppress. Name it, in the result and in the log.
        # Defensive: callers other than the tool boundary (voice startup, the
        # eval harness) build the request themselves, so normalize here too
        # rather than trust every construction site.
        requested = coerce_packet_ids(request.requested_packet_ids)
        active = self._reconcile_active(coerce_packet_ids(request.active_packet_ids))
        if requested and all(pid in active for pid in requested):
            note = self.prompts.get(
                "memory.redundant_note", requested=", ".join(requested)
            )
            if self.logger:
                self.logger.warn(
                    "check_and_load_kb.redundant",
                    context=(request.context or "")[:512],
                    requested=requested,
                    active_in=active,
                )
            self._active_order = list(active)
            return Delta(
                loaded=[],
                evicted=[],
                active_after=active,
                redundant=True,
                note=note,
            )

        if self.logger:
            self.logger.info(
                "check_and_load_kb.call",
                context=(request.context or "")[:512],
                requested=requested,
                active_in=active,
            )

        # Resolve every requested id so its token estimate is known before the
        # eviction policy runs. Unknown ids produce a LoadError.
        prelim_errors: list[LoadError] = []
        for pid in requested:
            if self._resolve(pid) is None and pid not in self._index:
                prelim_errors.append(
                    LoadError(packet_id=pid, reason="unknown_packet_id")
                )
        # Active ids should already be in the index (they were loaded before)
        # but resolve defensively.
        for pid in active:
            if pid not in self._index:
                self._resolve(pid)

        catalog = self._catalog_view()

        plan = self.eviction.plan(
            active=list(active),
            incoming=[
                pid for pid in requested if pid in self._index
            ],
            budget=self.budget,
            catalog=catalog,
        )

        if plan.error is not None:
            delta = Delta(
                loaded=[],
                evicted=[],
                active_after=list(active),
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
            if not self.storage.has_compiled(hit.path):
                # The catalog names it but the artifact is gone — the KB tree
                # changed without a `preprocess` re-run (§2.8).
                errors.append(
                    LoadError(
                        packet_id=pid,
                        reason=f"stale_catalog: no compiled.md at {hit.path}",
                    )
                )
                continue
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
                is_root = hit.path == ""
                loaded.append(
                    assemble_packet(
                        entry,
                        raw,
                        assets,
                        strip_subtopics=self.strip_subtopics_on_load and not is_root,
                    )
                )
                # If the root roll-up was capped, this folder's own catalog is
                # the only place its descendants are named — index them.
                self._index_records(parse_subtopics(strip_compiled_frontmatter(raw)), hit.path)
            except Exception as e:  # noqa: BLE001
                errors.append(LoadError(packet_id=pid, reason=f"packet_read_failed: {e}"))

        self._active_order = list(plan.ordered_active_after)
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
