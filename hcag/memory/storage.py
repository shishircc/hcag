"""KB backing-store abstraction (§2.1, §2.9.2).

Substitutes for LocalFsStorage (S3, GitVersionedStorage, RemoteHttpStorage,
etc.) plug in without touching the memory module — only this file speaks the
on-disk contract.

The KB layout (§2.1) is one ``compiled.md`` per folder, optionally alongside
an ``assets/`` subdirectory of images. The root folder's ``compiled.md`` is
what the memory module injects at bootstrap; deeper folders' ``compiled.md``
files are read on demand as the agent drills down.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class KBStorage(Protocol):
    def read_compiled(self, path: str) -> str: ...
    def has_compiled(self, path: str) -> bool: ...
    def list_assets(self, path: str) -> list[str]: ...
    def read_asset(self, path: str) -> bytes: ...


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
_COMPILED_NAME = "compiled.md"


class LocalFsStorage:
    def __init__(self, kb_root: str | Path) -> None:
        self.kb_root = Path(kb_root).expanduser().resolve()
        if not self.kb_root.is_dir():
            raise FileNotFoundError(f"KB root does not exist: {self.kb_root}")

    def _resolve(self, path: str) -> Path:
        """Resolve a KB-relative path (POSIX, empty = root) to an absolute Path."""
        p = (path or "").strip("/")
        return self.kb_root if not p else self.kb_root / p

    def read_compiled(self, path: str = "") -> str:
        """Read a folder's ``compiled.md``. ``path=""`` is the root."""
        target = self._resolve(path) / _COMPILED_NAME
        if not target.is_file():
            raise FileNotFoundError(f"compiled.md missing: {target}")
        return target.read_text(encoding="utf-8")

    def has_compiled(self, path: str = "") -> bool:
        return (self._resolve(path) / _COMPILED_NAME).is_file()

    def list_assets(self, path: str) -> list[str]:
        assets_dir = self._resolve(path) / "assets"
        if not assets_dir.is_dir():
            return []
        entries = sorted(
            p for p in assets_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
        )
        return [str(p.relative_to(self.kb_root)) for p in entries]

    def read_asset(self, path: str) -> bytes:
        return (self.kb_root / path).read_bytes()
