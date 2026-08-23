"""Preprocess preserves source .md files and only COPIES images to assets/ (§3.4.3, §3.4.6)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hcag.cli.preprocess import _relocate_images_and_rewrite, process_packet
from hcag.cli.metadata_llm import PacketMetadata
from hcag.config import CliConfig
from hcag.logger import build_logger


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _fake_metadata(cfg, content, max_chars=20000):  # noqa: D401,ARG001
    return PacketMetadata(
        title="Test Packet",
        short_description="Short",
        long_description="Long description here.",
    )


def test_images_are_copied_originals_preserved(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("# Notes\n![diagram](diagram.png)\n", encoding="utf-8")
    (tmp_path / "diagram.png").write_bytes(PNG_BYTES)

    body_sections, copied = _relocate_images_and_rewrite(tmp_path, [tmp_path / "notes.md"])

    # Original image still exists
    assert (tmp_path / "diagram.png").is_file()
    # Copy landed in assets/
    assert (tmp_path / "assets" / "diagram.png").is_file()
    # Reference rewritten
    assert "assets/diagram.png" in body_sections[0][1]
    assert copied == ["diagram.png"]


def test_source_md_files_are_preserved_after_packet_write(tmp_path: Path) -> None:
    src = tmp_path / "policy.md"
    src.write_text("# Policy\nSome content.\n", encoding="utf-8")
    (tmp_path / "chart.png").write_bytes(PNG_BYTES)

    cfg = CliConfig()
    cfg.log.file_path = str(tmp_path / "build.log")
    logger = build_logger(cfg.log, name="test.preprocess")

    with patch("hcag.cli.preprocess.generate_packet_metadata", side_effect=_fake_metadata):
        process_packet(tmp_path, "test.pkt", cfg, logger, force=False)

    # Original .md still there
    assert src.is_file()
    assert "Some content" in src.read_text(encoding="utf-8")

    # Original image still there AND copied under assets/
    assert (tmp_path / "chart.png").is_file()
    assert (tmp_path / "assets" / "chart.png").is_file()

    # Generated packet.md exists
    assert (tmp_path / "packet.md").is_file()
