"""`hcag preprocess` — bottom-up KB normalization (§3.4)."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import CliConfig
from ..logger import HcagLogger
from .catalog_io import (
    HCAG_CATALOG_MARKER,
    HCAG_PACKET_MARKER,
    ChildEntry,
    NodeCatalogFrontMatter,
    PacketFrontMatter,
    is_hcag_generated,
    read_node_catalog,
    read_packet_frontmatter,
    write_node_catalog_md,
    write_packet_md,
)
from .metadata_llm import generate_node_metadata, generate_packet_metadata
from .tokenizer import estimate_tokens


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
GENERATED_NAMES = {"packet.md", "catalog.md"}
IMAGE_REF_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\))")


@dataclass
class FolderInfo:
    path: Path
    subdirs: list[Path]
    source_md_files: list[Path]  # excludes generated packet.md / catalog.md
    image_files: list[Path]      # top-level images (not in assets/)
    ignored_files: list[Path]    # non-.md, non-image files silently skipped (§3.4.6)
    has_generated_packet: bool
    has_generated_catalog: bool

    @property
    def is_packet(self) -> bool:
        return bool(self.source_md_files) or self.has_generated_packet

    @property
    def is_node(self) -> bool:
        return bool(self.subdirs)


def scan_folder(path: Path, logger: HcagLogger | None = None) -> FolderInfo:
    """Enumerate a folder.

    Per §3.2 and §3.4.6, files that are neither `.md` nor a recognized image
    type are silently ignored (a WARN is logged when a logger is provided) so
    that incidental artifacts — source `.docx`/`.pdf` documents, editor notes,
    `.DS_Store`, etc. — do not break the build.
    """
    subdirs: list[Path] = []
    source_md: list[Path] = []
    images: list[Path] = []
    ignored: list[Path] = []
    has_pkt = False
    has_cat = False
    for entry in sorted(path.iterdir()):
        if entry.is_dir():
            if entry.name == "assets":
                continue
            subdirs.append(entry)
            continue
        name = entry.name
        suffix = entry.suffix.lower()
        if name == "packet.md":
            has_pkt = True
            continue
        if name == "catalog.md":
            has_cat = True
            continue
        if suffix == ".md":
            source_md.append(entry)
        elif suffix in IMAGE_EXTS:
            images.append(entry)
        else:
            ignored.append(entry)
            if logger is not None:
                logger.warn(
                    "preprocess.ignored_file",
                    path=str(entry),
                    reason="unsupported_extension",
                    suffix=suffix or "(none)",
                )
    return FolderInfo(
        path=path,
        subdirs=subdirs,
        source_md_files=source_md,
        image_files=images,
        ignored_files=ignored,
        has_generated_packet=has_pkt,
        has_generated_catalog=has_cat,
    )


def dotted_id_for(root: Path, folder: Path, mixed_suffix: str = "_", as_packet_of_mixed: bool = False) -> str:
    rel = folder.relative_to(root)
    parts = list(rel.parts)
    if not parts or parts == ["."]:
        return "root"
    base = ".".join(parts)
    if as_packet_of_mixed:
        return f"{base}{mixed_suffix}"
    return base


def _relocate_images_and_rewrite(folder: Path, source_files: list[Path]) -> tuple[list[tuple[str, str]], list[str]]:
    """Copy all top-level images into assets/ (originals preserved per §3.4.6)
    and rewrite image references in source files to point at assets/<name>.

    Returns (body_sections, copied_image_filenames).
    body_sections is a list of (source_filename, rewritten_markdown).
    """
    assets_dir = folder / "assets"
    assets_dir.mkdir(exist_ok=True)

    # Copy every top-level image into assets/. Originals stay in place.
    copied: list[str] = []
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXTS:
            target = assets_dir / entry.name
            shutil.copy2(str(entry), str(target))
            copied.append(entry.name)

    # Then rewrite image refs in each source
    body_sections: list[tuple[str, str]] = []
    for src in source_files:
        text = src.read_text(encoding="utf-8")

        def _rewrite(m: re.Match) -> str:
            prefix, url, suffix = m.group(1), m.group(2), m.group(3)
            if url.startswith(("http://", "https://", "assets/")):
                return prefix + url + suffix
            # Strip any leading ./ or ../ and point to assets/<basename>
            basename = url.rsplit("/", 1)[-1]
            return prefix + f"assets/{basename}" + suffix

        rewritten = IMAGE_REF_RE.sub(_rewrite, text)
        body_sections.append((src.name, rewritten))
    return body_sections, copied


def process_packet(
    folder: Path,
    packet_id: str,
    cfg: CliConfig,
    logger: HcagLogger,
    force: bool,
) -> PacketFrontMatter:
    """Generate packet.md for a leaf or mixed folder."""
    packet_md_path = folder / "packet.md"

    # Skip if exists and not forced
    if packet_md_path.is_file() and not force:
        existing = read_packet_frontmatter(packet_md_path)
        if existing is None:
            raise RuntimeError(
                f"Refusing to overwrite non-HCAG packet.md: {packet_md_path}"
            )
        if not is_hcag_generated(packet_md_path, HCAG_PACKET_MARKER):
            raise RuntimeError(
                f"Existing packet.md is not HCAG-generated (missing marker): {packet_md_path}"
            )
        logger.info("preprocess.skip_packet", folder=str(folder), id=packet_id)
        return existing

    # Collect sources and copy images. Non-.md/non-image files were already
    # logged when preprocess_tree scanned this folder; scan silently here to
    # avoid duplicate WARN lines. Per §3.4.3 step 4 and §3.4.6, source .md
    # files and original images are preserved — only derived artifacts
    # (packet.md and assets/<name>) are (re)written.
    info = scan_folder(folder)
    body_sections, copied_images = _relocate_images_and_rewrite(folder, info.source_md_files)

    if not body_sections:
        # No source .md files (mixed folder that only has subfolders and images, unusual)
        merged = ""
    else:
        merged = "\n\n---\n\n".join(content for _, content in body_sections)

    # Metadata via LLM
    logger.info("preprocess.metadata.request", folder=str(folder), id=packet_id, chars=len(merged))
    meta = generate_packet_metadata(cfg.llm, merged)

    tokens = estimate_tokens(merged, cfg.tokenizer, image_count=len(copied_images))

    fm = PacketFrontMatter(
        id=packet_id,
        title=meta.title,
        short_description=meta.short_description,
        long_description=meta.long_description,
        token_size_estimate=tokens,
        source_files=[name for name, _ in body_sections],
    )
    write_packet_md(packet_md_path, fm, body_sections)

    logger.info("preprocess.packet_written", id=packet_id, tokens=tokens, images=len(copied_images))
    return fm


def process_node(
    folder: Path,
    node_id: str,
    child_entries: list[ChildEntry],
    cfg: CliConfig,
    logger: HcagLogger,
    force: bool,
) -> None:
    """Generate catalog.md for a taxonomy node (or the taxonomy side of a mixed folder)."""
    catalog_path = folder / "catalog.md"

    if catalog_path.is_file() and not force:
        if not is_hcag_generated(catalog_path, HCAG_CATALOG_MARKER):
            raise RuntimeError(
                f"Refusing to overwrite non-HCAG catalog.md: {catalog_path}"
            )
        logger.info("preprocess.skip_catalog", folder=str(folder), id=node_id)
        return

    children_shorts = [(c.id, c.short) for c in child_entries]
    node_meta_llm = generate_node_metadata(cfg.llm, children_shorts) if children_shorts else None
    node_meta = NodeCatalogFrontMatter(
        node_title=node_meta_llm.node_title if node_meta_llm else node_id,
        node_short_description=node_meta_llm.node_short_description if node_meta_llm else "",
    )
    write_node_catalog_md(catalog_path, node_id, node_meta, child_entries)
    logger.info("preprocess.catalog_written", id=node_id, children=len(child_entries))


def preprocess_tree(root: Path, cfg: CliConfig, logger: HcagLogger, force: bool) -> None:
    """Bottom-up traversal producing packet.md at leaves/mixed and catalog.md at nodes/mixed."""
    logger.info("preprocess.start", root=str(root), force=force)

    # Post-order DFS: yield deepest folders first
    stack: list[tuple[Path, bool]] = [(root, False)]
    order: list[Path] = []
    while stack:
        p, visited = stack.pop()
        if visited:
            order.append(p)
            continue
        stack.append((p, True))
        for entry in sorted(p.iterdir()):
            if entry.is_dir() and entry.name != "assets":
                stack.append((entry, False))

    # Cache of processed folder metadata for parent-catalog assembly
    node_child_entries: dict[Path, list[ChildEntry]] = {}
    packet_metadata: dict[Path, PacketFrontMatter] = {}

    for folder in order:
        # Re-scan (state may have changed for deeper folders). Pass the logger
        # so non-.md/non-image files are recorded as WARN and then skipped.
        info = scan_folder(folder, logger=logger)
        is_packet = info.is_packet
        is_node = info.is_node

        # Reject unsupported files (implicit already in scan_folder)

        if not is_packet and not is_node:
            logger.warn("preprocess.skip_empty", folder=str(folder))
            continue

        if is_packet:
            if folder == root and not is_node:
                # Root folder as a packet is unusual but supported
                packet_id = dotted_id_for(root, folder, cfg.mixed_suffix, as_packet_of_mixed=False)
            elif is_node:
                packet_id = dotted_id_for(root, folder, cfg.mixed_suffix, as_packet_of_mixed=True)
            else:
                packet_id = dotted_id_for(root, folder, cfg.mixed_suffix, as_packet_of_mixed=False)
            fm = process_packet(folder, packet_id, cfg, logger, force)
            packet_metadata[folder] = fm

        if is_node:
            children: list[ChildEntry] = []
            for sub in info.subdirs:
                sub_is_packet = sub in packet_metadata
                sub_is_node = sub in node_child_entries
                if sub_is_packet and sub_is_node:
                    # Mixed child: emit BOTH entries (packet + node)
                    pfm = packet_metadata[sub]
                    children.append(
                        ChildEntry(
                            id=pfm.id,
                            kind="mixed_packet",
                            path=str(sub.relative_to(folder)),
                            title=pfm.title,
                            short=pfm.short_description,
                            long=pfm.long_description,
                            tokens=pfm.token_size_estimate,
                        )
                    )
                    sub_cat = read_node_catalog(sub / "catalog.md")
                    if sub_cat is not None:
                        node_meta, _ = sub_cat
                        children.append(
                            ChildEntry(
                                id=dotted_id_for(root, sub, cfg.mixed_suffix, as_packet_of_mixed=False),
                                kind="node",
                                path=str(sub.relative_to(folder)),
                                title=node_meta.node_title,
                                short=node_meta.node_short_description,
                                long="",
                                tokens=0,
                            )
                        )
                elif sub_is_packet:
                    pfm = packet_metadata[sub]
                    children.append(
                        ChildEntry(
                            id=pfm.id,
                            kind="packet",
                            path=str(sub.relative_to(folder)),
                            title=pfm.title,
                            short=pfm.short_description,
                            long=pfm.long_description,
                            tokens=pfm.token_size_estimate,
                        )
                    )
                elif sub_is_node or (sub / "catalog.md").is_file():
                    sub_cat = read_node_catalog(sub / "catalog.md")
                    if sub_cat is not None:
                        node_meta, _ = sub_cat
                        children.append(
                            ChildEntry(
                                id=dotted_id_for(root, sub, cfg.mixed_suffix, as_packet_of_mixed=False),
                                kind="node",
                                path=str(sub.relative_to(folder)),
                                title=node_meta.node_title,
                                short=node_meta.node_short_description,
                                long="",
                                tokens=0,
                            )
                        )
            node_id = dotted_id_for(root, folder, cfg.mixed_suffix, as_packet_of_mixed=False)
            if folder == root:
                node_id = "root"
                # Root catalog is written by `hcag aggregate`; for the root NODE we still write
                # a catalog.md as an intermediate. `aggregate` will overwrite it with root shape.
            process_node(folder, node_id, children, cfg, logger, force)
            node_child_entries[folder] = children

    logger.info("preprocess.done", root=str(root))
