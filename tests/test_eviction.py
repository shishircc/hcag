"""LRU eviction tests — the load-bearing algorithm from §2.5."""

from __future__ import annotations

from hcag.memory.eviction import LRUEvictionPolicy, TokenBudget
from hcag.models import Catalog, CatalogEntry


def _make_catalog(sizes: dict[str, int]) -> Catalog:
    return Catalog(
        entries=[
            CatalogEntry(
                id=k,
                path=k,
                title=k,
                short_description="",
                long_description="",
                token_size_estimate=v,
            )
            for k, v in sizes.items()
        ],
        raw_markdown="",
    )


def test_within_budget_no_eviction() -> None:
    catalog = _make_catalog({"a": 100, "b": 200, "c": 300})
    policy = LRUEvictionPolicy()
    plan = policy.plan(active=["a"], incoming=["b"], budget=TokenBudget(1000), catalog=catalog)
    assert plan.error is None
    assert plan.evicted == []
    assert plan.to_load == ["b"]
    assert plan.ordered_active_after == ["a", "b"]


def test_evicts_lru_when_over_budget() -> None:
    catalog = _make_catalog({"a": 400, "b": 400, "c": 400})
    policy = LRUEvictionPolicy()
    plan = policy.plan(
        active=["a", "b"],  # a is LRU
        incoming=["c"],
        budget=TokenBudget(1000),
        catalog=catalog,
    )
    assert plan.error is None
    assert plan.evicted == ["a"]
    assert plan.to_load == ["c"]
    assert plan.ordered_active_after == ["b", "c"]


def test_re_requesting_active_packet_is_noop() -> None:
    """§2.5: the caller supplies LRU order; the module does not silently reorder.
    A no-op re-request returns nothing to load and leaves the active set intact.
    """
    catalog = _make_catalog({"a": 100, "b": 100, "c": 100})
    policy = LRUEvictionPolicy()
    plan = policy.plan(
        active=["a", "b", "c"],
        incoming=["a"],
        budget=TokenBudget(1000),
        catalog=catalog,
    )
    assert plan.error is None
    assert plan.evicted == []
    assert plan.to_load == []
    assert plan.ordered_active_after == ["a", "b", "c"]


def test_unknown_id_errors_without_state_mutation() -> None:
    catalog = _make_catalog({"a": 100})
    policy = LRUEvictionPolicy()
    plan = policy.plan(
        active=["a"],
        incoming=["nope"],
        budget=TokenBudget(1000),
        catalog=catalog,
    )
    assert plan.error is not None
    assert plan.error.packet_id == "nope"
    assert plan.evicted == []
    assert plan.ordered_active_after == ["a"]


def test_single_request_over_budget_errors() -> None:
    catalog = _make_catalog({"big": 5000})
    policy = LRUEvictionPolicy()
    plan = policy.plan(
        active=[],
        incoming=["big"],
        budget=TokenBudget(1000),
        catalog=catalog,
    )
    assert plan.error is not None
    assert "budget_exceeded" in plan.error.reason


def test_multiple_evictions_until_fits() -> None:
    catalog = _make_catalog({"a": 400, "b": 400, "c": 400, "d": 400})
    policy = LRUEvictionPolicy()
    plan = policy.plan(
        active=["a", "b", "c"],  # 1200 tokens
        incoming=["d"],
        budget=TokenBudget(1000),
        catalog=catalog,
    )
    assert plan.error is None
    assert plan.evicted == ["a", "b"]  # evict both LRUs
    assert plan.to_load == ["d"]
    assert plan.ordered_active_after == ["c", "d"]
