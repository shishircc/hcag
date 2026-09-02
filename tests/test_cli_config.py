"""`hcag.toml` parsing — `[compiled] root_id` and its legacy top-level form (§3.6)."""

from __future__ import annotations

from pathlib import Path

from hcag.config import CliConfig, load_cli_config


def _cfg(tmp_path: Path, toml: str) -> CliConfig:
    p = tmp_path / "hcag.toml"
    p.write_text(toml, encoding="utf-8")
    return load_cli_config(p)


def test_root_id_defaults_to_empty(tmp_path: Path) -> None:
    """The root's ID is the empty string unless a consumer needs otherwise (§3.4.5)."""
    assert _cfg(tmp_path, "").root_id == ""


def test_root_id_read_from_compiled_table(tmp_path: Path) -> None:
    """`[compiled] root_id` is the documented home for the setting (§3.6)."""
    cfg = _cfg(tmp_path, '[compiled]\nroot_id = "_root"\n')
    assert cfg.root_id == "_root"
    assert cfg.compiled.root_id == "_root"


def test_legacy_top_level_root_id_still_honored(tmp_path: Path) -> None:
    """Configs written against the older flat shape keep working."""
    cfg = _cfg(tmp_path, 'root_id = "_legacy"\n')
    assert cfg.root_id == "_legacy"
    # Mirrored so either accessor answers the same question.
    assert cfg.compiled.root_id == "_legacy"


def test_compiled_table_wins_over_top_level(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, 'root_id = "_legacy"\n[compiled]\nroot_id = "_root"\n')
    assert cfg.root_id == "_root"


def test_compiled_table_can_set_root_id_back_to_empty(tmp_path: Path) -> None:
    """An explicit empty `[compiled] root_id` is a real value, not "unset"."""
    cfg = _cfg(tmp_path, 'root_id = "_legacy"\n[compiled]\nroot_id = ""\n')
    assert cfg.root_id == ""


def test_sample_config_parses() -> None:
    """The shipped example is a valid config and uses the documented table."""
    repo_root = Path(__file__).resolve().parent.parent
    cfg = load_cli_config(repo_root / "examples" / "kb-example" / "hcag.toml")
    assert cfg.root_id == "_root"


def test_root_id_reaches_the_generated_artifacts(tmp_path: Path) -> None:
    """Whatever `root_id` resolves to is what lands in the root's front-matter,
    its HCAG marker, and the `parent` of every top-level catalog record."""
    from unittest.mock import patch

    from hcag.cli.metadata_llm import FolderMetadata
    from hcag.cli.preprocess import preprocess_tree
    from hcag.compiled_io import read_compiled
    from hcag.logger import build_logger

    def _fake(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        return FolderMetadata(title="T", short_description="s", long_description="l")

    kb = tmp_path / "kb"
    (kb / "billing").mkdir(parents=True)
    (kb / "billing" / "x.md").write_text("# Billing\nbody\n", encoding="utf-8")

    cfg = _cfg(tmp_path, '[compiled]\nroot_id = "_root"\n')
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(tmp_path / "build.log")
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_fake):
        preprocess_tree(kb, cfg, build_logger(cfg.log, name="test.rootid"), force=True)

    fm, records, _ = read_compiled(kb / "compiled.md")
    assert fm.id == "_root"
    assert (kb / "compiled.md").read_text(encoding="utf-8").startswith(
        "<!-- HCAG:COMPILED id=_root -->"
    )
    # A configured `_root` survives the write/parse round trip rather than
    # being mistaken for "the root has no id".
    assert records[0].parent == "_root"


def test_top_level_branches_found_regardless_of_root_id(tmp_path: Path) -> None:
    """`root_children()` keys off depth, so it works whether the KB left the
    root id empty (rendered `_root`) or set one explicitly."""
    from unittest.mock import patch

    from hcag.cli.metadata_llm import FolderMetadata
    from hcag.cli.preprocess import preprocess_tree
    from hcag.logger import build_logger
    from hcag.memory import FileSystemMemoryModule, LocalFsStorage, TokenBudget

    def _fake(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        return FolderMetadata(title="T", short_description="s", long_description="l")

    for i, toml in enumerate(['[compiled]\nroot_id = "_root"\n', "", 'root_id = "kb"\n']):
        kb = tmp_path / f"kb{i}"
        (kb / "billing" / "refunds").mkdir(parents=True)
        (kb / "billing" / "refunds" / "x.md").write_text("# R\nbody\n", encoding="utf-8")

        cfg = _cfg(tmp_path, toml)
        cfg.tokenizer.kind = "rough"
        cfg.log.file_path = str(tmp_path / "build.log")
        with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_fake):
            preprocess_tree(kb, cfg, build_logger(cfg.log, name="test.rootkids"), force=True)

        catalog = FileSystemMemoryModule(
            storage=LocalFsStorage(kb), budget=TokenBudget(10_000)
        ).get_catalog()
        assert [e.id for e in catalog.root_children()] == ["billing"], toml
        # The deeper record is still reachable and correctly parented.
        assert [e.id for e in catalog.children_of("billing")] == ["billing.refunds"]
