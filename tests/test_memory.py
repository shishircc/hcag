"""FileSystemMemoryModule integration tests — bootstrap catalog + drill-load."""

from __future__ import annotations

from pathlib import Path

from hcag.memory import FileSystemMemoryModule, LocalFsStorage, TokenBudget
from hcag.models import CheckAndLoadRequest


ROOT_COMPILED = """<!-- HCAG:COMPILED id=_root -->
---
id: ""
title: Root
short_description: KB root
long_description: The top-level branches of the KB.
token_size_estimate: 50
kind: node
source_files: []
children:
- billing.refunds
- billing.invoices
---

# Root

KB root

## Sub-topics

### `billing.refunds`
- **path**: `billing/refunds`
- **title**: Refund Processing
- **short**: How refunds are issued.
- **long**: Full lifecycle of refund processing.
- **tokens**: 100

### `billing.invoices`
- **path**: `billing/invoices`
- **title**: Invoicing
- **short**: How invoices work.
- **long**: Invoice generation and states.
- **tokens**: 100
"""

REFUNDS_COMPILED = """<!-- HCAG:COMPILED id=billing.refunds -->
---
id: billing.refunds
title: Refund Processing
short_description: How refunds are issued.
long_description: Full lifecycle of refund processing.
token_size_estimate: 100
kind: leaf
source_files:
- refunds.md
children: []
---

# Refund Processing

## Content

<!-- source: refunds.md -->
Refunds work like this.
"""

INVOICES_COMPILED = """<!-- HCAG:COMPILED id=billing.invoices -->
---
id: billing.invoices
title: Invoicing
short_description: How invoices work.
long_description: Invoice generation and states.
token_size_estimate: 100
kind: leaf
source_files:
- invoices.md
children: []
---

# Invoicing

## Content

<!-- source: invoices.md -->
Invoices work like this.
"""


def _setup_kb(tmp_path: Path) -> Path:
    (tmp_path / "compiled.md").write_text(ROOT_COMPILED, encoding="utf-8")
    refunds = tmp_path / "billing" / "refunds"
    refunds.mkdir(parents=True)
    (refunds / "compiled.md").write_text(REFUNDS_COMPILED, encoding="utf-8")
    invoices = tmp_path / "billing" / "invoices"
    invoices.mkdir(parents=True)
    (invoices / "compiled.md").write_text(INVOICES_COMPILED, encoding="utf-8")
    return tmp_path


def test_get_catalog_parses_top_level_subtopics(tmp_path: Path) -> None:
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
    # The injected catalog text is a `## Packets` rendering of the top-level.
    assert "## Packets" in catalog.raw_markdown
    assert "billing.refunds" in catalog.raw_markdown


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


def test_drill_down_resolves_lazily(tmp_path: Path) -> None:
    """A packet the agent asks for that isn't in the top-level catalog is
    resolved by reading its own compiled.md front-matter on demand."""
    root = tmp_path / "kb"
    root.mkdir()
    # Root's top-level catalog only names 'a'.
    (root / "compiled.md").write_text(
        """<!-- HCAG:COMPILED id=_root -->
---
id: ""
title: R
short_description: root
long_description: root
token_size_estimate: 10
kind: node
source_files: []
children: [a]
---

# R

## Sub-topics

### `a`
- **path**: `a`
- **title**: A
- **short**: branch A
- **long**: branch A
- **tokens**: 20
""",
        encoding="utf-8",
    )
    # Mid-level 'a' exposes 'a.deep' in its sub-topics.
    (root / "a").mkdir()
    (root / "a" / "compiled.md").write_text(
        """<!-- HCAG:COMPILED id=a -->
---
id: a
title: A
short_description: branch A
long_description: branch A
token_size_estimate: 20
kind: node
source_files: []
children: [a.deep]
---

# A

## Sub-topics

### `a.deep`
- **path**: `deep`
- **title**: Deep
- **short**: deepest
- **long**: deepest content
- **tokens**: 30
""",
        encoding="utf-8",
    )
    # Deep leaf.
    (root / "a" / "deep").mkdir()
    (root / "a" / "deep" / "compiled.md").write_text(
        """<!-- HCAG:COMPILED id=a.deep -->
---
id: a.deep
title: Deep
short_description: deepest
long_description: deepest content
token_size_estimate: 30
kind: leaf
source_files: [d.md]
children: []
---

# Deep

## Content

<!-- source: d.md -->
Deep content.
""",
        encoding="utf-8",
    )

    module = FileSystemMemoryModule(
        storage=LocalFsStorage(root),
        budget=TokenBudget(1000),
    )
    # Ask directly for a.deep — it's not in the top-level catalog but the
    # module must resolve it by reading its own compiled.md.
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="get deep",
            requested_packet_ids=["a.deep"],
            active_packet_ids=[],
        )
    )
    assert delta.errors == []
    assert [p.id for p in delta.loaded] == ["a.deep"]
    text_blocks = [b.text for b in delta.loaded[0].content if hasattr(b, "text")]
    assert any("Deep content" in t for t in text_blocks)
