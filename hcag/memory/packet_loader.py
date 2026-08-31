"""Assemble ``Packet`` objects from storage bytes per §2.6.

The runtime ships the body of ``compiled.md`` — front-matter and marker line
stripped — followed by every image under ``assets/``. A short text header
precedes each packet so the LLM can identify what it is looking at from
context alone.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from ..compiled_io import HCAG_COMPILED_MARKER
from ..models import CatalogEntry, ImageBlock, Packet, TextBlock


_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def _mime_for(path: str) -> str:
    ext = PurePosixPath(path).suffix.lower()
    return _MIME_BY_EXT.get(ext, "application/octet-stream")


def strip_compiled_frontmatter(raw: str) -> str:
    """Return the body of a compiled.md — marker + YAML front-matter stripped.

    Preserves the `## Sub-topics` and `## Content` sections verbatim so the
    LLM sees the same catalog rendering downstream code produced.
    """
    text = raw
    lines = text.splitlines()
    # Strip HCAG marker line, if present.
    if lines and lines[0].startswith(HCAG_COMPILED_MARKER):
        lines = lines[1:]
    # Strip YAML front-matter block delimited by `---` fences.
    if lines and lines[0].strip() == "---":
        # find closing '---'
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1 :]
                break
    return "\n".join(lines).lstrip("\n")


def assemble_packet(
    entry: CatalogEntry,
    compiled_raw: str,
    assets: list[tuple[str, bytes]],
) -> Packet:
    header = (
        f"--- packet: {entry.id or '_root'} ---\n"
        f"Title: {entry.title}\n"
        f"Short: {entry.short_description}\n"
    )
    body = strip_compiled_frontmatter(compiled_raw)
    blocks: list[TextBlock | ImageBlock] = [
        TextBlock(text=header),
        TextBlock(text=body),
    ]
    for path, data in assets:
        blocks.append(
            ImageBlock(
                data=data,
                mime_type=_mime_for(path),
                filename=PurePosixPath(path).name,
            )
        )
    return Packet(id=entry.id, title=entry.title, content=blocks)
