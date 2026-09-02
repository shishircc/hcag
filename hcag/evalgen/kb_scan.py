"""Scan a normalized KB for folders, paragraphs, and image assets (§6.2).

Reads only the artifacts the runtime memory module serves: each folder's
``compiled.md`` and the images under its ``assets/`` directory. Source `.md`
files outside ``compiled.md`` are ignored — ``evalgen`` grounds its questions
in exactly the content the agent will retrieve. Paragraphs come from the
``## Content`` section of ``compiled.md`` (folders that are pure taxonomy
nodes have no ``## Content`` and are skipped for paragraph-grounded question
kinds).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..compiled_io import read_compiled_frontmatter
from ..logger import HcagLogger


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")

# Drop compiled.md plumbing that isn't reader-facing prose.
_SOURCE_MARKER_RE = re.compile(r"^<!--\s*source:.*?-->\s*$", re.MULTILINE)
_HR_RE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)

_CONTENT_HEADER_RE = re.compile(r"^##\s+Content\s*$", re.MULTILINE)
_SUBTOPICS_HEADER_RE = re.compile(r"^##\s+Sub-topics\s*$", re.MULTILINE)


@dataclass
class PacketRecord:
    """One folder, ready to sample paragraphs and images from."""

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
    text = _SOURCE_MARKER_RE.sub("", text)
    text = _HR_RE.sub("", text)
    return text


_TABLE_LINE = re.compile(r"^\s*\|")
_DELIM_LINE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")


def _unwrap_single_cell(block: str) -> str:
    """Return `block` as prose if it is one line fenced as a single table cell.

    PDF extraction (§4.4.2) sometimes emits a whole logical row as one cell, so
    a paragraph of real prose arrives as `| Eligible job titles … Job duties …
    |`. It is not a table and not a fragment of one — there is no delimiter row
    and no second cell — but it *looks* like a broken table row, and a question
    grounded in it would inherit the confusion.

    Only the unambiguous case is unwrapped: a single line, no delimiter, no
    internal `|`. Anything with real cell boundaries is left alone, because
    guessing where a row's columns were is how a table becomes wrong rather
    than merely ugly.
    """
    line = block.strip()
    if "\n" in line or not line.startswith("|") or not line.endswith("|"):
        return block
    inner = line[1:-1]
    if "|" in inner or _DELIM_LINE.match(line):
        return block
    return inner.strip()


def _split_paragraphs(body: str, min_chars: int) -> list[str]:
    """Split into grounding units.

    Blank-line splitting is right for prose. The one repair applied on top is
    `_unwrap_single_cell`, for the pipe-fenced paragraphs PDF extraction
    produces.

    Multi-row tables are left exactly as they are: they arrive from
    `pymupdf4llm` with their header row attached, and on the corpus this was
    measured against every headerless table block was a single-cell paragraph
    rather than a continuation. Reattaching headers to genuine continuations
    would be speculative machinery for a case that does not occur here.
    """
    paragraphs: list[str] = []
    for block in _PARAGRAPH_SPLIT_RE.split(body):
        stripped = _unwrap_single_cell(block.strip())
        if len(stripped) >= min_chars:
            paragraphs.append(stripped)
    return paragraphs


def _load_content_section(compiled_md: Path) -> str:
    """Return just the ``## Content`` section of a compiled.md, marker + FM stripped."""
    text = compiled_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].startswith("<!-- HCAG:COMPILED"):
        lines = lines[1:]
    text = "\n".join(lines)
    # Strip YAML front-matter (--- ... ---)
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    # Extract only the ## Content section — Sub-topics is catalog, not prose.
    m = _CONTENT_HEADER_RE.search(text)
    if m is None:
        return ""
    start = m.end()
    end_match = _SUBTOPICS_HEADER_RE.search(text, start)
    end = end_match.start() if end_match else len(text)
    return _strip_body(text[start:end]).strip()


def _list_assets(assets_dir: Path) -> list[Path]:
    if not assets_dir.is_dir():
        return []
    out: list[Path] = []
    for entry in sorted(assets_dir.iterdir()):
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXTS:
            out.append(entry)
    return out


def scan_kb(root: Path, paragraph_min_chars: int, logger: HcagLogger | None = None) -> list[PacketRecord]:
    """Walk `root` and return one `PacketRecord` per non-empty ``compiled.md`` found.

    Folders whose compiled.md has no ``## Content`` section (pure taxonomy
    nodes) are dropped — they cannot ground any of the reasoning-based
    question kinds. Same for folders whose Content is shorter than
    ``paragraph_min_chars``.
    """
    if not root.is_dir():
        return []

    packets: list[PacketRecord] = []
    for compiled_md in sorted(root.rglob("compiled.md")):
        fm = read_compiled_frontmatter(compiled_md)
        if fm is None:
            if logger is not None:
                logger.warn("evalgen.scan.skip_folder", path=str(compiled_md), reason="no_frontmatter")
            continue
        if not fm.id:
            # Root folder has an empty id by default; skip it for question generation.
            if logger is not None:
                logger.info("evalgen.scan.skip_root", path=str(compiled_md))
            continue
        body = _load_content_section(compiled_md)
        paragraphs = _split_paragraphs(body, paragraph_min_chars)
        if not paragraphs:
            if logger is not None:
                logger.warn("evalgen.scan.skip_folder", id=fm.id, path=str(compiled_md), reason="no_paragraphs")
            continue
        assets = _list_assets(compiled_md.parent / "assets")
        packets.append(
            PacketRecord(
                id=fm.id,
                title=fm.title,
                short_description=fm.short_description,
                long_description=fm.long_description,
                path=compiled_md,
                body=body,
                paragraphs=paragraphs,
                assets=assets,
            )
        )
    return packets


def taxonomy_prefix(packet_id: str) -> str:
    """Parent taxonomy id — everything before the last dot.

    Used by `hard-1` pair selection (§6.4.4) to bias toward packets that
    share a taxonomy parent (siblings).
    """
    if "." not in packet_id:
        return ""
    return packet_id.rsplit(".", 1)[0]
