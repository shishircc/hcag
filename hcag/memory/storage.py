"""KB backing-store abstraction. LocalFsStorage is the default; substitute for
S3, GitVersionedStorage, RemoteHttpStorage, etc. without touching anything else
(see §2.9.2 extension points).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class KBStorage(Protocol):
    def read_catalog(self) -> str: ...
    def read_packet_markdown(self, path: str) -> str: ...
    def list_assets(self, path: str) -> list[str]: ...
    def read_asset(self, path: str) -> bytes: ...


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


class LocalFsStorage:
    def __init__(self, kb_root: str | Path) -> None:
        self.kb_root = Path(kb_root).expanduser().resolve()
        if not self.kb_root.is_dir():
            raise FileNotFoundError(f"KB root does not exist: {self.kb_root}")

    def read_catalog(self) -> str:
        catalog = self.kb_root / "catalog.md"
        if not catalog.is_file():
            raise FileNotFoundError(f"catalog.md missing at KB root: {catalog}")
        return catalog.read_text(encoding="utf-8")

    def read_packet_markdown(self, path: str) -> str:
        packet_md = self.kb_root / path / "packet.md"
        if not packet_md.is_file():
            raise FileNotFoundError(f"packet.md missing: {packet_md}")
        return packet_md.read_text(encoding="utf-8")

    def list_assets(self, path: str) -> list[str]:
        assets_dir = self.kb_root / path / "assets"
        if not assets_dir.is_dir():
            return []
        entries = sorted(
            p for p in assets_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
        )
        return [str(p.relative_to(self.kb_root)) for p in entries]

    def read_asset(self, path: str) -> bytes:
        return (self.kb_root / path).read_bytes()
