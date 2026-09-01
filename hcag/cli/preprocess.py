"""`hcag preprocess` — DFS-based single-artifact KB normalization (§3.4).

Walks the tree depth-first, post-order. At every folder — leaf, taxonomy
node, mixed, or root — assembles one ``compiled.md`` that carries the
folder's own content and a ``## Sub-topics`` catalog.

The DFS return channel carries **two** things (§3.4.1): the folder's own
summary, and the folder's already-assembled *subtree index*. A parent
re-parents the records it inherits from each child (depth +1, path prefixed)
and splices them in after that child's own record, so every level's catalog
covers its entire subtree rather than one level down (D3a). The index reaches
full size at the root, which is why the root's ``compiled.md`` ends up holding
a catalog of the whole KB.

Summarization still looks only one level down — the roll-up copies records
rather than re-summarizing — so LLM cost stays at one call per folder.
"""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..config import CatalogConfig, CliConfig
from ..logger import HcagLogger
from ..compiled_io import (
    CatalogRecord,
    CompiledFrontMatter,
    FolderKind,
    is_hcag_generated,
    read_compiled,
    render_subtopics_section,
    write_compiled_md,
)
from .metadata_llm import (
    FolderMetadata,
    LLMUnavailableError,
    MetadataGenerationError,
    classify,
    describe_failure,
    generate_folder_metadata,
)
from .tokenizer import estimate_tokens


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
GENERATED_NAMES = {"compiled.md"}
IMAGE_REF_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\))")

#: Base for the exponential backoff between retries, in seconds.
RETRY_BASE_DELAY = 1.0


class PreprocessAborted(RuntimeError):
    """The run stopped rather than writing summaries it could not generate.

    Carries `folders_written` so the caller can tell the operator how much a
    resumed run will skip (§3.4.9).
    """

    def __init__(self, message: str, folders_written: int = 0) -> None:
        super().__init__(message)
        self.folders_written = folders_written


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
    """This folder's own summary record, as its parent will render it."""

    id: str
    path_rel_to_parent: str
    title: str
    short_description: str
    long_description: str
    token_size_estimate: int         # whole compiled.md + images
    content_token_estimate: int      # ## Content + images only (the budgeting figure)
    kind: FolderKind


@dataclass
class FolderResult:
    """What DFS returns to its caller (§3.4.1).

    ``subtree`` is the flat, DFS-pre-ordered index of every descendant of this
    folder, with ``depth`` and ``path`` expressed **relative to this folder**.
    The caller rebases it one level before splicing it into its own index.
    """

    summary: FolderSummary
    subtree: list[CatalogRecord] = field(default_factory=list)


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
    """Fallback when the LLM call fails so ancestors' catalogs still render.

    Only reachable under ``--allow-partial`` (§3.4.9): by default a folder that
    cannot be summarized aborts the run, because this placeholder is not a
    local blemish — it is an *input* to every ancestor's summary (§3.4.4).
    """
    return FolderMetadata(
        title=folder_id or "root",
        short_description=f"(summary unavailable: {reason})",
        long_description=f"Summary generation failed: {reason}. Content preserved as-is.",
    )


# --- LLM preflight and failure policy (§3.4.9) ------------------------------


@dataclass
class _BuildState:
    """Run-scoped knobs and counters threaded through the recursion."""

    allow_partial: bool = False
    folders_written: int = 0
    degraded: list[str] = field(default_factory=list)


def _sleep(attempt: int) -> None:
    """Exponential backoff between retries. Patched out in tests."""
    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))


def _summarize_with_retries(
    cfg: CliConfig,
    logger: HcagLogger,
    *,
    folder_id: str,
    own_content: str,
    children_longs: list[tuple[str, str]],
) -> FolderMetadata:
    """Call the summarizer, retrying transient failures (§3.4.9).

    Raises `LLMUnavailableError` immediately for systemic failures — retrying a
    rejected API key only delays the same outcome — and `MetadataGenerationError`
    once retries are exhausted for anything else.
    """
    attempts = max(0, cfg.llm.max_retries) + 1
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return generate_folder_metadata(
                cfg.llm,
                own_content=own_content,
                children_longs=children_longs,
            )
        except Exception as e:  # noqa: BLE001
            last = e
            kind = classify(e)
            if kind == "unavailable":
                raise LLMUnavailableError(describe_failure(cfg.llm, e)) from e
            if attempt + 1 < attempts:
                logger.warn(
                    "preprocess.metadata.retry",
                    id=folder_id,
                    attempt=attempt + 1,
                    of=attempts,
                    classification=kind,
                    error=f"{type(e).__name__}: {e}",
                )
                _sleep(attempt)
                continue
    raise MetadataGenerationError(describe_failure(cfg.llm, last)) from last


