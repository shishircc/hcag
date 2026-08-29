"""Per-file manifest for change detection (§8.4.5).

The manifest table has one row per indexed source file. On a re-run, `rag`
loads this into a dict keyed by ``kb_path`` and compares each candidate's
current content hash against the stored one to decide skip / re-index.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


BLOCK = 64 * 1024


def content_hash(path: Path) -> str:
    """SHA-256 hex digest of a file's bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(BLOCK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def stable_chunk_id(kb_path: str, chunk_index: int, file_hash: str) -> str:
    """Chunk id per §8.5 — sha256(kb_path + "|" + chunk_index + "|" + file_hash).

    Truncated to 32 hex chars — collision-safe for realistic KB sizes.
    """
    h = hashlib.sha256()
    h.update(kb_path.encode("utf-8"))
    h.update(b"|")
    h.update(str(chunk_index).encode("ascii"))
    h.update(b"|")
    h.update(file_hash.encode("ascii"))
    return h.hexdigest()[:32]


@dataclass
class ManifestEntry:
    kb_path: str
    content_hash: str
    bytes: int
    mtime: float
    chunk_count: int
    source_kind: str


def load_manifest_dict(rows: list[dict]) -> dict[str, ManifestEntry]:
    out: dict[str, ManifestEntry] = {}
    for r in rows:
        try:
            out[r["kb_path"]] = ManifestEntry(
                kb_path=r["kb_path"],
                content_hash=r["content_hash"],
                bytes=int(r.get("bytes") or 0),
                mtime=float(r.get("mtime") or 0.0),
                chunk_count=int(r.get("chunk_count") or 0),
                source_kind=str(r.get("source_kind") or ""),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out
