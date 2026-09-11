"""One flat catalog table at the root, built from each folder's own content (D3a, §3.4.1, §3.4.4).

This file replaces the subtree-roll-up suite. What it pins is the shape of the
replacement: rows only for folders that hold something, written once at the
root, with descriptions that were never generated from other descriptions.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hcag.cli.metadata_llm import FolderMetadata
from hcag.cli.preprocess import preprocess_tree
from hcag.compiled_io import (
    CATALOG_BEGIN,
    CATALOG_END,
    CONTENT_BEGIN,
    read_compiled,
    read_compiled_frontmatter,
)
from hcag.config import CliConfig
from hcag.logger import build_logger


def _fake_metadata(cfg, *, own_content="", **kw):  # noqa: ARG001
    """Title derived from the folder's first heading so rows stay identifiable."""
    first = (own_content.splitlines() or [""])[0].lstrip("# ").strip()
    name = first or "Node"
    return FolderMetadata(title=name, long_description=f"long for {name}")


def _build(root: Path, cfg: CliConfig, *, force: bool = True) -> None:
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(root / "build.log")
    logger = build_logger(cfg.log, name=f"test.catalog.{root.parent.name}")
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_fake_metadata):
        preprocess_tree(root, cfg, logger, force=force)


def _make_tree(tmp_path: Path) -> Path:
    """root ─ billing ─ refunds ─ chargebacks   (billing/refunds hold content)
              auth ─ sso ─ saml                 (auth and auth/sso are waypoints)
    """
    root = tmp_path / "kb"
    (root / "billing" / "refunds" / "chargebacks").mkdir(parents=True)
    (root / "auth" / "sso" / "saml").mkdir(parents=True)
    (root / "billing" / "overview.md").write_text("# Billing\nMoney movement.\n", encoding="utf-8")
    (root / "billing" / "refunds" / "p.md").write_text("# Refunds\nRefund states.\n", encoding="utf-8")
    (root / "billing" / "refunds" / "chargebacks" / "c.md").write_text(
        "# Chargebacks\nEvidence deadlines.\n", encoding="utf-8"
    )
    (root / "auth" / "sso" / "saml" / "s.md").write_text("# SAML\nCert rotation.\n", encoding="utf-8")
    return root


def _rows(root: Path):
    parsed = read_compiled(root / "compiled.md")
    assert parsed is not None
    return parsed[1]


# --- What the catalog contains ---------------------------------------------