def preflight(cfg: CliConfig, logger: HcagLogger) -> None:
    """Prove the LLM works before the walk starts (§3.4.9).

    Deliberately a real summarizer request against the configured model and
    endpoint rather than a credentials-present check or a `/models` ping: it
    has to exercise the same path the build will — env-var resolution, provider
    dispatch, model-id validity, endpoint reachability, auth, and whether the
    model can actually produce the JSON object the build parses. Transient
    classes honor `llm.max_retries`; everything else fails the probe.

    Raises `LLMUnavailableError`. The caller must not have written anything yet.
    """
    started = time.monotonic()
    try:
        meta = _summarize_with_retries(
            cfg,
            logger,
            folder_id="(preflight)",
            own_content="# Preflight\n\nA short probe document used to verify LLM access.",
            children_longs=[],
        )
    except LLMUnavailableError:
        raise
    except MetadataGenerationError as e:
        # The call reached the model but the reply was unusable — commonly a
        # model too small to follow the output contract. Cheaper to learn now
        # than on call one hundred.
        raise LLMUnavailableError(f"preflight reply was not usable — {e}") from e
    if not meta.title.strip():
        raise LLMUnavailableError(
            f"preflight reply had no title ({describe_failure(cfg.llm, ValueError('empty title'))})"
        )
    logger.info(
        "preprocess.preflight.ok",
        provider=cfg.llm.provider,
        model=cfg.llm.litellm_model(),
        endpoint=cfg.llm.endpoint or None,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


# --- Subtree roll-up (D3a, §3.4.1) -----------------------------------------


def _rebase(records: list[CatalogRecord], child_dirname: str) -> list[CatalogRecord]:
    """Re-express a child's subtree index against *this* folder.

    A pure coordinate shift: ``depth`` gains a level and ``path`` gains the
    child's folder name as a prefix. ``id`` and ``parent`` are absolute dotted
    paths from the KB root (§3.4.5) and are deliberately left untouched — that
    invariance is what lets an id read from the root catalog be handed straight
    to ``check_and_load_kb``.
    """
    return [
        replace(
            r,
            depth=r.depth + 1,
            path=f"{child_dirname}/{r.path}" if r.path else child_dirname,
        )
        for r in records
    ]


def _roll_up(folder_id: str, children: list[tuple[str, FolderResult]]) -> list[CatalogRecord]:
    """Build this folder's subtree index from its children's DFS returns.

    Emits DFS pre-order: each child's own record immediately followed by that
    child's (rebased) subtree.
    """
    subtree: list[CatalogRecord] = []
    for dirname, result in children:
        s = result.summary
        subtree.append(
            CatalogRecord(
                id=s.id,
                path=dirname,
                title=s.title,
                short=s.short_description,
                long=s.long_description,
                tokens=s.content_token_estimate,
                depth=1,
                parent=folder_id,
                kind=s.kind,
            )
        )
        subtree.extend(_rebase(result.subtree, dirname))
    return subtree


def _render_view(records: list[CatalogRecord], cat: CatalogConfig) -> list[CatalogRecord]:
    """Trim the full subtree index down to what actually gets written (§3.4.4).

    ``max_depth`` caps how deep the section reaches; ``long_depth`` drops the
    `long` description below the nearest levels. Trimming happens only at
    render time — the untrimmed records keep travelling up the recursion,
    because a record that is too deep to render here may be shallow enough to
    carry its `long` in a different ancestor's catalog.
    """
    out: list[CatalogRecord] = []
    for r in records:
        if cat.max_depth > 0 and r.depth > cat.max_depth:
            continue
        out.append(r if r.depth <= cat.long_depth else replace(r, long=""))
    return out


def _process_folder(
    folder: Path,
    root: Path,
    cfg: CliConfig,
    logger: HcagLogger,
    force: bool,
    state: _BuildState | None = None,
) -> FolderResult | None:
    """DFS post-order: recurse into subdirs first, then emit this folder's
    ``compiled.md`` from the subtree index the recursion just returned.
    """
    state = state if state is not None else _BuildState()
    info = scan_folder(folder, logger=logger)
    kind = _classify(info)
    if kind is None:
        logger.warn("preprocess.skip_empty", folder=str(folder))
        return None

    folder_id = dotted_id_for(root, folder, root_id=cfg.root_id)
    compiled_path = folder / "compiled.md"

    # 1) Recurse into children (post-order). Each returns its own summary plus
    #    the subtree index it just assembled.
    children: list[tuple[str, FolderResult]] = []
    for sub in info.subdirs:
        result = _process_folder(sub, root, cfg, logger, force, state)
        if result is not None:
            children.append((sub.name, result))

    # 2) Roll the children's returns up into this folder's subtree index.
    subtree = _roll_up(folder_id, children)
    rendered = _render_view(subtree, cfg.catalog)
    subtree_depth = max((r.depth for r in subtree), default=0)

    # 3) Overwrite policy.
    if compiled_path.is_file() and not force:
        if not is_hcag_generated(compiled_path):
            raise RuntimeError(
                f"Refusing to overwrite non-HCAG compiled.md: {compiled_path}"
            )
        # Ancestors still need this folder's summary *and* its subtree index to
        # render their own catalogs — recover both from the existing artifact.
        existing = read_compiled(compiled_path)
        if existing is None:
            raise RuntimeError(f"Cannot read existing compiled.md: {compiled_path}")
        efm, erecords, _ = existing
        logger.info(
            "preprocess.skip_compiled",
            folder=str(folder),
            id=folder_id,
            descendants=len(erecords),
        )
        return FolderResult(
            summary=FolderSummary(
                id=folder_id,
                path_rel_to_parent=folder.name if folder != root else "",
                title=efm.title,
                short_description=efm.short_description,
                long_description=efm.long_description,
                token_size_estimate=efm.token_size_estimate,
                content_token_estimate=efm.content_token_estimate,
                kind=efm.kind,
            ),
            subtree=erecords,
        )

    # 4) Assemble own content + relocate images (leaf and mixed folders).
    if info.source_md_files:
        body_sections, copied_images = _relocate_images_and_rewrite(folder, info.source_md_files)
        own_content = "\n\n---\n\n".join(content for _, content in body_sections)
    else:
        body_sections = []
        copied_images = []
        own_content = ""

    # 5) Summarize this folder via LLM — from its own content plus its
    #    IMMEDIATE children's LONG descriptions (§3.4.4). Long, not short:
    #    summarization is iterated up the tree, so feeding one-line labels
    #    upward compounds the loss and leaves the root generic. The roll-up
    #    copies records; it does not re-summarize, so cost stays at one call
    #    per folder.
    children_longs = [(r.summary.id, r.summary.long_description) for _, r in children]
    logger.info(
        "preprocess.metadata.request",
        folder=str(folder),
        id=folder_id,
        kind=kind,
        own_chars=len(own_content),
        children=len(children),
        descendants=len(rendered),
    )
    try:
        meta = _summarize_with_retries(
            cfg,
            logger,
            folder_id=folder_id,
            own_content=own_content,
            children_longs=children_longs,
        )
    except LLMUnavailableError as e:
        # Systemic: every remaining folder needs the same call, so there is
        # nothing to be gained by walking the rest of the tree (§3.4.9).
        # --allow-partial does not cover this — it is not a per-folder problem.
        logger.error(
            "preprocess.abort",
            folder=str(folder),
            id=folder_id,
            reason="llm_unavailable",
            folders_written=state.folders_written,
            error=str(e),
        )
        raise PreprocessAborted(
            f"LLM became unavailable at {folder_id or '<root>'}: {e}",
            folders_written=state.folders_written,
        ) from e
    except MetadataGenerationError as e:
        logger.error(
            "preprocess.metadata.failed",
            folder=str(folder),
            id=folder_id,
            error=str(e),
            allow_partial=state.allow_partial,
        )
        if not state.allow_partial:
            raise PreprocessAborted(
                f"could not summarize {folder_id or '<root>'}: {e}. "
                "A placeholder summary would silently degrade every ancestor's "
                "description; re-run to resume, or pass --allow-partial to accept it.",
                folders_written=state.folders_written,
            ) from e
        logger.warn(
            "preprocess.metadata.degraded",
            folder=str(folder),
            id=folder_id,
            reason="allow_partial",
        )
        state.degraded.append(folder_id or "<root>")
        meta = _placeholder_summary(folder_id, kind, f"{type(e).__name__}")

    # 6) Token estimates (§3.4.3 step 6). The catalog section is rendered here
    #    with the same function write_compiled_md uses, so the figure recorded
    #    in front-matter and the bytes on disk cannot drift apart.
    content_tokens = estimate_tokens(
        own_content, cfg.tokenizer, image_count=len(copied_images)
    )
    catalog_section = render_subtopics_section(
        rendered, include_tree=cfg.catalog.include_tree
    )
    catalog_tokens = estimate_tokens(catalog_section, cfg.tokenizer) if catalog_section else 0
    total_tokens = content_tokens + catalog_tokens

    # 7) Write compiled.md.
    fm = CompiledFrontMatter(
        id=folder_id,
        title=meta.title,
        short_description=meta.short_description,
        long_description=meta.long_description,
        token_size_estimate=total_tokens,
        content_token_estimate=content_tokens,
        catalog_token_estimate=catalog_tokens,
        kind=kind,
        source_files=[name for name, _ in body_sections],
        children=[r.summary.id for _, r in children],
        descendants=len(rendered),
        subtree_depth=subtree_depth,
    )
    write_compiled_md(
        compiled_path,
        fm,
        rendered,
        body_sections,
        include_tree=cfg.catalog.include_tree,
    )
    state.folders_written += 1

    logger.info(
        "preprocess.compiled_written",
        folder=str(folder),
        id=folder_id,
        kind=kind,
        tokens=total_tokens,
        content_tokens=content_tokens,
        catalog_tokens=catalog_tokens,
        images=len(copied_images),
        children=len(children),
        descendants=len(rendered),
        subtree_depth=subtree_depth,
    )

    # 8) Return this folder's summary AND its subtree index to the parent.
    return FolderResult(
        summary=FolderSummary(
            id=folder_id,
            path_rel_to_parent=folder.name if folder != root else "",
            title=meta.title,
            short_description=meta.short_description,
            long_description=meta.long_description,
            token_size_estimate=total_tokens,
            content_token_estimate=content_tokens,
            kind=kind,
        ),
        subtree=subtree,
    )


def _report_root_catalog(root: Path, cfg: CliConfig, logger: HcagLogger) -> None:
    """Log the size of what will be injected into the agent's system prompt,
    and WARN when it outgrows ``catalog.warn_tokens`` (§3.4.8, §3.9).
    """
    existing = read_compiled(root / "compiled.md")
    if existing is None:
        return
    fm, records, _ = existing
    logger.info(
        "preprocess.root_catalog",
        descendants=fm.descendants or len(records),
        subtree_depth=fm.subtree_depth,
        catalog_tokens=fm.catalog_token_estimate,
        warn_tokens=cfg.catalog.warn_tokens,
    )
    if fm.catalog_token_estimate > cfg.catalog.warn_tokens:
        # Name the branches contributing most entries so the remedy is obvious.
        per_branch: dict[str, int] = {}
        for r in records:
            per_branch[r.id.split(".", 1)[0]] = per_branch.get(r.id.split(".", 1)[0], 0) + 1
        worst = sorted(per_branch.items(), key=lambda kv: -kv[1])[:5]
        logger.warn(
            "preprocess.root_catalog_oversized",
            catalog_tokens=fm.catalog_token_estimate,
            warn_tokens=cfg.catalog.warn_tokens,
            descendants=fm.descendants or len(records),
            subtree_depth=fm.subtree_depth,
            largest_branches=[{"id": k, "entries": v} for k, v in worst],
            remedy="lower catalog.long_depth, or set catalog.max_depth (§3.4.4)",
        )


def preprocess_tree(
    root: Path,
    cfg: CliConfig,
    logger: HcagLogger,
    force: bool = False,
    only: Path | None = None,
    allow_partial: bool = False,
) -> None:
    """DFS post-order traversal. See module docstring.

    Raises `PreprocessAborted` if the LLM cannot serve the build — at the
    preflight before anything is written, or mid-walk rather than filling the
    rest of the tree with placeholder summaries (§3.4.9).
    """
    logger.info(
        "preprocess.start",
        root=str(root),
        force=force,
        only=str(only) if only else None,
        allow_partial=allow_partial,
    )

    # Prove the LLM works before scanning the tree or writing a byte (§3.4.9).
    if cfg.llm.preflight:
        try:
            preflight(cfg, logger)
        except LLMUnavailableError as e:
            logger.error("preprocess.preflight.failed", root=str(root), error=str(e))
            raise PreprocessAborted(
                f"LLM preflight failed, nothing was written: {e}", folders_written=0
            ) from e
    else:
        logger.warn("preprocess.preflight.skipped", root=str(root), reason="llm.preflight=false")

    state = _BuildState(allow_partial=allow_partial)

    if only is not None:
        only = only.resolve()
        # Preprocess the subtree first, then re-emit ancestors up to the root.
        # With whole-subtree roll-up this is mandatory, not an optimization: a
        # change anywhere in a branch alters the catalog of every ancestor up
        # to and including the root (§3.4.7).
        _process_folder(only, root, cfg, logger, force, state)
        cursor = only.parent
        while True:
            if not cursor.is_dir() or not cursor.exists():
                break
            _process_folder(cursor, root, cfg, logger, True, state)
            if cursor == root:
                break
            cursor = cursor.parent
    else:
        _process_folder(root, root, cfg, logger, force, state)

    _report_root_catalog(root, cfg, logger)
    if state.degraded:
        logger.warn(
            "preprocess.done_degraded",
            root=str(root),
            degraded=list(state.degraded),
            count=len(state.degraded),
        )
    logger.info(
        "preprocess.done", root=str(root), folders_written=state.folders_written
    )
