"""Runtime behavior over the flat root catalog (D3a, §2.6, §2.7).

The catalog names every content-bearing folder, so a deep packet resolves in
one hop; a pure waypoint has no row and loads as a header and nothing else.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hcag.cli.metadata_llm import FolderMetadata
from hcag.cli.preprocess import preprocess_tree
from hcag.config import CliConfig
from hcag.logger import build_logger
from hcag.memory import FileSystemMemoryModule, LocalFsStorage, TokenBudget
from hcag.models import CheckAndLoadRequest


def _fake_metadata(cfg, *, own_content="", **kw):  # noqa: ARG001
    first = (own_content.splitlines() or [""])[0].lstrip("# ").strip()
    name = first or "Node"
    return FolderMetadata(
        title=name,
        long_description=f"long for {name}",
    )


def _kb(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    (root / "auth" / "sso" / "saml").mkdir(parents=True)
    (root / "billing").mkdir(parents=True)
    (root / "billing" / "overview.md").write_text("# Billing\nMoney movement.\n", encoding="utf-8")
    (root / "auth" / "sso" / "saml" / "s.md").write_text(
        "# SAML\nCertificate rotation happens every 90 days.\n", encoding="utf-8"
    )
    cfg = CliConfig()
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(root / "build.log")
    logger = build_logger(cfg.log, name=f"test.memory.catalog.{tmp_path.name}")
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_fake_metadata):
        preprocess_tree(root, cfg, logger, force=True)
    return root


def _module(root: Path, **kw) -> FileSystemMemoryModule:
    return FileSystemMemoryModule(
        storage=LocalFsStorage(root), budget=TokenBudget(kw.pop("budget", 10_000)), **kw
    )


def test_bootstrap_catalog_covers_every_depth(tmp_path: Path) -> None:
    catalog = _module(_kb(tmp_path)).get_catalog()

    by_id = {e.id: e for e in catalog.entries}
    # Waypoints hold nothing, so they get no row (D3a) — the tree beneath them
    # is still legible from the deep entry's path.
    assert set(by_id) == {"auth.sso.saml", "billing"}
    # Paths are KB-absolute so the module can resolve any of them directly.
    assert by_id["auth.sso.saml"].path == "auth/sso/saml"
    assert by_id["auth.sso.saml"].depth == 3
    # A parent id is derived from the key, not stored in a column.
    assert by_id["auth.sso.saml"].parent_id == "auth.sso"
    # The injected text is the root's table verbatim, deep entries included.
    assert "auth.sso.saml" in catalog.raw_markdown


def test_deep_leaf_loads_in_one_hop(tmp_path: Path) -> None:
    """No intermediate loads: the leaf id comes straight off the bootstrap catalog."""
    module = _module(_kb(tmp_path))
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="how often do SAML certs rotate",
            requested_packet_ids=["auth.sso.saml"],
            active_packet_ids=[],
        )
    )
    assert delta.errors == []
    assert [p.id for p in delta.loaded] == ["auth.sso.saml"]
    assert delta.active_after == ["auth.sso.saml"]
    body = [b.text for b in delta.loaded[0].content if hasattr(b, "text")]
    assert any("Certificate rotation" in t for t in body)


def test_a_loaded_packet_ships_content_and_no_index(tmp_path: Path) -> None:
    """Nothing has to be elided: only the root has a catalog section, and the
    root is not served as a packet."""
    module = _module(_kb(tmp_path))
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="billing overview",
            requested_packet_ids=["auth", "billing"],
            active_packet_ids=[],
        )
    )
    bodies = {
        p.id: "\n".join(b.text for b in p.content if hasattr(b, "text"))
        for p in delta.loaded
    }
    # `auth` is a pure waypoint: a metadata header and nothing else.
    assert "## Catalog" not in bodies["auth"]
    assert "auth.sso.saml" not in bodies["auth"]
    # `billing` still delivers the content it exists for.
    assert "## Catalog" not in bodies["billing"]
    assert "Money movement" in bodies["billing"]


def test_a_waypoint_is_not_in_the_catalog_but_still_loads_by_id(tmp_path: Path) -> None:
    """It has no row (D3a), so nothing invites the agent to load it — but an id
    that names it resolves rather than erroring."""
    module = _module(_kb(tmp_path))
    assert module.get_catalog().get("auth") is None

    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="auth", requested_packet_ids=["auth"], active_packet_ids=[]
        )
    )
    assert delta.errors == []
    assert delta.loaded[0].id == "auth"


def test_a_waypoint_resolves_even_when_folder_names_contain_dots(tmp_path: Path) -> None:
    """A waypoint has no catalog row, so its path cannot be looked up — and a
    crawled KB's top folder is a hostname, so dotted-id arithmetic cannot derive
    it either. It is found by trimming a known descendant's path instead."""
    root = tmp_path / "kb"
    deep = root / "www.example.gov" / "passes" / "ep"
    deep.mkdir(parents=True)
    (deep / "x.md").write_text("# EP\nEligibility rules.\n", encoding="utf-8")

    cfg = CliConfig()
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(root / "build.log")
    logger = build_logger(cfg.log, name=f"test.memory.dots.{tmp_path.name}")
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_fake_metadata):
        preprocess_tree(root, cfg, logger, force=True)

    module = _module(root)
    waypoint = "www.example.gov.passes"
    assert module.get_catalog().get(waypoint) is None

    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="x", requested_packet_ids=[waypoint], active_packet_ids=[]
        )
    )
    assert delta.errors == []
    assert delta.loaded[0].id == waypoint
    body = "\n".join(b.text for b in delta.loaded[0].content if hasattr(b, "text"))
    assert waypoint in body          # the header names it
    assert "Eligibility rules" not in body   # and it serves no content


def test_an_id_that_names_nothing_is_an_error_not_a_wrong_packet(tmp_path: Path) -> None:
    """Several ancestors of a guessed path have a compiled.md, so a candidate is
    confirmed by reading its id back rather than assumed."""
    module = _module(_kb(tmp_path))
    module.get_catalog()
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="x", requested_packet_ids=["billing.nope"], active_packet_ids=[]
        )
    )
    assert delta.loaded == []
    assert delta.errors[0].reason == "unknown_packet_id"


def test_budget_uses_the_content_estimate_from_frontmatter(tmp_path: Path) -> None:
    """The table carries no token count — a number the model cannot act on is
    noise across several hundred rows — so the module reads it from each
    folder's front-matter at bootstrap (§2.2.1)."""
    root = _kb(tmp_path)
    module = _module(root)
    entry = module.get_catalog().get("billing")
    assert entry is not None

    from hcag.compiled_io import read_compiled_frontmatter

    fm = read_compiled_frontmatter(root / "billing" / "compiled.md")
    assert fm is not None
    assert entry.content_token_estimate == fm.content_token_estimate
    assert entry.budget_tokens == fm.content_token_estimate
    assert entry.kind == fm.kind


def test_stale_catalog_entry_reports_cleanly(tmp_path: Path) -> None:
    """The catalog names a folder whose artifact has since disappeared (§2.8)."""
    root = _kb(tmp_path)
    (root / "auth" / "sso" / "saml" / "compiled.md").unlink()

    delta = _module(root).check_and_load_kb(
        CheckAndLoadRequest(
            context="saml",
            requested_packet_ids=["auth.sso.saml"],
            active_packet_ids=[],
        )
    )
    assert delta.loaded == []
    assert delta.errors
    assert delta.errors[0].reason.startswith("stale_catalog:")
