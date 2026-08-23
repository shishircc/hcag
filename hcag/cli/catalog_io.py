"""Read/write packet.md and catalog.md files (§3.7)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import frontmatter


HCAG_PACKET_MARKER = "<!-- HCAG:PACKET"
HCAG_CATALOG_MARKER = "<!-- HCAG:CATALOG"
HCAG_ROOT_CATALOG_MARKER = "<!-- HCAG:ROOT_CATALOG"


@dataclass
class PacketFrontMatter:
    id: str
    title: str
    short_description: str
    long_description: str
    token_size_estimate: int
    source_files: list[str]


@dataclass
class NodeCatalogFrontMatter:
    node_title: str
    node_short_description: str


@dataclass
class ChildEntry:
    """One entry inside a level's catalog.md — describes an immediate child."""

    id: str
    kind: str  # "packet" | "node" | "mixed"
    path: str
    title: str
    short: str
    long: str
    tokens: int


def is_hcag_generated(path: Path, marker: str) -> bool:
    if not path.is_file():
        return False
    with path.open("r", encoding="utf-8") as f:
        head = f.read(2048)
    return marker in head


def write_packet_md(
    dest: Path,
    fm: PacketFrontMatter,
    body_sections: list[tuple[str, str]],
) -> None:
    """Write packet.md with front-matter, marker, and merged body sections."""
    marker = f"{HCAG_PACKET_MARKER} id={fm.id} -->"
    post = frontmatter.Post(
        content=_render_body(fm, body_sections),
        **{
            "id": fm.id,
            "title": fm.title,
            "short_description": fm.short_description,
            "long_description": fm.long_description,
            "token_size_estimate": fm.token_size_estimate,
            "source_files": fm.source_files,
        },
    )
    text = marker + "\n" + frontmatter.dumps(post) + "\n"
    dest.write_text(text, encoding="utf-8")


def _render_body(fm: PacketFrontMatter, body_sections: list[tuple[str, str]]) -> str:
    parts = [f"# {fm.title}", ""]
    for name, content in body_sections:
        parts.append(f"<!-- source: {name} -->")
        parts.append(content.strip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def read_packet_frontmatter(path: Path) -> PacketFrontMatter | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    # Strip HCAG marker line so frontmatter parses cleanly
    lines = text.splitlines()
    if lines and lines[0].startswith(HCAG_PACKET_MARKER):
        text = "\n".join(lines[1:])
    post = frontmatter.loads(text)
    m = post.metadata
    return PacketFrontMatter(
        id=str(m.get("id", "")),
        title=str(m.get("title", "")),
        short_description=str(m.get("short_description", "")),
        long_description=str(m.get("long_description", "")),
        token_size_estimate=int(m.get("token_size_estimate", 0) or 0),
        source_files=list(m.get("source_files", []) or []),
    )


def write_node_catalog_md(
    dest: Path,
    node_id: str,
    node_meta: NodeCatalogFrontMatter,
    children: list[ChildEntry],
) -> None:
    marker = f"{HCAG_CATALOG_MARKER} level={node_id} -->"
    post = frontmatter.Post(
        content=_render_catalog_body(node_meta, children),
        **{
            "node_title": node_meta.node_title,
            "node_short_description": node_meta.node_short_description,
        },
    )
    text = marker + "\n" + frontmatter.dumps(post) + "\n"
    dest.write_text(text, encoding="utf-8")


def _render_catalog_body(node_meta: NodeCatalogFrontMatter, children: list[ChildEntry]) -> str:
    parts = [f"# {node_meta.node_title}", "", node_meta.node_short_description.strip(), "", "## Children", ""]
    for c in children:
        parts.append(f"### `{c.id}`")
        parts.append(f"- **kind**: {c.kind}")
        parts.append(f"- **path**: `{c.path}`")
        parts.append(f"- **title**: {c.title}")
        parts.append(f"- **short**: {c.short}")
        parts.append(f"- **long**: {c.long}")
        parts.append(f"- **tokens**: {c.tokens}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def read_node_catalog(path: Path) -> tuple[NodeCatalogFrontMatter, list[ChildEntry]] | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].startswith(HCAG_CATALOG_MARKER):
        text = "\n".join(lines[1:])
    post = frontmatter.loads(text)
    meta = NodeCatalogFrontMatter(
        node_title=str(post.metadata.get("node_title", "")),
        node_short_description=str(post.metadata.get("node_short_description", "")),
    )
    children = _parse_children(post.content)
    return meta, children


def _parse_children(body: str) -> list[ChildEntry]:
    import re

    children: list[ChildEntry] = []
    blocks = re.split(r"^###\s+`([^`]+)`\s*$", body, flags=re.MULTILINE)
    # blocks alternates: [preamble, id1, body1, id2, body2, ...]
    for i in range(1, len(blocks), 2):
        pid = blocks[i]
        block = blocks[i + 1] if i + 1 < len(blocks) else ""
        fields: dict[str, str] = {}
        for fm in re.finditer(r"^-\s*\*\*(?P<k>[^*]+)\*\*\s*:\s*(?P<v>.+?)\s*$", block, re.MULTILINE):
            fields[fm.group("k").strip().lower()] = fm.group("v").strip()
        try:
            tokens = int(fields.get("tokens", "0"))
        except ValueError:
            tokens = 0
        children.append(
            ChildEntry(
                id=pid,
                kind=fields.get("kind", "packet"),
                path=fields.get("path", "").strip("`/ ").rstrip("/"),
                title=fields.get("title", pid),
                short=fields.get("short", ""),
                long=fields.get("long", ""),
                tokens=tokens,
            )
        )
    return children
