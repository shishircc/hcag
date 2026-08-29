"""Scan a normalized KB for packets, paragraphs, and image assets (§6.2).

Reads only the artifacts the runtime memory module serves: `packet.md` files
and images under each packet's `assets/` folder. Source `.md` files outside
`packet.md` are ignored — `evalgen` grounds its questions in exactly the
content the agent will retrieve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..cli.catalog_io import read_packet_frontmatter
from ..logger import HcagLogger


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

# A paragraph is a block of text separated by one or more blank lines.
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")

# Drop HCAG-internal markers and per-source separators that appear inside
# packet.md bodies (see catalog_io._render_body). They aren't prose the
# reader would answer questions about.
_SOURCE_MARKER_RE = re.compile(r"^<!--\s*source:.*?-->\s*$", re.MULTILINE)
_HR_RE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)


@dataclass
class PacketRecord:
    """One packet, ready to sample paragraphs and images from."""

    id: str
    title: str
    short_description: str
    long_description: str
    path: Path
    body: str
    paragraphs: list[str]
    assets: list[Path] = field(default_factory=list)

    @property
    def has_images(self) -> bool:
        return bool(self.assets)


def _strip_body(text: str) -> str:
    """Remove packet-body plumbing (source markers, HR separators) so the
    remaining prose is what a human reader would parse."""
    text = _SOURCE_MARKER_RE.sub("", text)
    text = _HR_RE.sub("", text)
    return text


def _split_paragraphs(body: str, min_chars: int) -> list[str]:
    """Split packet body into paragraph-sized units.

    A "paragraph" here is a block of text separated by blank lines. Blocks
    shorter than `min_chars` (e.g., a stray heading) are dropped so the
    LLM has enough substance to build a reasoning question from.
    """
    paragraphs: list[str] = []
    for block in _PARAGRAPH_SPLIT_RE.split(body):
        stripped = block.strip()
        if len(stripped) >= min_chars:
            paragraphs.append(stripped)
    return paragraphs


def _load_packet_body(packet_md: Path) -> str:
    """Return the packet.md body with front-matter and HCAG marker stripped."""
    text = packet_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Drop the HCAG marker line if present
    if lines and lines[0].startswith("<!-- HCAG:PACKET"):
        lines = lines[1:]
    text = "\n".join(lines)
    # Strip YAML front-matter (--- ... ---)
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return _strip_body(text).strip()


def _list_assets(assets_dir: Path) -> list[Path]:
    if not assets_dir.is_dir():
        return []
    out: list[Path] = []
    for entry in sorted(assets_dir.iterdir()):
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXTS:
            out.append(entry)
    return out


def scan_kb(root: Path, paragraph_min_chars: int, logger: HcagLogger | None = None) -> list[PacketRecord]:
    """Walk `root` and return one `PacketRecord` per `packet.md` found.

    Packets with an empty body or fewer than one usable paragraph are dropped
    with a WARN — they cannot ground any of the reasoning-based question kinds.
    """
    if not root.is_dir():
        return []

    packets: list[PacketRecord] = []
    for packet_md in sorted(root.rglob("packet.md")):
        fm = read_packet_frontmatter(packet_md)
        if fm is None or not fm.id:
            if logger is not None:
                logger.warn("evalgen.scan.skip_packet", path=str(packet_md), reason="no_frontmatter")
            continue
        body = _load_packet_body(packet_md)
        paragraphs = _split_paragraphs(body, paragraph_min_chars)
        if not paragraphs:
            if logger is not None:
                logger.warn("evalgen.scan.skip_packet", id=fm.id, path=str(packet_md), reason="no_paragraphs")
            continue
        assets = _list_assets(packet_md.parent / "assets")
        packets.append(
            PacketRecord(
                id=fm.id,
                title=fm.title,
                short_description=fm.short_description,
                long_description=fm.long_description,
                path=packet_md,
                body=body,
                paragraphs=paragraphs,
                assets=assets,
            )
        )
    return packets


def taxonomy_prefix(packet_id: str) -> str:
    """Parent taxonomy id — everything before the last dot.

    Used by `hard-1` pair selection (§6.4.4) to bias toward packets that
    share a taxonomy parent (siblings) when a root catalog is present.
    """
    if "." not in packet_id:
        return ""
    return packet_id.rsplit(".", 1)[0]
