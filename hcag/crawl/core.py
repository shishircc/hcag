"""Crawl orchestration (§4.3, §4.4.4, §4.7).

Two-phase execution:

- **Phase 1 (BFS + fingerprint accumulation).** Fetch → convert → for HTML
  pages, split the produced Markdown into blocks, add fingerprints to the
  cross-page index, and buffer the parsed blocks in memory. PDFs and images
  are written eagerly (they don't participate in boilerplate detection).
- **Phase 2 (identify + strip + write).** After BFS ends, classify each
  fingerprint as header, footer, or content per §4.4.4; strip each buffered
  HTML page's leading + trailing boilerplate run (bounded by the 50%-cap
  guard); write the finalized Markdown to disk.

All events are structured-logged via the shared ``HcagLogger``.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ..logger import HcagLogger
from .boilerplate import (
    DEFAULT_MIN_CORPUS,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW,
    BoilerplateSets,
    FingerprintIndex,
    blocks_to_markdown,
    identify_boilerplate,
    split_blocks,
    strip_page,
)
from .fetch import Fetcher, FetcherProtocol
from .html_conv import convert_html
from .pdf_conv import convert_pdf
from .urls import normalize_url, url_to_output_paths


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
    blank, and the block splitter (§4.4.4) absorbs the surrounding blanks.
    """
    pattern = _IMG_REF_TEMPLATE.format(name=re.escape(local_name))
    return re.sub(pattern, "", markdown)


@dataclass
class CrawlStats:
    pages_fetched: int = 0
    pages_written: int = 0
    images_extracted: int = 0
    images_skipped_small: int = 0  # dropped by --min-image-bytes (§4.4.3)
    links_skipped_scope: int = 0
    links_skipped_visited: int = 0
    links_skipped_depth: int = 0
    warnings: int = 0
    errors: int = 0
    # Boilerplate accounting (§4.4.4). All zeros when detection is disabled
    # or the corpus is below the minimum.
    boilerplate_pages_scanned: int = 0
    boilerplate_headers_detected: int = 0
    boilerplate_footers_detected: int = 0
    boilerplate_header_blocks_stripped: int = 0
    boilerplate_footer_blocks_stripped: int = 0
    boilerplate_page_guard_hits: int = 0


@dataclass
class _BufferedHtmlPage:
    url: str
    md_path: Path
    blocks: list[str]
    content_type: str
    byte_size: int
    elapsed_ms: int
    depth: int


@dataclass
class _Buffer:
    """In-memory state that survives from Phase 1 into Phase 2."""

    pages: list[_BufferedHtmlPage] = field(default_factory=list)
    index: FingerprintIndex = field(default_factory=FingerprintIndex)


