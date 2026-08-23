"""Memory module — per-call stateless, sole KB accessor (D4a, D7)."""

from __future__ import annotations

import re
from typing import Protocol

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


# --- catalog.md parsing ---------------------------------------------------

_ENTRY_HEADER_RE = re.compile(r"^###\s+`([^`]+)`\s*$", re.MULTILINE)
_FIELD_RE = re.compile(
    r"^-\s*\*\*(?P<key>[^*]+)\*\*\s*:\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)


def parse_catalog(raw: str) -> Catalog:
    """Parse the root catalog.md emitted by `hcag aggregate` (§3.5.3)."""
    packets_section = raw
    if "## Packets" in raw:
        packets_section = raw.split("## Packets", 1)[1]

    entries: list[CatalogEntry] = []
    matches = list(_ENTRY_HEADER_RE.finditer(packets_section))
    for i, m in enumerate(matches):
        packet_id = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(packets_section)
        block = packets_section[start:end]
        fields: dict[str, str] = {}
        for fm in _FIELD_RE.finditer(block):
            key = fm.group("key").strip().lower()
            fields[key] = fm.group("value").strip()

        try:
            entries.append(
                CatalogEntry(
                    id=packet_id,
                    path=fields.get("path", "").strip("`/ ").rstrip("/"),
                    title=fields.get("title", packet_id),
                    short_description=fields.get("short", ""),
                    long_description=fields.get("long", ""),
                    token_size_estimate=int(fields.get("tokens", "0") or "0"),
                )
            )
        except ValueError:
            # Bad tokens field — treat as zero rather than failing catalog load
            entries.append(
                CatalogEntry(
                    id=packet_id,
                    path=fields.get("path", "").strip("`/ ").rstrip("/"),
                    title=fields.get("title", packet_id),
                    short_description=fields.get("short", ""),
                    long_description=fields.get("long", ""),
                    token_size_estimate=0,
                )
            )
    return Catalog(entries=entries, raw_markdown=raw)


# --- FileSystemMemoryModule -----------------------------------------------


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

    # ---- get_catalog -----------------------------------------------------

    def get_catalog(self) -> Catalog:
        if self._catalog is None:
            raw = self.storage.read_catalog()
            self._catalog = parse_catalog(raw)
            if self.logger:
                self.logger.info(
                    "catalog.loaded",
                    entries=len(self._catalog.entries),
                    bytes=len(raw),
                )
        return self._catalog

    # ---- check_and_load_kb ----------------------------------------------

    def check_and_load_kb(self, request: CheckAndLoadRequest) -> Delta:
        catalog = self.get_catalog()

        if self.logger:
            self.logger.info(
                "check_and_load_kb.call",
                context=(request.context or "")[:512],
                requested=list(request.requested_packet_ids),
                active_in=list(request.active_packet_ids),
            )

        plan = self.eviction.plan(
            active=list(request.active_packet_ids),
            incoming=list(request.requested_packet_ids),
            budget=self.budget,
            catalog=catalog,
        )

        if plan.error is not None:
            delta = Delta(
                loaded=[],
                evicted=[],
                active_after=list(request.active_packet_ids),
                errors=[plan.error],
            )
            if self.logger:
                self.logger.error(
                    "check_and_load_kb.error",
                    error_packet=plan.error.packet_id,
                    reason=plan.error.reason,
                )
            return delta

        loaded: list[Packet] = []
        errors: list[LoadError] = []
        for pid in plan.to_load:
            entry = catalog.get(pid)
            if entry is None:
                errors.append(LoadError(packet_id=pid, reason="catalog_lookup_failed"))
                continue
            try:
                markdown = self.storage.read_packet_markdown(entry.path)
                asset_paths = self.storage.list_assets(entry.path)
                assets: list[tuple[str, bytes]] = []
                for ap in asset_paths:
                    try:
                        assets.append((ap, self.storage.read_asset(ap)))
                    except Exception as e:  # noqa: BLE001
                        errors.append(LoadError(packet_id=pid, reason=f"asset_read_failed: {ap}: {e}"))
                loaded.append(assemble_packet(entry, markdown, assets))
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
