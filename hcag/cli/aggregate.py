"""`hcag aggregate` — top-down assembly of the root catalog.md (§3.5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..logger import HcagLogger
from .catalog_io import HCAG_ROOT_CATALOG_MARKER, ChildEntry, read_node_catalog, read_packet_frontmatter


@dataclass
class TaxonomyNode:
    id: str
    title: str
    short: str
    path: str
    children: list["TaxonomyNode"] = field(default_factory=list)
    packet: "PacketRecord | None" = None  # a mixed folder carries both


@dataclass
class PacketRecord:
    id: str
    path: str
    title: str
    short: str
    long: str
    tokens: int
    breadcrumb: list[str]


def _build_tree(root: Path, folder: Path, breadcrumb: list[str]) -> TaxonomyNode:
    catalog_path = folder / "catalog.md"
    packet_path = folder / "packet.md"

    node_meta, child_entries = (None, [])
    cat = read_node_catalog(catalog_path)
    if cat is not None:
        node_meta, child_entries = cat

    packet_fm = read_packet_frontmatter(packet_path) if packet_path.is_file() else None

    node = TaxonomyNode(
        id=_id_for_folder(root, folder, is_node=True),
        title=(node_meta.node_title if node_meta else folder.name),
        short=(node_meta.node_short_description if node_meta else ""),
        path=str(folder.relative_to(root)) if folder != root else "",
    )

    if packet_fm is not None:
        node.packet = PacketRecord(
            id=packet_fm.id,
            path=str(folder.relative_to(root)) if folder != root else "",
            title=packet_fm.title,
            short=packet_fm.short_description,
            long=packet_fm.long_description,
            tokens=packet_fm.token_size_estimate,
            breadcrumb=breadcrumb + [node.title],
        )

    # Recurse into children directories
    for entry in sorted(folder.iterdir()):
        if entry.is_dir() and entry.name != "assets":
            sub = _build_tree(root, entry, breadcrumb + [node.title])
            node.children.append(sub)

    return node


def _id_for_folder(root: Path, folder: Path, is_node: bool) -> str:
    if folder == root:
        return "root"
    return ".".join(folder.relative_to(root).parts)


def _flatten_packets(node: TaxonomyNode) -> list[PacketRecord]:
    out: list[PacketRecord] = []
    if node.packet is not None:
        out.append(node.packet)
    for child in node.children:
        out.extend(_flatten_packets(child))
    return out


def _render_overview(node: TaxonomyNode, depth: int = 0) -> list[str]:
    lines: list[str] = []
    prefix = "  " * depth + "- "
    if node.id == "root":
        # Skip the root line itself; render children as top-level
        for child in node.children:
            lines.extend(_render_overview(child, 0))
        return lines
    label = f"**{node.id}** — {node.short}" if node.short else f"**{node.id}**"
    lines.append(prefix + label)
    if node.packet is not None and node.packet.id != node.id:
        pkt_prefix = "  " * (depth + 1) + "- "
        lines.append(pkt_prefix + f"**{node.packet.id}** — {node.packet.short}")
    for child in node.children:
        lines.extend(_render_overview(child, depth + 1))
    return lines


def aggregate_tree(root: Path, logger: HcagLogger) -> None:
    logger.info("aggregate.start", root=str(root))
    if not (root / "catalog.md").is_file():
        raise FileNotFoundError(
            f"No catalog.md at KB root — run `hcag preprocess {root}` first."
        )

    tree = _build_tree(root, root, [])
    packets = _flatten_packets(tree)

    generated_at = datetime.now(timezone.utc).isoformat()
    lines: list[str] = []
    lines.append(f"{HCAG_ROOT_CATALOG_MARKER} generated_at={generated_at} -->")
    lines.append("")
    lines.append("# Knowledge Catalog")
    lines.append("")
    lines.append("## Taxonomy Overview")
    lines.append("")
    lines.extend(_render_overview(tree))
    lines.append("")
    lines.append("## Packets")
    lines.append("")
    for p in packets:
        lines.append(f"### `{p.id}`")
        lines.append(f"- **path**: `{p.path}/`")
        lines.append(f"- **breadcrumb**: {' → '.join(p.breadcrumb)}")
        lines.append(f"- **title**: {p.title}")
        lines.append(f"- **short**: {p.short}")
        lines.append(f"- **long**: {p.long}")
        lines.append(f"- **tokens**: {p.tokens}")
        lines.append("")

    # Detect duplicate packet IDs (§3.5.4)
    seen: dict[str, PacketRecord] = {}
    for p in packets:
        if p.id in seen:
            raise ValueError(f"Duplicate packet ID: {p.id} at {p.path} and {seen[p.id].path}")
        seen[p.id] = p

    (root / "catalog.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    logger.info("aggregate.done", packets=len(packets))
