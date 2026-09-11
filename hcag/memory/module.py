"""Memory module — per-call stateless, sole KB accessor (§2, D4a, D7).

Bootstrap reads the root ``compiled.md`` and returns its ``## Catalog`` table
as the catalog injected into the system prompt. One table, one row per
content-bearing folder in the KB, so the agent resolves a question straight to
a packet id in one hop instead of descending the tree one ``check_and_load_kb``
at a time (§2.7).

Loading a packet ships its ``## Content`` section. Nothing has to be elided:
only the root has a catalog section, and the root is not served as a packet
(§2.6, D3a).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..compiled_io import (
    CatalogRecord,
    extract_catalog_section,
    parse_compiled,
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
    why it is only the last-resort guess in `_candidate_paths`. On a KB the
    build produced it is never reached for a content folder: the catalog
    carries an explicit `path` on every row. It *is* reached for a pure
    taxonomy node, which has no row (D3a) and which nothing normally loads.
    """
    return packet_id.replace(".", "/")


def _record_to_catalog_entry(record: CatalogRecord) -> CatalogEntry:
    """Turn a catalog row into an entry.

    A row's ``id`` and ``path`` are already KB-absolute (§3.4.1), so there is
    nothing to stitch. ``content_token_estimate`` is left unset here and filled
    from the folder's own front-matter (§2.2.1) — the table carries no token
    count, because the module budgets and the model does not.
    """
    return CatalogEntry(
        id=record.id,
        path=record.path.strip("/"),
        title=record.title,
        long_description=record.long,
        depth=record.depth,
    )


def _render_catalog_for_prompt(section: str) -> str:
    """Wrap the root's ``## Catalog`` table for system-prompt injection.

    The section is emitted verbatim rather than re-rendered: it is already
    exactly the shape §2.2.1 documents, and passing the bytes straight through
    means the build tool's output and what the LLM sees cannot drift apart.
    """
    return "# Knowledge Catalog\n\n## Catalog\n\n" + section.strip() + "\n"


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
        prompts: "PromptLibrary | None" = None,
    ) -> None:
        self.storage = storage
        self.budget = budget
        self.eviction = eviction or LRUEvictionPolicy()
        self.logger = logger
        self.tracer = tracer
        # Model-facing text, so a file rather than a literal (D11).
        self.prompts = prompts or load_prompts()
        self._catalog: Catalog | None = None
        # Populated wholesale from the root catalog. It grows only for ids the
        # catalog does not name — a pure taxonomy node, which has no row.
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
            section = extract_catalog_section(body)
            entries = [_record_to_catalog_entry(r) for r in records]
            for e in entries:
                self._hydrate(e)
                self._index[e.id] = _ResolvedFolder(entry=e, path=e.path)
            self._catalog = Catalog(
                entries=entries, raw_markdown=_render_catalog_for_prompt(section)
            )
            if self.logger:
                self.logger.info(
                    "catalog.loaded",
                    entries=len(entries),
                    max_depth=max((e.depth for e in entries), default=0),
                    bytes=len(raw),
                )
        return self._catalog

    def _hydrate(self, entry: CatalogEntry) -> None:
        """Fill in what the catalog table deliberately does not carry (§2.2.1).

        One pass at bootstrap, reading each folder's front-matter for its
        `content_token_estimate` and `kind`. The budget is enforced *before* a
        packet is loaded (§2.5), so this figure has to be known up front — but
        it is a number the model cannot act on, so it stays out of the table
        rather than costing a column across several hundred rows.

        A row whose folder cannot be read keeps a zero estimate rather than
        failing the bootstrap: one unreadable folder should cost that folder,
        not the whole conversation.
        """
        try:
            fm, _rows, _body = parse_compiled(self.storage.read_compiled(entry.path))
        except Exception:  # noqa: BLE001 - storage/IO/parse, all non-fatal here
            if self.logger:
                self.logger.warn("catalog.entry.unreadable", id=entry.id, path=entry.path)
            return
        entry.content_token_estimate = fm.content_token_estimate
        entry.token_size_estimate = fm.token_size_estimate
        entry.kind = fm.kind

    # ---- Packet-index population ----------------------------------------

    def _candidate_paths(self, packet_id: str) -> list[str]:
        """KB-relative paths to try for an id the index does not name.

        Three sources, best first. Each is a guess; `_resolve` confirms the one
        it picks by reading the folder's own id back (§3.4.5), so a wrong guess
        costs a small read rather than serving the wrong packet.

        1. **Hang the unknown tail off the longest known ancestor.** Survives
           folder names containing dots, which pure id arithmetic cannot.
        2. **Trim a known descendant's path.** This is what resolves a pure
           taxonomy node: it has no catalog row (D3a), so nothing above it is
           known, but everything below it is.
        3. The naive dotted-to-slash mapping, which is right only when no
           folder name contains a dot.
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

        prefix = packet_id + "."
        for known_id, rf in self._index.items():
            if not known_id.startswith(prefix):
                continue
            depth_below = len(known_id[len(prefix) :].split("."))
            parts = rf.path.split("/")
            # A dotted segment below the ancestor is one folder, so the counts
            # line up unless a *descendant* folder name has a dot in it — which
            # is why the caller verifies rather than trusting this.
            for drop in range(depth_below, 0, -1):
                if len(parts) > drop:
                    candidates.append("/".join(parts[:-drop]))

        candidates.append(_id_to_relpath(packet_id))
        return list(dict.fromkeys(c for c in candidates if c)) or [""]

    def _resolve(self, packet_id: str) -> CatalogEntry | None:
        """Return the metadata for ``packet_id``.

        The catalog names every content-bearing folder, so this is normally a
        dict hit. The fallback — reading the folder's own ``compiled.md``
        front-matter — is what serves a pure taxonomy node, which has no row
        (D3a) and which loads as a header and nothing else. Returns ``None`` on
        any I/O failure; the caller turns that into a ``LoadError``.
        """
        hit = self._index.get(packet_id)
        if hit is not None:
            return hit.entry

        found: tuple[str, object] | None = None
        for relpath in self._candidate_paths(packet_id):
            if not self.storage.has_compiled(relpath):
                continue
            try:
                candidate_fm, _r, _b = parse_compiled(self.storage.read_compiled(relpath))
            except Exception:  # noqa: BLE001
                continue
            # Confirm rather than assume: several ancestors of a guessed path
            # have a compiled.md, and serving the wrong one silently answers
            # from the wrong folder.
            if candidate_fm.id == packet_id:
                found = (relpath, candidate_fm)
                break
        if found is None:
            return None
        relpath, fm = found
        entry = CatalogEntry(
            id=fm.id or packet_id,
            path=relpath,
            title=fm.title or packet_id,
            long_description=fm.long_description,
            token_size_estimate=fm.token_size_estimate,
            kind=fm.kind,
            content_token_estimate=fm.content_token_estimate,
        )
        self._index[entry.id] = _ResolvedFolder(entry=entry, path=relpath)
        return entry

    def _catalog_view(self) -> Catalog:
        """A Catalog reflecting every id resolved so far.

        The eviction policy consults ``Catalog.get(id)`` and ``Catalog.ids()``;
        those need to see every id currently in play, which is the catalog plus
        any waypoint the agent has resolved by id.
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
                loaded.append(assemble_packet(entry, raw, assets))
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
