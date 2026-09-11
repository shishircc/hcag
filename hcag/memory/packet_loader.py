"""Assemble ``Packet`` objects from storage bytes per §2.6.

The runtime ships a folder's ``## Content`` section followed by every image
under ``assets/``. A short text header precedes each packet so the LLM can
identify what it is looking at from context alone.

Nothing has to be elided on the way out. Under the subtree roll-up every packet
carried its own index and this module stripped it before serving; now only the
root has a catalog section and the root is not served as a packet (D3a).
"""

from __future__ import annotations

from pathlib import PurePosixPath

from ..compiled_io import extract_content_section, strip_compiled_frontmatter
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
) -> Packet:
    """Build the content blocks for one loaded folder.

    The header names the packet and nothing more. It used to carry a one-line
    summary; a generated description sitting immediately above the source it
    describes is something a model can quote in preference to the text itself,
    and the agent already holds every folder's description in the catalog.

    A pure taxonomy node has no ``## Content``, so it loads as a header and
    nothing else — the expected shape for a folder that holds no knowledge.
    """
    header = (
        f"--- packet: {entry.id or '_root'} ---\n"
        f"Title: {entry.title}\n"
        f"Kind: {entry.kind}\n"
    )
    body = extract_content_section(strip_compiled_frontmatter(compiled_raw))
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