def crawl(
    seeds: list[str],
    depth: int,
    kb_root: Path,
    logger: HcagLogger,
    fetcher: FetcherProtocol | None = None,
    *,
    boilerplate_threshold: float = DEFAULT_THRESHOLD,
    boilerplate_window: int = DEFAULT_WINDOW,
    min_corpus_for_boilerplate: int = DEFAULT_MIN_CORPUS,
    no_boilerplate: bool = False,
    min_image_bytes: int = DEFAULT_MIN_IMAGE_BYTES,
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
        boilerplate_threshold=boilerplate_threshold,
        boilerplate_window=boilerplate_window,
        no_boilerplate=no_boilerplate,
        min_image_bytes=min_image_bytes,
    )

    owns_fetcher = fetcher is None
    if fetcher is None:
        fetcher = Fetcher()

    buffer = _Buffer()

    try:
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        for seed in seeds:
            n = normalize_url(seed)
            if n in visited:
                continue
            visited.add(n)
            queue.append((seed, 0))

        # -------- Phase 1: BFS + fingerprint accumulation ----------------
        while queue:
            url, cur_depth = queue.popleft()
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
                buffer,
                min_image_bytes=min_image_bytes,
            )

        # -------- Phase 2: identify boilerplate, strip, write ------------
        _phase2_finalize(
            buffer,
            logger,
            stats,
            threshold=boilerplate_threshold,
            window=boilerplate_window,
            min_corpus=min_corpus_for_boilerplate,
            no_boilerplate=no_boilerplate,
        )

        logger.info(
            "crawl.done",
            pages_fetched=stats.pages_fetched,
            pages_written=stats.pages_written,
            images_extracted=stats.images_extracted,
            images_skipped_small=stats.images_skipped_small,
            skipped_scope=stats.links_skipped_scope,
            skipped_visited=stats.links_skipped_visited,
            skipped_depth=stats.links_skipped_depth,
            boilerplate_pages_scanned=stats.boilerplate_pages_scanned,
            boilerplate_headers_detected=stats.boilerplate_headers_detected,
            boilerplate_footers_detected=stats.boilerplate_footers_detected,
            boilerplate_blocks_stripped=(
                stats.boilerplate_header_blocks_stripped
                + stats.boilerplate_footer_blocks_stripped
            ),
            warnings=stats.warnings,
            errors=stats.errors,
        )
        return stats
    finally:
        if owns_fetcher:
            fetcher.close()


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
    buffer: _Buffer,
    *,
    min_image_bytes: int,
) -> None:
    try:
        result = fetcher.get(url)
    except Exception as e:
        logger.error("crawl.fetch.failed", url=url, depth=cur_depth, error=str(e))
        stats.errors += 1
        return

    stats.pages_fetched += 1

    if result.status_code >= 400:
        logger.warn("crawl.fetch.non_2xx", url=url, status=result.status_code)
        stats.warnings += 1
        return

    md_path, doc_basename = url_to_output_paths(url, kb_root)

    if result.content_type in HTML_TYPES:
        try:
            converted = convert_html(result.content, base_url=result.url, doc_basename=doc_basename)
        except Exception as e:
            logger.error("crawl.convert.failed", url=url, kind="html", error=str(e))
            stats.errors += 1
            return

        # Fetch + size-filter images BEFORE splitting/fingerprinting so buffered
        # blocks reflect the trimmed Markdown (§4.4.3, §4.9).
        markdown = converted.markdown
        for remote_url, local_name in converted.images:
            outcome = _process_html_image(
                remote_url,
                local_name,
                md_path.parent,
                fetcher,
                logger,
                stats,
                source_doc=url,
                min_image_bytes=min_image_bytes,
            )
            if outcome == IMG_SIZE_SKIP:
                markdown = _remove_image_reference(markdown, local_name)

        blocks = split_blocks(markdown)
        buffer.index.add_page(page_id=url, blocks=blocks)
        buffer.pages.append(
            _BufferedHtmlPage(
                url=url,
                md_path=md_path,
                blocks=blocks,
                content_type=result.content_type,
                byte_size=len(result.content),
                elapsed_ms=result.elapsed_ms,
                depth=cur_depth,
            )
        )
        logger.info(
            "crawl.page.fetched",
            url=url,
            depth=cur_depth,
            content_type=result.content_type,
            byte_size=len(result.content),
            elapsed_ms=result.elapsed_ms,
            planned_output_path=str(md_path),
            blocks=len(blocks),
        )

        for link in converted.links:
            _consider_link(link, cur_depth, max_depth, normalized_seeds, visited, queue, logger, stats, source_doc=url)

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
        )

        for image in kept_images:
            target = md_path.parent / image.local_filename
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(image.data)
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
        stats.warnings += 1


# --- Phase 2 --------------------------------------------------------------


