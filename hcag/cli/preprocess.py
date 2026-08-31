"""`hcag preprocess` — DFS-based single-artifact KB normalization (§3.4).

Walks the tree depth-first, post-order. At every folder — leaf, taxonomy
node, mixed, or root — assembles one ``compiled.md`` that carries the
folder's own content and a ``## Sub-topics`` catalog of its immediate
children. The DFS return channel bubbles each folder's summary up to its
parent so the parent's catalog section has fresh metadata to render.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import CliConfig
from ..logger import HcagLogger
from ..compiled_io import (
    ChildEntry,
    CompiledFrontMatter,
    FolderKind,
    is_hcag_generated,
    write_compiled_md,
)
from .metadata_llm import FolderMetadata, generate_folder_metadata
from .tokenizer import estimate_tokens


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
GENERATED_NAMES = {"compiled.md"}
IMAGE_REF_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\))")


@dataclass
class FolderInfo:
    path: Path
    subdirs: list[Path]
    source_md_files: list[Path]  # excludes generated compiled.md
    image_files: list[Path]      # top-level images (not in assets/)
    ignored_files: list[Path]    # non-.md, non-image files silently skipped (§3.4.6)
    has_generated_compiled: bool


@dataclass
class FolderSummary:
    """What DFS returns to its caller so the parent can render its own entry."""

    id: str
    path_rel_to_parent: str
    title: str
    short_description: str
    long_description: str
    token_size_estimate: int
    kind: FolderKind


def scan_folder(path: Path, logger: HcagLogger | None = None) -> FolderInfo:
    """Enumerate a folder.

    Per §3.2 and §3.4.6, files that are neither ``.md`` nor a recognized image
    type are silently ignored (a WARN is logged when a logger is provided).
    """
    subdirs: list[Path] = []
    source_md: list[Path] = []
    images: list[Path] = []
    ignored: list[Path] = []
    has_compiled = False
    for entry in sorted(path.iterdir()):
        if entry.is_dir():
            if entry.name == "assets":
                continue
            subdirs.append(entry)
            continue
        name = entry.name
        suffix = entry.suffix.lower()
        if name == "compiled.md":
            has_compiled = True
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
        has_generated_compiled=has_compiled,
    )


def dotted_id_for(root: Path, folder: Path, root_id: str = "") -> str:
    """Dotted path from the KB root (§3.4.5). Root uses ``root_id``."""
    if folder == root:
        return root_id
    rel = folder.relative_to(root)
    return ".".join(rel.parts)


def _relocate_images_and_rewrite(
    folder: Path, source_files: list[Path]
) -> tuple[list[tuple[str, str]], list[str]]:
    """Copy top-level images into ``assets/`` (originals preserved per §3.4.6)
    and rewrite image references in the source files to point at ``assets/<name>``.

    Returns ``(body_sections, copied_image_filenames)`` where ``body_sections``
    is ``[(source_filename, rewritten_markdown), ...]``.
    """
    assets_dir = folder / "assets"
    copied: list[str] = []
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXTS:
            assets_dir.mkdir(exist_ok=True)
            target = assets_dir / entry.name
            shutil.copy2(str(entry), str(target))
            copied.append(entry.name)

    body_sections: list[tuple[str, str]] = []
    for src in source_files:
        text = src.read_text(encoding="utf-8")

        def _rewrite(m: re.Match) -> str:
            prefix, url, suffix = m.group(1), m.group(2), m.group(3)
            if url.startswith(("http://", "https://", "assets/")):
                return prefix + url + suffix
            basename = url.rsplit("/", 1)[-1]
            return prefix + f"assets/{basename}" + suffix

        rewritten = IMAGE_REF_RE.sub(_rewrite, text)
        body_sections.append((src.name, rewritten))
    return body_sections, copied


def _classify(info: FolderInfo) -> FolderKind | None:
    """Return leaf | node | mixed, or None for an empty folder (§3.4.2)."""
    has_md = bool(info.source_md_files)
    has_subs = bool(info.subdirs)
    if has_md and has_subs:
        return "mixed"
    if has_md:
        return "leaf"
    if has_subs:
        return "node"
    return None


def _placeholder_summary(folder_id: str, kind: FolderKind, reason: str) -> FolderMetadata:
    """Fallback when the LLM call fails so the parent's catalog can still render."""
    return FolderMetadata(
        title=folder_id or "root",
        short_description=f"(summary unavailable: {reason})",
        long_description=f"Summary generation failed: {reason}. Content preserved as-is.",
    )


