"""File discovery + exclusion rules + per-format text extraction (§8.2, §8.4.1).

Two exclusion rules per §8.2:
  1. Skip ``compiled.md`` (HCAG artifacts — every folder has one after
     ``hcag preprocess``).
  2. Skip any file inside an ``assets/`` folder that sits alongside a
     ``compiled.md`` — those are folder assets, indirectly indexed via the
     folder body.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal


SourceKind = Literal["markdown", "text", "html", "pdf", "image"]

_MD_EXTS = {".md", ".markdown"}
_TXT_EXTS = {".txt"}
_HTML_EXTS = {".html", ".htm"}
_PDF_EXTS = {".pdf"}
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

_HCAG_ARTIFACT_NAMES = {"compiled.md"}


@dataclass
class Candidate:
    kb_path: str          # POSIX relative path from kb_root
    abs_path: Path
    source_kind: SourceKind
    bytes: int
    mtime: float


@dataclass
class SkipReason:
    kb_path: str
    reason: str           # "packet_md" | "catalog_md" | "hcag_asset" | "unknown_ext"


def classify_extension(ext: str) -> SourceKind | None:
    ext = ext.lower()
    if ext in _MD_EXTS:
        return "markdown"
    if ext in _TXT_EXTS:
        return "text"
    if ext in _HTML_EXTS:
        return "html"
    if ext in _PDF_EXTS:
        return "pdf"
    if ext in _IMG_EXTS:
        return "image"
    return None


def _is_hcag_asset_dir(dir_path: Path) -> bool:
    """A directory named ``assets`` counts as an HCAG asset dir iff its parent
    also contains a ``compiled.md``. That's the layout §2.1 pins."""
    if dir_path.name != "assets":
        return False
    return (dir_path.parent / "compiled.md").is_file()


def walk(
    kb_root: Path,
    *,
    include_images: bool = True,
) -> Iterator[Candidate | SkipReason]:
    """Deterministically walk ``kb_root`` and yield candidates + skip reasons.

    Directories are traversed in sorted order so a re-run against a stable
    tree produces stable row ordering. Yields ``Candidate`` for files that
    should be indexed and ``SkipReason`` for files that matched an exclusion
    rule — the caller decides how to log each.
    """
    kb_root = Path(kb_root).resolve()
    for dirpath, dirnames, filenames in os.walk(kb_root):
        dirnames.sort()
        cur = Path(dirpath)

        # Prune HCAG asset dirs from the walk so we don't even enter them.
        pruned = []
        for d in list(dirnames):
            sub = cur / d
            if _is_hcag_asset_dir(sub):
                # Emit a synthetic skip for the whole dir so it appears in the log.
                pruned.append(d)
        for d in pruned:
            dirnames.remove(d)
            yield SkipReason(
                kb_path=(cur / d).relative_to(kb_root).as_posix() + "/",
                reason="hcag_asset",
            )

        for name in sorted(filenames):
            fp = cur / name
            rel = fp.relative_to(kb_root).as_posix()

            if name in _HCAG_ARTIFACT_NAMES:
                yield SkipReason(kb_path=rel, reason="compiled_md")
                continue

            kind = classify_extension(fp.suffix)
            if kind is None:
                yield SkipReason(kb_path=rel, reason="unknown_ext")
                continue
            if kind == "image" and not include_images:
                yield SkipReason(kb_path=rel, reason="images_disabled")
                continue

            try:
                st = fp.stat()
            except OSError:
                yield SkipReason(kb_path=rel, reason="stat_failed")
                continue

            yield Candidate(
                kb_path=rel,
                abs_path=fp,
                source_kind=kind,
                bytes=st.st_size,
                mtime=st.st_mtime,
            )


# ------ Text extraction -----------------------------------------------------


def extract_text(candidate: Candidate) -> str:
    """Return UTF-8 text for one candidate. Raises on malformed sources."""
    if candidate.source_kind == "markdown" or candidate.source_kind == "text":
        return candidate.abs_path.read_text(encoding="utf-8", errors="replace")

    if candidate.source_kind == "html":
        # Reuse the crawl HTML converter — it strips scripts/styles and yields
        # Markdown, which is exactly what we want to chunk downstream.
        from ..crawl.html_conv import convert_html

        raw = candidate.abs_path.read_bytes()
        base = candidate.abs_path.as_uri()
        return convert_html(raw, base_url=base, doc_basename=candidate.abs_path.stem).markdown

    if candidate.source_kind == "pdf":
        from ..crawl.pdf_conv import convert_pdf

        raw = candidate.abs_path.read_bytes()
        return convert_pdf(raw, doc_basename=candidate.abs_path.stem).markdown

    # Images are handled separately (§8.4.3); text extraction is a no-op here.
    raise ValueError(f"extract_text called on non-text candidate: {candidate.source_kind}")
