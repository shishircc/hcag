"""FileSystemMemoryModule integration test — end-to-end catalog + packet load."""

from __future__ import annotations

from pathlib import Path

from hcag.memory import FileSystemMemoryModule, LocalFsStorage, TokenBudget
from hcag.models import CheckAndLoadRequest


CATALOG = """<!-- HCAG:ROOT_CATALOG generated_at=2026-01-01T00:00:00Z -->

# Knowledge Catalog

## Taxonomy Overview

- **billing** — Billing docs.
  - **billing.refunds** — Refund processing.

## Packets

### `billing.refunds`
- **path**: `billing/refunds/`
- **breadcrumb**: billing → refunds
- **title**: Refund Processing
- **short**: How refunds are issued.
- **long**: Full lifecycle of refund processing.
- **tokens**: 100

### `billing.invoices`
- **path**: `billing/invoices/`
- **breadcrumb**: billing → invoices
- **title**: Invoicing
- **short**: How invoices work.
- **long**: Invoice generation and states.
- **tokens**: 100
"""


REFUNDS_MD = "# Refunds\n\nRefunds work like this.\n"
INVOICES_MD = "# Invoices\n\nInvoices work like this.\n"


def _setup_kb(tmp_path: Path) -> Path:
    (tmp_path / "catalog.md").write_text(CATALOG, encoding="utf-8")
    refunds = tmp_path / "billing" / "refunds"
    refunds.mkdir(parents=True)
    (refunds / "packet.md").write_text(REFUNDS_MD, encoding="utf-8")
    invoices = tmp_path / "billing" / "invoices"
    invoices.mkdir(parents=True)
    (invoices / "packet.md").write_text(INVOICES_MD, encoding="utf-8")
    return tmp_path


def test_get_catalog_parses_entries(tmp_path: Path) -> None:
    root = _setup_kb(tmp_path)
    module = FileSystemMemoryModule(
        storage=LocalFsStorage(root),
        budget=TokenBudget(1000),
    )
    catalog = module.get_catalog()
    ids = {e.id for e in catalog.entries}
    assert ids == {"billing.refunds", "billing.invoices"}
    entry = catalog.get("billing.refunds")
    assert entry is not None
    assert entry.path == "billing/refunds"
    assert entry.token_size_estimate == 100


def test_check_and_load_returns_delta(tmp_path: Path) -> None:
    root = _setup_kb(tmp_path)
    module = FileSystemMemoryModule(
        storage=LocalFsStorage(root),
        budget=TokenBudget(1000),
    )
    req = CheckAndLoadRequest(
        context="need refund flow",
        requested_packet_ids=["billing.refunds"],
        active_packet_ids=[],
    )
    delta = module.check_and_load_kb(req)
    assert len(delta.loaded) == 1
    assert delta.loaded[0].id == "billing.refunds"
    assert delta.evicted == []
    assert delta.active_after == ["billing.refunds"]
    # The packet content should include header + markdown as text blocks
    text_blocks = [b for b in delta.loaded[0].content if hasattr(b, "text")]
    assert any("Refunds work like this" in b.text for b in text_blocks)


def test_unknown_id_returns_error(tmp_path: Path) -> None:
    root = _setup_kb(tmp_path)
    module = FileSystemMemoryModule(
        storage=LocalFsStorage(root),
        budget=TokenBudget(1000),
    )
    req = CheckAndLoadRequest(
        context="try bad id",
        requested_packet_ids=["not.a.real.id"],
        active_packet_ids=[],
    )
    delta = module.check_and_load_kb(req)
    assert delta.loaded == []
    assert delta.errors
    assert delta.errors[0].packet_id == "not.a.real.id"
