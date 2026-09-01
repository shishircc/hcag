"""Token budget + LRU eviction per §2.5.

The eviction policy is stateless — the caller supplies the current LRU-ordered
active set, the incoming packet IDs, and the catalog for token estimates. The
policy returns an EvictionPlan describing what to load and evict.
"""

from __future__ import annotations

from typing import Protocol

from ..models import Catalog, EvictionPlan, LoadError


class TokenBudget:
    def __init__(self, max_active_tokens: int) -> None:
        self.max_active_tokens = max_active_tokens

    def sum_estimate(self, ids: list[str], catalog: Catalog) -> int:
        total = 0
        for pid in ids:
            entry = catalog.get(pid)
            if entry is not None:
                total += entry.budget_tokens
        return total

    def fits(self, total: int) -> bool:
        return total <= self.max_active_tokens


class EvictionPolicy(Protocol):
    def plan(
        self,
        active: list[str],
        incoming: list[str],
        budget: TokenBudget,
        catalog: Catalog,
    ) -> EvictionPlan: ...


def _dedup_keep_last(seq: list[str]) -> list[str]:
    """Preserve last occurrence, keeping stable relative order for the survivors."""
    seen: set[str] = set()
    out: list[str] = []
    for x in reversed(seq):
        if x not in seen:
            seen.add(x)
            out.append(x)
    return list(reversed(out))


class LRUEvictionPolicy:
    """Head = least recently used; tail = most recently used."""

    def plan(
        self,
        active: list[str],
        incoming: list[str],
        budget: TokenBudget,
        catalog: Catalog,
    ) -> EvictionPlan:
        catalog_ids = catalog.ids()
        unknown = [pid for pid in incoming if pid not in catalog_ids]
        if unknown:
            return EvictionPlan(
                ordered_active_after=list(active),
                evicted=[],
                to_load=[],
                error=LoadError(packet_id=unknown[0], reason="unknown_packet_id"),
            )

        to_add = [pid for pid in incoming if pid not in active]
        ordered = _dedup_keep_last(active + to_add)

        total = budget.sum_estimate(ordered, catalog)
        evicted: list[str] = []
        protected = set(to_add)

        while not budget.fits(total) and ordered:
            # Never evict a packet the caller just requested
            if ordered[0] in protected:
                # If the head is protected, budget cannot be reclaimed — bail out
                oversized = ordered[0]
                return EvictionPlan(
                    ordered_active_after=list(active),
                    evicted=[],
                    to_load=[],
                    error=LoadError(
                        packet_id=oversized,
                        reason=f"budget_exceeded: requested packet {oversized} does not fit within budget",
                    ),
                )
            victim = ordered.pop(0)
            entry = catalog.get(victim)
            if entry is not None:
                total -= entry.budget_tokens
            evicted.append(victim)

        return EvictionPlan(
            ordered_active_after=ordered,
            evicted=evicted,
            to_load=[pid for pid in to_add if pid in ordered],
        )