def _process_folder(
    folder: Path,
    root: Path,
    cfg: CliConfig,
    logger: HcagLogger,
    force: bool,
) -> FolderSummary | None:
    """DFS post-order: recurse into subdirs first, then emit this folder's
    ``compiled.md`` using the child summaries returned from the recursion.
    """
    info = scan_folder(folder, logger=logger)
    kind = _classify(info)
    if kind is None:
        logger.warn("preprocess.skip_empty", folder=str(folder))
        return None

    # 1) Recurse into children (post-order).
    child_summaries: list[FolderSummary] = []
    for sub in info.subdirs:
        summary = _process_folder(sub, root, cfg, logger, force)
        if summary is not None:
            child_summaries.append(summary)

    folder_id = dotted_id_for(root, folder, root_id=cfg.root_id)
    compiled_path = folder / "compiled.md"

    # 2) Overwrite policy.
    if compiled_path.is_file() and not force:
        if not is_hcag_generated(compiled_path):
            raise RuntimeError(
                f"Refusing to overwrite non-HCAG compiled.md: {compiled_path}"
            )
        # We still need to return the folder's summary so ancestors can render
        # entries — pull it from the existing front-matter.
        from ..compiled_io import read_compiled_frontmatter

        existing = read_compiled_frontmatter(compiled_path)
        if existing is None:
            raise RuntimeError(f"Cannot read existing compiled.md: {compiled_path}")
        logger.info("preprocess.skip_compiled", folder=str(folder), id=folder_id)
        return FolderSummary(
            id=folder_id,
            path_rel_to_parent=folder.name if folder != root else "",
            title=existing.title,
            short_description=existing.short_description,
            long_description=existing.long_description,
            token_size_estimate=existing.token_size_estimate,
            kind=existing.kind,
        )

    # 3) Assemble own content + relocate images (leaf and mixed folders).
    if info.source_md_files:
        body_sections, copied_images = _relocate_images_and_rewrite(folder, info.source_md_files)
        own_content = "\n\n---\n\n".join(content for _, content in body_sections)
    else:
        body_sections = []
        copied_images = []
        own_content = ""

    # 4) Build catalog entries from child summaries.
    child_entries: list[ChildEntry] = [
        ChildEntry(
            id=s.id,
            path=s.path_rel_to_parent,
            title=s.title,
            short=s.short_description,
            long=s.long_description,
            tokens=s.token_size_estimate,
        )
        for s in child_summaries
    ]
    children_shorts = [(s.id, s.short_description) for s in child_summaries]

    # 5) Summarize this folder via LLM.
    logger.info(
        "preprocess.metadata.request",
        folder=str(folder),
        id=folder_id,
        kind=kind,
        own_chars=len(own_content),
        children=len(child_summaries),
    )
    try:
        meta = generate_folder_metadata(
            cfg.llm,
            own_content=own_content,
            children_shorts=children_shorts,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "preprocess.metadata.failed",
            folder=str(folder),
            id=folder_id,
            error=f"{type(e).__name__}: {e}",
        )
        meta = _placeholder_summary(folder_id, kind, f"{type(e).__name__}")

    # 6) Compute token estimate on the assembled content + image count.
    tokens = estimate_tokens(own_content, cfg.tokenizer, image_count=len(copied_images))

    # 7) Write compiled.md.
    fm = CompiledFrontMatter(
        id=folder_id,
        title=meta.title,
        short_description=meta.short_description,
        long_description=meta.long_description,
        token_size_estimate=tokens,
        kind=kind,
        source_files=[name for name, _ in body_sections],
        children=[s.id for s in child_summaries],
    )
    write_compiled_md(compiled_path, fm, child_entries, body_sections)

    logger.info(
        "preprocess.compiled_written",
        folder=str(folder),
        id=folder_id,
        kind=kind,
        tokens=tokens,
        images=len(copied_images),
        children=len(child_entries),
    )

    # 8) Return this folder's summary to the parent.
    return FolderSummary(
        id=folder_id,
        path_rel_to_parent=folder.name if folder != root else "",
        title=meta.title,
        short_description=meta.short_description,
        long_description=meta.long_description,
        token_size_estimate=tokens,
        kind=kind,
    )


def preprocess_tree(
    root: Path,
    cfg: CliConfig,
    logger: HcagLogger,
    force: bool = False,
    only: Path | None = None,
) -> None:
    """DFS post-order traversal. See module docstring."""
    logger.info("preprocess.start", root=str(root), force=force, only=str(only) if only else None)

    if only is not None:
        only = only.resolve()
        # Preprocess the subtree first, then re-emit ancestors up to the root
        # so their `## Sub-topics` sections pick up the changed child summary.
        _process_folder(only, root, cfg, logger, force)
        cursor = only.parent
        while True:
            if not cursor.is_dir() or not cursor.exists():
                break
            _process_folder(cursor, root, cfg, logger, force=True)
            if cursor == root:
                break
            cursor = cursor.parent
    else:
        _process_folder(root, root, cfg, logger, force)

    logger.info("preprocess.done", root=str(root))
