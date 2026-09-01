"""Assemble ``Packet`` objects from storage bytes per §2.6.

The runtime ships the body of ``compiled.md`` — front-matter and marker line
stripped — followed by every image under ``assets/``. A short text header
precedes each packet so the LLM can identify what it is looking at from
context alone.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from ..compiled_io import strip_compiled_frontmatter, strip_subtopics_section
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


def assemble_packet(
    entry: CatalogEntry,
    compiled_raw: str,
    assets: list[tuple[str, bytes]],
    *,
    strip_subtopics: bool = False,
) -> Packet:
    """Build the content blocks for one loaded folder.

    When ``strip_subtopics`` is set the folder's ``## Sub-topics`` section is
    dropped (§2.6): because catalogs roll up the whole subtree (D3a), that
    section is a verbatim subset of the root catalog already sitting in the
    agent's system prompt, so shipping it again would duplicate that text
    inside the active set for no navigational gain. What remains is the
    ``## Content`` the packet exists to deliver.
    """
    header = (
        f"--- packet: {entry.id or '_root'} ---\n"
        f"Title: {entry.title}\n"
        f"Short: {entry.short_description}\n"
    )
    body = strip_compiled_frontmatter(compiled_raw)
    if strip_subtopics:
        body = strip_subtopics_section(body)
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