def _phase2_finalize(
    buffer: _Buffer,
    logger: HcagLogger,
    stats: CrawlStats,
    *,
    threshold: float,
    window: int,
    min_corpus: int,
    no_boilerplate: bool,
) -> None:
    stats.boilerplate_pages_scanned = buffer.index.page_count()

    if not buffer.pages:
        logger.info("crawl.boilerplate.skipped", reason="no_html_pages")
        return

    if no_boilerplate:
        logger.info("crawl.boilerplate.skipped", reason="disabled", pages=len(buffer.pages))
        _flush_verbatim(buffer, logger, stats)
        return

    if stats.boilerplate_pages_scanned < min_corpus:
        logger.info(
            "crawl.boilerplate.skipped",
            reason="min_corpus",
            pages=stats.boilerplate_pages_scanned,
            min_corpus=min_corpus,
        )
        _flush_verbatim(buffer, logger, stats)
        return

    sets = identify_boilerplate(buffer.index, threshold=threshold, window=window)
    stats.boilerplate_headers_detected = len(sets.headers)
    stats.boilerplate_footers_detected = len(sets.footers)
    logger.info(
        "crawl.boilerplate.identified",
        pages_scanned=stats.boilerplate_pages_scanned,
        header_fingerprints=stats.boilerplate_headers_detected,
        footer_fingerprints=stats.boilerplate_footers_detected,
        threshold=threshold,
        window=window,
    )

    for page in buffer.pages:
        result = strip_page(page.blocks, sets)
        if result.guard_tripped:
            stats.boilerplate_page_guard_hits += 1
            logger.warn(
                "crawl.boilerplate.page_guard",
                url=page.url,
                blocks=len(page.blocks),
                would_remove=len(page.blocks) - len(result.blocks) if result.blocks else 0,
            )
        content = blocks_to_markdown(result.blocks)
        _write_markdown(
            page.md_path, content, logger, stats,
            url=page.url, depth=page.depth, content_type=page.content_type,
            byte_size=page.byte_size, elapsed_ms=page.elapsed_ms,
        )
        stats.boilerplate_header_blocks_stripped += result.header_removed
        stats.boilerplate_footer_blocks_stripped += result.footer_removed
        logger.info(
            "crawl.boilerplate.stripped",
            url=page.url,
            header_blocks_removed=result.header_removed,
            footer_blocks_removed=result.footer_removed,
            total_blocks_before=len(page.blocks),
            total_blocks_after=len(result.blocks),
        )


def _flush_verbatim(buffer: _Buffer, logger: HcagLogger, stats: CrawlStats) -> None:
    """Write every buffered page as-is — no boilerplate stripping."""
    for page in buffer.pages:
        content = blocks_to_markdown(page.blocks)
        _write_markdown(
            page.md_path, content, logger, stats,
            url=page.url, depth=page.depth, content_type=page.content_type,
            byte_size=page.byte_size, elapsed_ms=page.elapsed_ms,
        )


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
) -> None:
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content, encoding="utf-8")
    except OSError as e:
        logger.error("crawl.write.failed", url=url, output_path=str(md_path), error=str(e))
        stats.errors += 1
        return
    stats.pages_written += 1
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
    *,
    source_doc: str,
    min_image_bytes: int,
) -> str:
    """Fetch an HTML-referenced image, size-check it, write on success.

    Returns one of ``IMG_KEPT`` (written to disk), ``IMG_SIZE_SKIP`` (dropped
    by the ``--min-image-bytes`` filter — caller should strip its Markdown
    reference), or ``IMG_FAILED`` (fetch/status/write failed — reference is
    left alone per pre-existing behavior; a WARN is logged).
    """
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
    logger.info(
        "crawl.image.extract",
        source=source_doc,
        remote_url=remote_url,
        local_path=str(target),
    )
    return IMG_KEPT


def _consider_link(
    link: str,
    cur_depth: int,
    max_depth: int,
    normalized_seeds: list[str],
    visited: set[str],
    queue: deque[tuple[str, int]],
    logger: HcagLogger,
    stats: CrawlStats,
    *,
    source_doc: str,
) -> None:
    next_depth = cur_depth + 1
    if next_depth > max_depth:
        logger.debug(
            "crawl.link.skipped",
            disposition="skipped:depth-cap",
            url=link,
            source=source_doc,
        )
        stats.links_skipped_depth += 1
        return

    try:
        n = normalize_url(link)
    except ValueError:
        logger.warn("crawl.link.unparseable", url=link, source=source_doc)
        stats.warnings += 1
        return

    if not any(n.startswith(seed) for seed in normalized_seeds):
        logger.debug(
            "crawl.link.skipped",
            disposition="skipped:out-of-scope",
            url=link,
            source=source_doc,
        )
        stats.links_skipped_scope += 1
        return

    if n in visited:
        logger.debug(
            "crawl.link.skipped",
            disposition="skipped:visited",
            url=link,
            source=source_doc,
        )
        stats.links_skipped_visited += 1
        return

    visited.add(n)
    queue.append((link, next_depth))
    logger.debug(
        "crawl.link.queued",
        url=link,
        source=source_doc,
        depth=next_depth,
    )
