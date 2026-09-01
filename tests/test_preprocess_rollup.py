"""Catalogs roll up the whole subtree, not one level (D3a, §3.4.1, §3.4.4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hcag.cli.metadata_llm import FolderMetadata
from hcag.cli.preprocess import preprocess_tree
from hcag.compiled_io import read_compiled
from hcag.config import CliConfig
from hcag.logger import build_logger


def _fake_metadata(cfg, *, own_content="", children_longs=None, max_content_chars=20000, max_child_chars=1200):  # noqa: ARG001
    """Title derived from the folder's first heading so records stay identifiable."""
    first = (own_content.splitlines() or [""])[0].lstrip("# ").strip()
    name = first or "Node"
    return FolderMetadata(
        title=name,
        short_description=f"short for {name}",
        long_description=f"long for {name}",
    )


def _build(root: Path, cfg: CliConfig) -> None:
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(root / "build.log")
    logger = build_logger(cfg.log, name="test.rollup")
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_fake_metadata):
        preprocess_tree(root, cfg, logger, force=True)


def _make_tree(tmp_path: Path) -> Path:
    """root ─ billing ─ refunds ─ chargebacks  (3 levels deep)
              auth ─ sso ─ saml
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


def test_root_catalog_indexes_every_folder_at_every_depth(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    fm, records, _ = read_compiled(root / "compiled.md")
    by_id = {r.id: r for r in records}

    # Every folder in the KB, not just the two top-level branches.
    assert set(by_id) == {
        "auth",
        "auth.sso",
        "auth.sso.saml",
        "billing",
        "billing.refunds",
        "billing.refunds.chargebacks",
    }
    # `children` stays immediate-only; `descendants` counts the whole subtree.
    assert fm.children == ["auth", "billing"]
    assert fm.descendants == 6
    assert fm.subtree_depth == 3


def test_records_are_rebased_against_the_catalog_owner(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    root_recs = {r.id: r for r in read_compiled(root / "compiled.md")[1]}
    mid_recs = {r.id: r for r in read_compiled(root / "billing" / "compiled.md")[1]}

    deep = "billing.refunds.chargebacks"
    # depth and path are relative to whoever owns the catalog...
    assert (root_recs[deep].depth, root_recs[deep].path) == (3, "billing/refunds/chargebacks")
    assert (mid_recs[deep].depth, mid_recs[deep].path) == (2, "refunds/chargebacks")
    # ...while id and parent are absolute, so an id read from the root catalog
    # is usable verbatim (§3.4.5).
    assert root_recs[deep].parent == mid_recs[deep].parent == "billing.refunds"
    assert root_recs[deep].kind == "leaf"
    assert root_recs["billing"].kind == "mixed"
    assert root_recs["auth"].kind == "node"


def test_records_are_dfs_pre_order(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())
    ids = [r.id for r in read_compiled(root / "compiled.md")[1]]
    # Each folder is immediately followed by its own subtree; siblings sorted.
    assert ids == [
        "auth",
        "auth.sso",
        "auth.sso.saml",
        "billing",
        "billing.refunds",
        "billing.refunds.chargebacks",
    ]


def test_long_description_only_within_long_depth(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())  # long_depth defaults to 1

    by_id = {r.id: r for r in read_compiled(root / "compiled.md")[1]}
    assert by_id["billing"].long  # depth 1 keeps `long`
    assert not by_id["billing.refunds"].long  # depth 2 drops to `short` only
    assert by_id["billing.refunds"].short  # ...but `short` always survives

    # A record trimmed in one ancestor still carries `long` where it is shallow.
    mid = {r.id: r for r in read_compiled(root / "billing" / "compiled.md")[1]}
    assert mid["billing.refunds"].long


def test_max_depth_caps_the_rollup(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    cfg = CliConfig()
    cfg.catalog.max_depth = 2
    _build(root, cfg)

    fm, records, _ = read_compiled(root / "compiled.md")
    assert {r.id for r in records} == {"auth", "auth.sso", "billing", "billing.refunds"}
    assert max(r.depth for r in records) == 2
    assert fm.descendants == 4
    # subtree_depth still reports the true tree, not the rendered slice.
    assert fm.subtree_depth == 3


def test_include_tree_toggles_the_outline(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    cfg = CliConfig()
    cfg.catalog.include_tree = False
    _build(root, cfg)

    text = (root / "compiled.md").read_text(encoding="utf-8")
    assert "#### Tree" not in text
    # Entries themselves are unaffected.
    assert "#### `billing.refunds.chargebacks`" in text


def test_token_estimates_are_split(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    fm, _, _ = read_compiled(root / "billing" / "compiled.md")
    assert fm.catalog_token_estimate > 0     # billing indexes two descendants
    assert fm.content_token_estimate > 0     # ...and has its own overview.md
    assert fm.token_size_estimate == fm.content_token_estimate + fm.catalog_token_estimate

    leaf_fm, leaf_records, _ = read_compiled(
        root / "billing" / "refunds" / "chargebacks" / "compiled.md"
    )
    assert leaf_records == []                # a leaf indexes nothing
    assert leaf_fm.catalog_token_estimate == 0
    assert leaf_fm.descendants == 0


def test_only_reemits_ancestors_so_the_root_index_stays_current(tmp_path: Path) -> None:
    root = _make_tree(tmp_path)
    _build(root, CliConfig())

    # Add a new leaf deep in one branch, then rebuild just that branch.
    new_leaf = root / "billing" / "refunds" / "disputes"
    new_leaf.mkdir()
    (new_leaf / "d.md").write_text("# Disputes\nDispute handling.\n", encoding="utf-8")

    cfg = CliConfig()
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(root / "build.log")
    logger = build_logger(cfg.log, name="test.rollup.only")
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_fake_metadata):
        preprocess_tree(root, cfg, logger, force=True, only=root / "billing" / "refunds")

    # The root catalog — three levels above the edit — knows about it.
    root_recs = {r.id: r for r in read_compiled(root / "compiled.md")[1]}
    assert "billing.refunds.disputes" in root_recs
    assert root_recs["billing.refunds.disputes"].depth == 3
    assert root_recs["billing.refunds.disputes"].path == "billing/refunds/disputes"
    # Untouched branches survive the partial rebuild.
    assert "auth.sso.saml" in root_recs
