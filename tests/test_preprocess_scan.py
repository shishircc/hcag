"""scan_folder ignores unsupported files per §3.2 and §3.4.6."""

from __future__ import annotations

from pathlib import Path

from hcag.cli.preprocess import scan_folder


def test_scan_ignores_non_md_non_image(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("# hi", encoding="utf-8")
    (tmp_path / "diagram.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / ".DS_Store").write_bytes(b"junk")
    (tmp_path / "source.docx").write_bytes(b"docx-bytes")
    (tmp_path / "README").write_text("editorial note", encoding="utf-8")

    info = scan_folder(tmp_path)

    assert [p.name for p in info.source_md_files] == ["notes.md"]
    assert [p.name for p in info.image_files] == ["diagram.png"]
    assert {p.name for p in info.ignored_files} == {".DS_Store", "source.docx", "README"}


def test_scan_does_not_raise_on_unknown_extensions(tmp_path: Path) -> None:
    (tmp_path / "weird.xyz").write_bytes(b"anything")
    info = scan_folder(tmp_path)
    assert info.source_md_files == []
    assert info.image_files == []
    assert len(info.ignored_files) == 1
