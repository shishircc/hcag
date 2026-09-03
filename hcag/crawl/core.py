"""Crawl orchestration (§4.3, §4.4, §4.7).

A single BFS pass. Each popped URL runs three skip decisions (visited-dedup,
depth-cap, out-of-scope) before a fetch; each fetched document is converted,
has its images fetched and size-filtered, and is written before the loop moves
on. Nothing is buffered across pages and there is no post-BFS phase — the main
content decision for a page is made from that page alone (§4.4.1).

All events are structured-logged via the shared ``HcagLogger``.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..logger import HcagLogger
from .fetch import Fetcher, FetcherProtocol
from .html_conv import DEFAULT_MIN_EXTRACT_CHARS, FALLBACK_DISABLED, convert_html

#: Below this share of the DOM's visible text, a "successful" extraction is
#: reported as suspect (§4.7).
#:
#: Calibrated against a 35-page mom.gov.sg crawl whose extractions were each
#: checked by hand against the page's real main-content container. The
#: denominator here is the WHOLE DOM's text — nav, footer and banners included
#: — so on a chrome-heavy site even a perfect extraction scores 40-50%, and a
#: threshold set by intuition drowns in false positives: 55% flagged 15 pages
#: of which 11 were fine. Measured, the curve is steep:
#:
#:     threshold   caught   false alarms   missed
#:         25%        3           0          1
#:         35%        3           1          1
#:         45%        4           6          0
#:         55%        4          11          0
#:
#: 25% is the knee — every page it flags had genuinely lost content. It buys
#: precision at the cost of missing partial losses in the 40s, which is the
#: right trade for a WARN an operator is meant to act on rather than filter out.
LOW_RETENTION_PCT = 25.0
from .pdf_conv import convert_pdf
from .console import Console, CrawlReport
from .urls import (
    asset_host_allowed,
    collapse_leaf_dirs,
    find_layout_collisions,
    is_asset_url,
    normalize_url,
    url_to_output_paths,
    write_sidecar,
    OWN_PAGE_STEM,
)


HTML_TYPES = {"text/html", "application/xhtml+xml"}
PDF_TYPES = {"application/pdf", "application/x-pdf"}

DEFAULT_MIN_IMAGE_BYTES = 10_240  # §4.4.3 — 10 KB catches logos/favicons/glyphs

# Image outcomes returned by ``_process_html_image``. The caller uses these
# to decide whether to strip the corresponding Markdown reference (§4.4.3).
IMG_KEPT = "kept"
IMG_SIZE_SKIP = "size_skip"
IMG_FAILED = "failed"


_IMG_REF_TEMPLATE = r"!\[[^\]]*\]\({name}(?:\s+\"[^\"]*\")?\)"


def _remove_image_reference(markdown: str, local_name: str) -> str:
    """Strip every ``![alt](<local_name>)`` occurrence from ``markdown``.

    Only the reference itself is removed; surrounding whitespace is left
    alone. For an image-only line the reference removal leaves the line
    blank, which collapses when the Markdown is tidied.
    """
    pattern = _IMG_REF_TEMPLATE.format(name=re.escape(local_name))
    return re.sub(pattern, "", markdown)


@dataclass
class CrawlStats:
    pages_fetched: int = 0
    pages_written: int = 0
    pages_extracted: int = 0  # main content found by the extractor (§4.4.1)
    pages_fallback: int = 0   # whole-DOM path, chrome included (§4.4.1 stage 3)
    images_extracted: int = 0
    images_skipped_small: int = 0  # dropped by --min-image-bytes (§4.4.3)
    links_skipped_scope: int = 0
    links_skipped_visited: int = 0
    links_skipped_depth: int = 0
    assets_offsite: int = 0   # PDFs/images fetched from outside the prefix (§4.3.4)
    dirs_collapsed: int = 0   # leaf dirs flattened by the finalize pass (§4.5.2)
    sidecars_written: int = 0  # .hcag-crawl.json link-order files (§4.5.3)
    warnings: int = 0
    errors: int = 0


def crawl(
    seeds: list[str],
    depth: int,
    kb_root: Path,
    logger: HcagLogger,
    fetcher: FetcherProtocol | None = None,
    *,
    no_extract: bool = False,
    extract_favor: str = "balanced",
    min_extract_chars: int = DEFAULT_MIN_EXTRACT_CHARS,
    min_image_bytes: int = DEFAULT_MIN_IMAGE_BYTES,
    asset_hosts: tuple[str, ...] | None = None,
    console: Console | None = None,
) -> CrawlStats:
    stats = CrawlStats()

    if not seeds:
        logger.error("crawl.start.failed", reason="no_seeds")
        stats.errors += 1
        return stats

    try:
        kb_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(
            "crawl.start.failed",
            reason="output_root_not_writable",
            path=str(kb_root),
            error=str(e),
        )
        stats.errors += 1
        return stats

    normalized_seeds = [normalize_url(s) for s in seeds]

    logger.info(
        "crawl.start",
        seeds=list(seeds),
        depth=depth,
        output_root=str(kb_root),
        no_extract=no_extract,
        extract_favor=extract_favor,
        min_extract_chars=min_extract_chars,
        min_image_bytes=min_image_bytes,
    )

    console = console or Console()
    report = CrawlReport()
    asset_hosts = frozenset(h.lower() for h in (asset_hosts or ()))

    owns_fetcher = fetcher is None
    if fetcher is None:
        fetcher = Fetcher()

    try:
        visited: set[str] = set()
        # folder -> (source_url, links in document order). Populated as pages
        # are written; consumed by the finalize pass to emit §4.5.3 sidecars.
        # The order is the FULL DOM's, captured before extraction discards a
        # hub page's link list.
        page_links: dict[Path, tuple[str, list[str]]] = {}
        # Absolute path -> the URL it came from (§4.5.3). Keyed by path, not by
        # folder, because the finalize pass moves files: provenance is learned
        # before the tree takes its final shape and has to survive the collapse.
        doc_urls: dict[Path, str] = {}
        image_urls: dict[Path, str] = {}
        # (url, depth, write_into). `write_into` is set for an off-prefix asset
        # (§4.3.4): it lands in the folder of the page that cited it, never
        # mirrored at its own URL path.
        queue: deque[tuple[str, int, Path | None]] = deque()
        for seed in seeds:
            n = normalize_url(seed)
            if n in visited:
                continue
            visited.add(n)
            queue.append((seed, 0, None))

        while queue:
            url, cur_depth, write_into = queue.popleft()
            _process(
                url,
                cur_depth,
                depth,
                normalized_seeds,
                visited,
                queue,
                kb_root,
                fetcher,
                logger,
                stats,
                page_links,
                doc_urls,
                image_urls,
                console,
                report,
                write_into=write_into,
                asset_hosts=asset_hosts,
                no_extract=no_extract,
                extract_favor=extract_favor,
                min_extract_chars=min_extract_chars,
                min_image_bytes=min_image_bytes,
            )

        _finalize_layout(kb_root, logger, stats, page_links, doc_urls, image_urls)
        console.report(report)

        logger.info(
            "crawl.done",
            pages_fetched=stats.pages_fetched,
            pages_written=stats.pages_written,
            pages_extracted=stats.pages_extracted,
            pages_fallback=stats.pages_fallback,
            images_extracted=stats.images_extracted,
            images_skipped_small=stats.images_skipped_small,
            skipped_scope=stats.links_skipped_scope,
            skipped_visited=stats.links_skipped_visited,
            skipped_depth=stats.links_skipped_depth,
            dirs_collapsed=stats.dirs_collapsed,
            sidecars_written=stats.sidecars_written,
            assets_offsite=stats.assets_offsite,
            warnings=stats.warnings,
            errors=stats.errors,
        )
        return stats
    finally:
        if owns_fetcher:
            fetcher.close()


def _finalize_layout(
    kb_root: Path,
    logger: HcagLogger,
    stats: CrawlStats,
    page_links: dict[Path, tuple[str, list[str]]] | None = None,
    doc_urls: dict[Path, str] | None = None,
    image_urls: dict[Path, str] | None = None,
) -> None:
    """Flatten leaf directories, write sidecars, assert the invariant.

    Runs once, after the traversal, because whether a page has children is only
    knowable when the crawl is done (§4.5.2), and a sidecar describes the tree
    as it finally stands (§4.5.3).
    """
    doc_urls = dict(doc_urls or {})
    image_urls = dict(image_urls or {})
    moved: dict[Path, Path] = {}

    def _log(directory: Path, md_path: Path, renamed: list[str], moves: dict[Path, Path]) -> None:
        moved.update(moves)
        logger.debug(
            "crawl.layout.collapsed",
            directory=str(directory),
            output_path=str(md_path),
            images_renamed=len(renamed),
        )

    stats.dirs_collapsed = collapse_leaf_dirs(kb_root, on_collapse=_log)

    # Provenance was recorded against pre-collapse paths; follow the moves so a
    # fact learned at fetch time survives the tree being reshaped.
    def _rebase(urls: dict[Path, str]) -> dict[Path, str]:
        return {moved.get(path, path): url for path, url in urls.items()}

    doc_urls = _rebase(doc_urls)
    image_urls = _rebase(image_urls)

    # A sidecar per folder holding documents, not only branch folders: a folder
    # of collapsed leaves has no index page and still holds files whose origin
    # someone will want (§4.5.3).
    folders = {p.parent for p in doc_urls} | {p.parent for p in image_urls}
    folders |= set(page_links or {})
    for folder in sorted(folders):
        if not folder.is_dir():
            continue
        source_url, links = (page_links or {}).get(folder, (None, None))
        documents = {p.name: u for p, u in doc_urls.items() if p.parent == folder and p.exists()}
        images = {p.name: u for p, u in image_urls.items() if p.parent == folder and p.exists()}
        recorded = write_sidecar(folder, source_url, links, documents, images)
        if not (documents or images or recorded):
            continue
        stats.sidecars_written += 1
        logger.debug(
            "crawl.layout.sidecar",
            folder=str(folder),
            source_url=source_url,
            link_order=len(recorded),
            documents=len(documents),
            images=len(images),
        )
        if (folder / f"{OWN_PAGE_STEM}.md").is_file() and links and not recorded:
            # The hub's link list did not survive extraction AND the full-DOM
            # order found no in-folder children — `preprocess` will fall back
            # to alphabetical for this packet (§3.4.3).
            stats.warnings += 1
            logger.warn(
                "crawl.layout.link_order_empty",
                folder=str(folder),
                source_url=source_url,
                detail="no linked child pages were written; packet order will be alphabetical",
            )

    # Postcondition, not a heuristic: the collapse creates `X.md` in the same
    # operation that removes `X/`, so a surviving pair means the tree is
    # mis-shaped in exactly the way this layout exists to prevent — and
    # `hcag preprocess` would build packets on top of it.
    collisions = find_layout_collisions(kb_root)
    if collisions:
        stats.errors += len(collisions)
        for directory in collisions:
            logger.error(
                "crawl.layout.invariant_violated",
                directory=str(directory),
                sibling=str(directory.parent / f"{directory.name}.md"),
                detail="a directory must not sit beside a same-named .md file",
            )


def _process(
    url: str,
    cur_depth: int,
    max_depth: int,
    normalized_seeds: list[str],
    visited: set[str],
    queue: deque[tuple[str, int]],
    kb_root: Path,
    fetcher: FetcherProtocol,
    logger: HcagLogger,
    stats: CrawlStats,
    page_links: dict[Path, tuple[str, list[str]]],
    doc_urls: dict[Path, str],
    image_urls: dict[Path, str],
    console: Console,
    report: CrawlReport,
    *,
    write_into: Path | None = None,
    asset_hosts: frozenset[str] = frozenset(),
    no_extract: bool,
    extract_favor: str,
    min_extract_chars: int,
    min_image_bytes: int,
) -> None:
    console.fetching(url, cur_depth, "pdf" if write_into is not None else "html")
    try:
        result = fetcher.get(url)
    except Exception as e:
        logger.error("crawl.fetch.failed", url=url, depth=cur_depth, error=str(e))
        console.failed(url, "err")
        stats.errors += 1
        return

    stats.pages_fetched += 1

    if result.status_code >= 400:
        logger.warn("crawl.fetch.non_2xx", url=url, status=result.status_code)
        console.failed(url, str(result.status_code))
        report.skip("non-2xx", url, detail=str(result.status_code))
        stats.warnings += 1
        return

    md_path, doc_basename = url_to_output_paths(url, kb_root)
    if write_into is not None:
        # §4.3.4/§4.5: the asset inherits the topic of the page that cited it.
        # Its own path is where a CMS filed it, not what it is about.
        md_path = _asset_target(write_into, md_path.parent.name)
        doc_basename = md_path.stem

    if result.content_type in HTML_TYPES:
        try:
            converted = convert_html(
                result.content,
                base_url=result.url,
                doc_basename=doc_basename,
                extract=not no_extract,
                favor=extract_favor,
                min_extract_chars=min_extract_chars,
            )
        except Exception as e:
            logger.error("crawl.convert.failed", url=url, kind="html", error=str(e))
            stats.errors += 1
            return

        logger.info(
            "crawl.page.fetched",
            url=url,
            depth=cur_depth,
            content_type=result.content_type,
            byte_size=len(result.content),
            elapsed_ms=result.elapsed_ms,
            output_path=str(md_path),
        )

        if converted.extracted:
            stats.pages_extracted += 1
            logger.info(
                "crawl.extract.ok",
                url=url,
                html_bytes=len(result.content),
                markdown_chars=converted.markdown_chars,
                retained_pct=converted.retained_pct,
                links=converted.feature_counts.get("links", 0),
                images=converted.feature_counts.get("images", 0),
                tables=converted.feature_counts.get("tables", 0),
                elapsed_ms=result.elapsed_ms,
            )
            # Extraction can "succeed" and still drop half the article: the
            # min_extract_chars floor only catches a near-empty result, and a
            # partial one clears it easily. Retention is the signal that
            # separates them, so a low ratio is a WARN rather than a number
            # sitting unread in the INFO line above (§4.7).
            if converted.retained_pct < LOW_RETENTION_PCT:
                stats.warnings += 1
                logger.warn(
                    "crawl.extract.low_retention",
                    url=url,
                    retained_pct=converted.retained_pct,
                    threshold_pct=LOW_RETENTION_PCT,
                    markdown_chars=converted.markdown_chars,
                    text_chars=converted.text_chars,
                    hint=(
                        "main content may have been dropped; compare against "
                        "--no-extract or --favor recall"
                    ),
                )
            logger.debug(
                "crawl.extract.detail",
                url=url,
                favor=extract_favor,
                title_synthesized=converted.title_synthesized,
                forms_unwrapped=converted.forms_unwrapped,
                **converted.feature_counts,
            )
        else:
            stats.pages_fallback += 1
            # `--no-extract` is the operator's choice, not a failure — INFO.
            # A genuine extraction miss is a WARN the operator should act on.
            if converted.fallback_reason == FALLBACK_DISABLED:
                emit = logger.info
            else:
                emit = logger.warn
                stats.warnings += 1
            emit(
                "crawl.extract.fallback",
                url=url,
                reason=converted.fallback_reason,
                chars=converted.markdown_chars,
                min_extract_chars=min_extract_chars,
            )

        # Fetch + size-filter images BEFORE writing so no dangling reference
        # ever lands on disk (§4.4.3).
        markdown = converted.markdown
        for remote_url, local_name in converted.images:
            outcome = _process_html_image(
                remote_url,
                local_name,
                md_path.parent,
                fetcher,
                logger,
                stats,
                report,
                console,
                image_urls,
                source_doc=url,
                min_image_bytes=min_image_bytes,
                asset_hosts=asset_hosts,
            )
            if outcome == IMG_SIZE_SKIP:
                markdown = _remove_image_reference(markdown, local_name)

        _write_markdown(
            md_path, markdown, logger, stats,
            url=url, depth=cur_depth, content_type=result.content_type,
            byte_size=len(result.content), elapsed_ms=result.elapsed_ms,
            report=report, kind="html",
        )
        doc_urls[md_path] = url
        # Retain what the page linked, in document order (§4.5.3). Stage 1 read
        # the whole DOM, so this survives extraction dropping the link list.
        page_links[md_path.parent] = (url, list(converted.links))

        for link in converted.links:
            _consider_link(
                link, cur_depth, max_depth, normalized_seeds, visited, queue,
                logger, stats, report,
                source_doc=url, source_dir=md_path.parent, asset_hosts=asset_hosts,
            )

    elif result.content_type in PDF_TYPES or url.lower().endswith(".pdf"):
        try:
            converted_pdf = convert_pdf(result.content, doc_basename=doc_basename)
        except Exception as e:
            logger.error("crawl.convert.failed", url=url, kind="pdf", error=str(e))
            stats.errors += 1
            return

        # Size-filter embedded images BEFORE writing Markdown so no dangling
        # references land on disk (§4.4.3).
        markdown = converted_pdf.markdown
        kept_images = []
        for image in converted_pdf.images:
            if min_image_bytes > 0 and len(image.data) < min_image_bytes:
                stats.images_skipped_small += 1
                logger.info(
                    "crawl.image.skipped_small",
                    source=url,
                    local_filename=image.local_filename,
                    byte_size=len(image.data),
                    threshold=min_image_bytes,
                    embedded_in="pdf",
                )
                markdown = _remove_image_reference(markdown, image.local_filename)
                continue
            kept_images.append(image)

        _write_markdown(
            md_path, markdown, logger, stats,
            url=url, depth=cur_depth, content_type=result.content_type or "application/pdf",
            byte_size=len(result.content), elapsed_ms=result.elapsed_ms,
            report=report, kind="pdf",
        )
        doc_urls[md_path] = url

        for image in kept_images:
            target = md_path.parent / image.local_filename
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(image.data)
                # Embedded in the PDF, so the PDF is where it came from.
                image_urls[target] = url
                stats.images_extracted += 1
                logger.info(
                    "crawl.image.extract",
                    source=url,
                    local_path=str(target),
                    embedded_in="pdf",
                )
            except OSError as e:
                logger.warn("crawl.image.write_failed", source=url, local_path=str(target), error=str(e))
                stats.warnings += 1

    else:
        logger.warn(
            "crawl.fetch.unsupported_content_type",
            url=url,
            content_type=result.content_type or "(missing)",
        )
        report.skip("unsupported-type", url, detail=result.content_type or "(missing)")
        stats.warnings += 1


def _write_markdown(
    md_path: Path,
    content: str,
    logger: HcagLogger,
    stats: CrawlStats,
    *,
    url: str,
    depth: int,
    content_type: str,
    byte_size: int,
    elapsed_ms: int,
    report: CrawlReport | None = None,
    kind: str = "html",
) -> None:
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content, encoding="utf-8")
    except OSError as e:
        logger.error("crawl.write.failed", url=url, output_path=str(md_path), error=str(e))
        stats.errors += 1
        return
    stats.pages_written += 1
    if report is not None:
        report.include(kind, url)
    logger.info(
        "crawl.page.written",
        url=url,
        depth=depth,
        content_type=content_type,
        byte_size=byte_size,
        elapsed_ms=elapsed_ms,
        output_path=str(md_path),
    )


def _process_html_image(
    remote_url: str,
    local_name: str,
    doc_dir: Path,
    fetcher: FetcherProtocol,
    logger: HcagLogger,
    stats: CrawlStats,
    report: CrawlReport,
    console: Console,
    image_urls: dict[Path, str],
    *,
    source_doc: str,
    min_image_bytes: int,
    asset_hosts: frozenset[str] = frozenset(),
) -> str:
    """Fetch an HTML-referenced image, size-check it, write on success.

    Images have always been exempt from prefix scope — an embedded image is
    content of the page, not a page of its own — which §4.3.4 now states
    explicitly and extends to linked PDFs. The *host* bound applies here too.

    Returns one of ``IMG_KEPT`` (written to disk), ``IMG_SIZE_SKIP`` (dropped
    by the ``--min-image-bytes`` filter — caller should strip its Markdown
    reference), or ``IMG_FAILED`` (fetch/status/write failed — reference is
    left alone per pre-existing behavior; a WARN is logged).
    """
    if not asset_host_allowed(remote_url, source_doc, asset_hosts):
        logger.warn(
            "crawl.asset.skipped_host",
            url=remote_url,
            source=source_doc,
            host=(urlparse(remote_url).hostname or ""),
        )
        report.skip("asset-host-not-allowed", remote_url)
        stats.warnings += 1
        return IMG_FAILED

    console.fetching(remote_url, 0, "img")
    try:
        result = fetcher.get(remote_url)
    except Exception as e:
        logger.warn("crawl.image.download_failed", source=source_doc, remote_url=remote_url, error=str(e))
        stats.warnings += 1
        return IMG_FAILED

    if result.status_code >= 400:
        logger.warn(
            "crawl.image.non_2xx",
            source=source_doc,
            remote_url=remote_url,
            status=result.status_code,
        )
        stats.warnings += 1
        return IMG_FAILED

    if min_image_bytes > 0 and len(result.content) < min_image_bytes:
        stats.images_skipped_small += 1
        logger.info(
            "crawl.image.skipped_small",
            source=source_doc,
            remote_url=remote_url,
            local_filename=local_name,
            byte_size=len(result.content),
            threshold=min_image_bytes,
        )
        report.skip("image-too-small", remote_url, detail=f"{len(result.content)}B")
        return IMG_SIZE_SKIP

    target = doc_dir / local_name
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.content)
    except OSError as e:
        logger.warn(
            "crawl.image.write_failed",
            source=source_doc,
            remote_url=remote_url,
            local_path=str(target),
            error=str(e),
        )
        stats.warnings += 1
        return IMG_FAILED

    stats.images_extracted += 1
    image_urls[target] = remote_url
    report.include("img", remote_url)
    logger.info(
        "crawl.image.extract",
        source=source_doc,
        remote_url=remote_url,
        local_path=str(target),
    )
    return IMG_KEPT


def _asset_target(folder: Path, stem: str) -> Path:
    """Where an off-prefix asset lands inside its citing page's folder (§4.5).

    Collisions with an existing sibling are disambiguated rather than
    overwritten — an asset must never displace a crawled page.
    """
    candidate = folder / f"{stem}.md"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{n}.md"
        n += 1
    return candidate


def _consider_link(
    link: str,
    cur_depth: int,
    max_depth: int,
    normalized_seeds: list[str],
    visited: set[str],
    queue: deque[tuple[str, int, Path | None]],
    logger: HcagLogger,
    stats: CrawlStats,
    report: CrawlReport,
    *,
    source_doc: str,
    source_dir: Path,
    asset_hosts: frozenset[str] = frozenset(),
) -> None:
    try:
        n = normalize_url(link)
    except ValueError:
        logger.warn("crawl.link.unparseable", url=link, source=source_doc)
        report.skip("unparseable", link)
        stats.warnings += 1
        return

    # §4.3.4 — an asset referenced by an in-scope page is content *of* that
    # page. Prefix scope answers "which pages is this crawl about" and is the
    # wrong question for a file the CMS filed under its own media root; the
    # depth limit is likewise wrong, because an asset is terminal and cannot
    # expand the frontier. The host restriction is NOT lifted.
    if is_asset_url(link):
        if not asset_host_allowed(link, source_doc, asset_hosts):
            logger.warn(
                "crawl.asset.skipped_host",
                url=link,
                source=source_doc,
                host=(urlparse(link).hostname or ""),
            )
            report.skip("asset-host-not-allowed", link)
            stats.warnings += 1
            return
        if n in visited:
            report.skip("already-visited", link)
            stats.links_skipped_visited += 1
            return
        visited.add(n)
        stats.assets_offsite += 1
        logger.info(
            "crawl.asset.offsite_fetched",
            url=link,
            source=source_doc,
            kind="pdf",
            folder=str(source_dir),
        )
        queue.append((link, cur_depth, source_dir))
        return

    next_depth = cur_depth + 1
    if next_depth > max_depth:
        logger.debug(
            "crawl.link.skipped",
            disposition="skipped:depth-cap",
            url=link,
            source=source_doc,
        )
        report.skip("depth-limit", link)
        stats.links_skipped_depth += 1
        return

    if not any(n.startswith(seed) for seed in normalized_seeds):
        logger.debug(
            "crawl.link.skipped",
            disposition="skipped:out-of-scope",
            url=link,
            source=source_doc,
        )
        report.skip("out-of-scope", link)
        stats.links_skipped_scope += 1
        return

    if n in visited:
        report.skip("already-visited", link)
        logger.debug(
            "crawl.link.skipped",
            disposition="skipped:visited",
            url=link,
            source=source_doc,
        )
        stats.links_skipped_visited += 1
        return

    visited.add(n)
    queue.append((link, next_depth, None))
    logger.debug(
        "crawl.link.queued",
        url=link,
        source=source_doc,
        depth=next_depth,
    )
