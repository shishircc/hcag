"""Assemble Packet objects from storage bytes per §2.6."""

from __future__ import annotations

from pathlib import PurePosixPath

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


def assemble_packet(entry: CatalogEntry, markdown: str, assets: list[tuple[str, bytes]]) -> Packet:
    header = (
        f"--- packet: {entry.id} ---\n"
        f"Title: {entry.title}\n"
        f"Short: {entry.short_description}\n"
    )
    blocks: list[TextBlock | ImageBlock] = [
        TextBlock(text=header),
        TextBlock(text=markdown),
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