def test_the_root_indexes_every_content_folder_at_every_depth(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    ids = [r.id for r in _rows(root)]
    assert ids == [
        "auth.sso.saml",
        "billing",
        "billing.refunds",
        "billing.refunds.chargebacks",
    ]


def test_waypoints_get_no_row(tmp_path: Path) -> None:
    """A folder with no content has nothing to serve, so a row for it is an
    invitation to spend a turn loading nothing."""
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    ids = {r.id for r in _rows(root)}
    assert "auth" not in ids
    assert "auth.sso" not in ids


def test_a_waypoints_existence_is_still_legible_from_a_path(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    saml = next(r for r in _rows(root) if r.id == "auth.sso.saml")
    assert saml.path == "auth/sso/saml"
    assert saml.depth == 3


def test_rows_are_dfs_pre_order(tmp_path: Path) -> None:
    """A folder immediately followed by its own subtree, siblings alphabetical,
    so the table reads top-down as an outline of the KB."""
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    ids = [r.id for r in _rows(root)]
    assert ids.index("billing") < ids.index("billing.refunds")
    assert ids.index("billing.refunds") < ids.index("billing.refunds.chargebacks")


def test_paths_and_depths_are_absolute_from_the_root(tmp_path: Path) -> None:
    """A row is created once, with its final coordinates, and travels up the
    recursion unchanged — there is no rebase step to get wrong."""
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    by_id = {r.id: r for r in _rows(root)}
    assert (by_id["billing"].path, by_id["billing"].depth) == ("billing", 1)
    assert (by_id["billing.refunds"].path, by_id["billing.refunds"].depth) == ("billing/refunds", 2)
    assert by_id["billing.refunds.chargebacks"].path == "billing/refunds/chargebacks"
    assert by_id["billing.refunds.chargebacks"].depth == 3


def test_every_row_carries_its_long_description(tmp_path: Path) -> None:
    """No depth cap trims it: an index that describes deep folders less well
    routes to them worse, which is the whole failure this replaced."""
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    for r in _rows(root):
        assert r.long.startswith("long for "), r.id


# --- Where the catalog lives ------------------------------------------------


def test_only_the_root_carries_a_catalog(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    assert CATALOG_BEGIN in (root / "compiled.md").read_text(encoding="utf-8")
    for sub in ("billing", "billing/refunds", "auth", "auth/sso/saml"):
        text = (root / sub / "compiled.md").read_text(encoding="utf-8")
        assert CATALOG_BEGIN not in text, sub
        assert "## Catalog" not in text, sub


def test_both_sections_are_machine_delimited(tmp_path: Path) -> None:
    """A source document containing a heading called "Content" cannot be
    mistaken for the section boundary."""
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    rootdoc = (root / "compiled.md").read_text(encoding="utf-8")
    assert CATALOG_BEGIN in rootdoc and CATALOG_END in rootdoc

    billing = (root / "billing" / "compiled.md").read_text(encoding="utf-8")
    assert CONTENT_BEGIN in billing


def test_a_waypoint_writes_frontmatter_and_no_content(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    fm = read_compiled_frontmatter(root / "auth" / "compiled.md")
    assert fm is not None
    assert fm.kind == "node"
    assert fm.long_description == ""
    assert CONTENT_BEGIN not in (root / "auth" / "compiled.md").read_text(encoding="utf-8")


# --- Cost -------------------------------------------------------------------


def test_a_waypoint_costs_no_llm_call(tmp_path: Path) -> None:
    """One call per content-bearing folder, not one per folder."""
    root = _make_tree(tmp_path)
    cfg = CliConfig()
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(root / "build.log")
    logger = build_logger(cfg.log, name=f"test.catalog.calls.{tmp_path.name}")

    seen: list[str] = []

    def counting(cfg_, *, own_content="", **kw):  # noqa: ARG001
        seen.append(own_content)
        return _fake_metadata(cfg_, own_content=own_content)

    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=counting):
        preprocess_tree(root, cfg, logger, force=True)

    folders = [c for c in seen if "Preflight" not in c]  # the probe is not a folder
    # billing, billing/refunds, chargebacks, saml — and neither auth nor auth/sso.
    assert len(folders) == 4
    assert all(c.strip() for c in folders)


def test_token_estimates_are_split(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    rootfm = read_compiled_frontmatter(root / "compiled.md")
    assert rootfm is not None
    # The root here is a pure waypoint: its file is almost entirely catalog.
    assert rootfm.catalog_token_estimate > 0
    assert rootfm.catalog_token_estimate > rootfm.content_token_estimate

    leaf = read_compiled_frontmatter(root / "billing" / "refunds" / "compiled.md")
    assert leaf is not None
    assert leaf.content_token_estimate > 0
    assert leaf.catalog_token_estimate == 0  # no catalog anywhere but the root


# --- Rebuild semantics ------------------------------------------------------


def test_a_skipped_folder_still_contributes_its_row(tmp_path: Path) -> None:
    """`--only` re-renders the root from front-matter without re-summarizing;
    skipping the root's re-emission would leave the catalog stale (§3.4.7)."""
    root = _make_tree(tmp_path)
    _build(root, CliConfig())
    before = {r.id: r.long for r in _rows(root)}

    # Second pass without --force: every folder but the root is skipped.
    _build(root, CliConfig(), force=False)
    after = {r.id: r.long for r in _rows(root)}

    assert after == before


def test_a_description_edited_on_disk_reaches_the_root(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    target = root / "billing" / "refunds" / "compiled.md"
    text = target.read_text(encoding="utf-8").replace(
        "long for Refunds", "edited description for refunds"
    )
    target.write_text(text, encoding="utf-8")

    _build(root, CliConfig(), force=False)
    row = next(r for r in _rows(root) if r.id == "billing.refunds")
    assert row.long == "edited description for refunds"


# --- Table integrity --------------------------------------------------------


@pytest.mark.parametrize(
    "description",
    [
        "Covers a | b | c and the pipe that separates them.",
        "First sentence.\nSecond sentence on a new line.",
    ],
)
def test_a_description_cannot_break_the_table(tmp_path: Path, description: str) -> None:
    """Escaped, never shortened: a table that dropped half a description would
    reintroduce as a rendering detail the loss this design removed."""
    root = tmp_path / "kb"
    (root / "billing").mkdir(parents=True)
    (root / "billing" / "p.md").write_text("# Billing\nText.\n", encoding="utf-8")

    cfg = CliConfig()
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(root / "build.log")
    logger = build_logger(cfg.log, name=f"test.catalog.escape.{tmp_path.name}")

    def meta(cfg_, *, own_content="", **kw):  # noqa: ARG001
        return FolderMetadata(title="Billing", long_description=description)

    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=meta):
        preprocess_tree(root, cfg, logger, force=True)

    rows = _rows(root)
    assert len(rows) == 1
    # Round-trips through the table with its words intact, on one line.
    assert rows[0].long == " ".join(description.split())
