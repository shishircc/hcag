"""`hcag preprocess` — DFS-based single-artifact KB normalization (§3.4).

Walks the tree depth-first, post-order. At every folder — leaf, taxonomy
node, mixed, or root — assembles one ``compiled.md`` that carries the
folder's own content, plus — at the root only — the KB's one ``## Catalog``.

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
from ..crawl.urls import SIDECAR_NAME, read_link_order, read_sidecar
from ..logger import HcagLogger
from ..compiled_io import (
    CatalogRecord,
    CompiledFrontMatter,
    FolderKind,
    is_hcag_generated,
    read_compiled,
    read_compiled_frontmatter,
    render_catalog_table,
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

#: The folder's own page, written by `crawl` at the deepest level of its URL
#: (§4.5). It leads the packet, because it introduces the topic.
OWN_PAGE = "index.md"
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
class FolderResult:
    """What DFS returns to its caller (§3.4.1).

    ``rows`` is this folder's catalog row — if it has content of its own —
    followed by everything its children returned, already in DFS pre-order and
    already carrying final, KB-absolute ids and paths. Nothing is re-parented
    on the way up and nothing is re-summarized; the root writes the accumulated
    list as the KB's one catalog (D3a).

    ``folders`` counts every folder in this subtree including this one, which
    is what an ancestor's ``descendants`` front-matter field reports. It is not
    ``len(rows)``: a pure taxonomy node is a folder and is not a row.
    """

    id: str
    rows: list[CatalogRecord] = field(default_factory=list)
    folders: int = 1
    subtree_depth: int = 0


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
        if name == SIDECAR_NAME:
            # HCAG's own provenance file (§3.2 rule 1a): consumed for ordering,
            # never content, and never reported as a stray — it would otherwise
            # WARN on every branch folder of every crawled KB.
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


def order_sources(folder: Path, sources: list[Path]) -> list[Path]:
    """Order a folder's source `.md` files for concatenation (§3.4.3).

    A packet is one document an LLM reads top to bottom, so the order sources
    are concatenated in *is* the order the model reads them. Alphabetical order
    is an accident of slug spelling; the site's own index page knows better.

    1. `index.md` leads — it introduces the topic.
    2. Then the order the index page mentioned the others, taken from the
       `.hcag-crawl.json` sidecar if `crawl` wrote one (§4.5.3), else from the
       links left in `index.md` itself.
    3. Then anything unmentioned, alphabetically, so the build stays
       reproducible.

    Every stage degrades rather than fails: no sidecar falls back to the
    index's own links, no links falls back to alphabetical, no `index.md`
    falls back to alphabetical.
    """
    by_stem = {p.stem: p for p in sources}
    own = next((p for p in sources if p.name == OWN_PAGE), None)

    ordered: list[Path] = []
    if own is not None:
        ordered.append(own)
        # The sidecar reads the full DOM, so it works on a hub page whose link
        # list extraction removed; index.md's own links only work where they
        # survived. Prefer the former, fall back to the latter.
        mentioned = read_link_order(folder) or _links_in(own)
        for slug in mentioned:
            hit = by_stem.get(slug)
            # An entry naming something absent is skipped rather than trusted:
            # an edited tree must not break a build or cite a missing source.
            if hit is not None and hit is not own and hit not in ordered:
                ordered.append(hit)

    remaining = sorted((p for p in sources if p not in ordered), key=lambda p: p.name)
    return ordered + remaining


_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")


def _links_in(path: Path) -> list[str]:
    """Slugs linked by `path`, in first-mention order.

    Crawled links are absolute URLs (§4.4.1 stage 1) and the tree mirrors the
    URL path (§4.5), so a link's last segment names a sibling.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    slugs: list[str] = []
    for match in _LINK_RE.finditer(text):
        url = match.group(1).split("#", 1)[0].split("?", 1)[0]
        segments = [s for s in url.split("/") if s]
        if len(segments) < 2:  # scheme + host only
            continue
        last = segments[-1]
        slug = last.rsplit(".", 1)[0] if "." in last else last
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


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
    for src in order_sources(folder, source_files):
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
    """Fallback when the LLM call fails, so the folder still appears and loads.

    Only reachable under ``--allow-partial`` (§3.4.9). The text is deliberately
    not plausible prose: a row that reads like a description is one an agent
    will route on, and this folder's description is precisely what is missing.
    """
    return FolderMetadata(
        title=folder_id or "root",
        long_description=f"(description unavailable: {reason})",
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
            return generate_folder_metadata(cfg.llm, own_content=own_content)
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


# --- Catalog rows (D3a, §3.4.1) --------------------------------------------
#
# There is nothing here to rebase or roll up. A row is created once, at the
# folder it describes, with its final KB-absolute id and path, and travels up
# the recursion untouched. What used to be `_rebase` + `_roll_up` + a render
# trim is now list concatenation, which is the point: no coordinate arithmetic
# to get wrong, and no ancestor holding a copy that can disagree with the root.


def _rel_path(root: Path, folder: Path) -> str:
    """Folder path relative to the KB root, POSIX, ``""`` for the root."""
    if folder == root:
        return ""
    return folder.relative_to(root).as_posix()


def _process_folder(
    folder: Path,
    root: Path,
    cfg: CliConfig,
    logger: HcagLogger,
    force: bool,
    state: _BuildState | None = None,
) -> FolderResult | None:
    """DFS post-order: recurse into subdirs first, then emit this folder's
    ``compiled.md`` and hand its catalog rows up (§3.4.1).
    """
    state = state if state is not None else _BuildState()
    info = scan_folder(folder, logger=logger)
    kind = _classify(info)
    if kind is None:
        logger.warn("preprocess.skip_empty", folder=str(folder))
        return None

    folder_id = dotted_id_for(root, folder, root_id=cfg.root_id)
    folder_path = _rel_path(root, folder)
    depth = 0 if folder == root else len(folder_path.split("/"))
    compiled_path = folder / "compiled.md"
    is_root = folder == root

    # 1) Recurse into children (post-order). Each returns its rows — its own
    #    first, if it has content, then its descendants' — already in order.
    children: list[FolderResult] = []
    for sub in info.subdirs:
        result = _process_folder(sub, root, cfg, logger, force, state)
        if result is not None:
            children.append(result)

    child_rows = [r for c in children for r in c.rows]
    descendants = sum(c.folders for c in children)
    subtree_depth = max((c.subtree_depth + 1 for c in children), default=0)

    def _result(own_row: CatalogRecord | None) -> FolderResult:
        rows = ([own_row] if own_row is not None else []) + child_rows
        return FolderResult(
            id=folder_id,
            rows=rows,
            folders=descendants + 1,
            subtree_depth=subtree_depth,
        )

    # 2) Overwrite policy. The children have already been walked, so a skipped
    #    folder still contributes its subtree — only its own row is recovered
    #    from the artifact on disk rather than regenerated (§3.4.7).
    if compiled_path.is_file() and not force and not is_root:
        if not is_hcag_generated(compiled_path):
            raise RuntimeError(
                f"Refusing to overwrite non-HCAG compiled.md: {compiled_path}"
            )
        efm = read_compiled_frontmatter(compiled_path)
        if efm is None:
            raise RuntimeError(f"Cannot read existing compiled.md: {compiled_path}")
        logger.info(
            "preprocess.skip_compiled", folder=str(folder), id=folder_id, kind=efm.kind
        )
        existing_row = (
            CatalogRecord(
                id=folder_id,
                path=folder_path,
                depth=depth,
                title=efm.title,
                long=efm.long_description,
            )
            if efm.long_description
            else None
        )
        return _result(existing_row)

    # 3) Assemble own content + relocate images (leaf and mixed folders).
    if info.source_md_files:
        body_sections, copied_images = _relocate_images_and_rewrite(folder, info.source_md_files)
        own_content = "\n\n---\n\n".join(content for _, content in body_sections)
    else:
        body_sections = []
        copied_images = []
        own_content = ""

    sidecar = read_sidecar(folder)
    provenance = {str(k): str(v) for k, v in (sidecar.get("documents") or {}).items()}
    sidecar_images = {str(k): str(v) for k, v in (sidecar.get("images") or {}).items()}

    # 4) Summarize — but only if this folder has content of its own, and from
    #    that content alone (§3.4.4). A pure taxonomy node is a waypoint: no
    #    call, no description, no row. Describing one meant describing a branch
    #    nobody had read, which is where the invented prose came from (D3a).
    meta: FolderMetadata | None = None
    if own_content.strip():
        logger.info(
            "preprocess.metadata.request",
            folder=str(folder),
            id=folder_id,
            kind=kind,
            own_chars=len(own_content),
        )
        try:
            meta = _summarize_with_retries(
                cfg, logger, folder_id=folder_id, own_content=own_content
            )
        except LLMUnavailableError as e:
            # Systemic: every remaining folder with content needs the same
            # call, so there is nothing to be gained by walking the rest.
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
                    "A folder with no description is one the agent routes past; "
                    "re-run to resume, or pass --allow-partial to accept it.",
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
    else:
        logger.info(
            "preprocess.metadata.skipped",
            folder=str(folder),
            id=folder_id,
            kind=kind,
            reason="no_own_content",
        )

    title = meta.title if meta else _title_from_folder(folder, folder_id)
    own_row = (
        CatalogRecord(
            id=folder_id,
            path=folder_path,
            depth=depth,
            title=title,
            long=meta.long_description,
        )
        if meta is not None
        else None
    )

    # 5) The root carries the KB's one catalog: its own row, if it has content,
    #    followed by every descendant's, already in DFS pre-order.
    catalog_rows = ([own_row] if own_row is not None else []) + child_rows if is_root else None

    # 6) Token estimates (§3.4.3 step 6). The catalog is rendered here with the
    #    same function write_compiled_md uses, so the figure in front-matter
    #    and the bytes on disk cannot drift apart.
    content_tokens = estimate_tokens(
        own_content, cfg.tokenizer, image_count=len(copied_images)
    )
    catalog_tokens = (
        estimate_tokens(render_catalog_table(catalog_rows), cfg.tokenizer)
        if catalog_rows
        else 0
    )
    total_tokens = content_tokens + catalog_tokens

    # 7) Write compiled.md.
    fm = CompiledFrontMatter(
        id=folder_id,
        title=title,
        long_description=meta.long_description if meta else "",
        token_size_estimate=total_tokens,
        content_token_estimate=content_tokens,
        catalog_token_estimate=catalog_tokens,
        kind=kind,
        source_files=[name for name, _ in body_sections],
        # Copied, never fetched or verified: provenance stays a fact about the
        # crawl rather than a claim made at build time (§3.4.3 step 4a).
        source_urls=[provenance.get(name, "") for name, _ in body_sections],
        image_urls={n: sidecar_images[n] for n in copied_images if n in sidecar_images},
        children=[c.id for c in children],
        descendants=descendants,
        subtree_depth=subtree_depth,
    )
    write_compiled_md(compiled_path, fm, body_sections, catalog=catalog_rows)
    state.folders_written += 1

    logger.info(
        "preprocess.compiled_written",
        folder=str(folder),
        id=folder_id,
        kind=kind,
        tokens=total_tokens,
        content_tokens=content_tokens,
        catalog_tokens=catalog_tokens,
        catalog_rows=len(catalog_rows) if catalog_rows else 0,
        images=len(copied_images),
        children=len(children),
        descendants=descendants,
        subtree_depth=subtree_depth,
    )

    return _result(own_row)


def _title_from_folder(folder: Path, folder_id: str) -> str:
    """A waypoint's title, derived rather than generated (§3.4.2).

    It is a label for a folder that holds nothing, so it costs no LLM call and
    claims nothing: the folder's own name, tidied.
    """
    name = folder.name if folder_id else "root"
    return name.replace("-", " ").replace("_", " ").strip().title() or name


def _report_root_catalog(root: Path, cfg: CliConfig, logger: HcagLogger) -> None:
    """Log the size of what will be injected into the agent's system prompt,
    and WARN when it outgrows ``catalog.warn_tokens`` (§3.4.8, §3.9).
    """
    existing = read_compiled(root / "compiled.md")
    if existing is None:
        return
    fm, rows, _ = existing
    logger.info(
        "preprocess.root_catalog",
        # Rows are content-bearing folders; `descendants` counts every folder,
        # waypoints included. The gap is what the catalog no longer describes.
        catalog_rows=len(rows),
        folders=fm.descendants,
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
