"""Render Mermaid .mmd files to PNG via mermaid.ink."""

from __future__ import annotations

import base64
import sys
import urllib.request
from pathlib import Path


def encode(mmd: str) -> str:
    return base64.urlsafe_b64encode(mmd.encode("utf-8")).decode("ascii").rstrip("=")


def render(src: Path, dest: Path) -> None:
    text = src.read_text(encoding="utf-8")
    url = f"https://mermaid.ink/img/{encode(text)}?type=png&bgColor=FFFFFF"
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    dest.write_bytes(data)
    print(f"{src.name} -> {dest.name}  ({len(data):,} bytes)")


def main() -> None:
    diagrams = Path(__file__).parent
    for src in sorted(diagrams.glob("*.mmd")):
        render(src, diagrams / (src.stem + ".png"))


if __name__ == "__main__":
    sys.exit(main())
