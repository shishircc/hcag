"""Runtime behavior over a rolled-up root catalog (D3a, §2.6, §2.7)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hcag.cli.metadata_llm import FolderMetadata
from hcag.cli.preprocess import preprocess_tree
from hcag.config import CliConfig
from hcag.logger import build_logger
from hcag.memory import FileSystemMemoryModule, LocalFsStorage, TokenBudget
from hcag.models import CheckAndLoadRequest


def _fake_metadata(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
    first = (own_content.splitlines() or [""])[0].lstrip("# ").strip()
    name = first or "Node"
    return FolderMetadata(
        title=name,
        short_description=f"short for {name}",
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
    logger = build_logger(cfg.log, name="test.memory.rollup")
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
    assert set(by_id) == {"auth", "auth.sso", "auth.sso.saml", "billing"}
    # Paths are KB-absolute so the module can resolve any of them directly.
    assert by_id["auth.sso.saml"].path == "auth/sso/saml"
    assert by_id["auth.sso.saml"].depth == 3
    assert by_id["auth.sso.saml"].parent == "auth.sso"
    # `parent` makes the flat index a reconstructible tree.
    assert [e.id for e in catalog.children_of("auth")] == ["auth.sso"]
    # The injected text is the root's section verbatim, deep entries included.
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


def test_subtopics_elided_for_non_root_packets(tmp_path: Path) -> None:
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
    # `auth` is a pure taxonomy node — with its index elided it carries no
    # catalog text at all, only its metadata header.
    assert "## Sub-topics" not in bodies["auth"]
    assert "auth.sso.saml" not in bodies["auth"]
    # `billing` still delivers the content it exists for.
    assert "## Sub-topics" not in bodies["billing"]
    assert "Money movement" in bodies["billing"]


def test_subtopics_shipped_when_stripping_is_disabled(tmp_path: Path) -> None:
    module = _module(_kb(tmp_path), strip_subtopics_on_load=False)
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="auth branch",
            requested_packet_ids=["auth"],
            active_packet_ids=[],
        )
    )
    body = "\n".join(b.text for b in delta.loaded[0].content if hasattr(b, "text"))
    assert "## Sub-topics" in body
    assert "auth.sso.saml" in body


def test_budget_uses_content_estimate_not_the_catalog_inflated_total(tmp_path: Path) -> None:
    """A node's compiled.md is mostly catalog; only its content occupies budget."""
    root = _kb(tmp_path)
    module = _module(root)
    entry = module.get_catalog().get("auth")
    assert entry is not None
    # The catalog records the content-only figure (§2.2), and that is what the
    # budget is enforced against (§2.5).
    assert entry.budget_tokens == entry.content_token_estimate

    from hcag.compiled_io import read_compiled

    fm, _, _ = read_compiled(root / "auth" / "compiled.md")
    assert fm.catalog_token_estimate > 0
    assert entry.budget_tokens == fm.content_token_estimate
    assert entry.budget_tokens < fm.token_size_estimate


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


def test_legacy_one_level_kb_still_resolves_deep_ids(tmp_path: Path) -> None:
    """A KB built before the roll-up has a one-level root catalog and folder
    names containing dots. The id tail is hung off the longest known ancestor,
    so a direct deep request still resolves without walking the tree."""
    root = tmp_path / "kb"
    branch = root / "www.example.com" / "guides"
    branch.mkdir(parents=True)
    (root / "compiled.md").write_text(
        "<!-- HCAG:COMPILED id=_root -->\n"
        "---\nid: ''\ntitle: R\nshort_description: r\nlong_description: r\n"
        "token_size_estimate: 10\nkind: node\nsource_files: []\n"
        "children: [www.example.com]\n---\n\n"
        "# R\n\n## Sub-topics\n\n"
        "### `www.example.com`\n"
        "- **path**: `www.example.com`\n"
        "- **title**: Site\n- **short**: site\n- **long**: site\n- **tokens**: 20\n",
        encoding="utf-8",
    )
    (root / "www.example.com" / "compiled.md").write_text(
        "<!-- HCAG:COMPILED id=www.example.com -->\n"
        "---\nid: www.example.com\ntitle: Site\nshort_description: site\n"
        "long_description: site\ntoken_size_estimate: 20\nkind: node\n"
        "source_files: []\nchildren: [www.example.com.guides]\n---\n\n# Site\n",
        encoding="utf-8",
    )
    (branch / "compiled.md").write_text(
        "<!-- HCAG:COMPILED id=www.example.com.guides -->\n"
        "---\nid: www.example.com.guides\ntitle: Guides\nshort_description: g\n"
        "long_description: g\ntoken_size_estimate: 30\nkind: leaf\n"
        "source_files: [g.md]\nchildren: []\n---\n\n"
        "# Guides\n\n## Content\n\n<!-- source: g.md -->\nGuide body here.\n",
        encoding="utf-8",
    )

    module = _module(root)
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="guides",
            requested_packet_ids=["www.example.com.guides"],
            active_packet_ids=[],
        )
    )
    assert delta.errors == []
    body = "\n".join(b.text for b in delta.loaded[0].content if hasattr(b, "text"))
    assert "Guide body here" in body
    # Pre-split front-matter: the total is the only estimate, so it is budgeted.
    entry = module._index["www.example.com.guides"].entry
    assert entry.budget_tokens == 30
