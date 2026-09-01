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

from ..logger import HcagLogger
from .fetch import Fetcher, FetcherProtocol
from .html_conv import DEFAULT_MIN_EXTRACT_CHARS, FALLBACK_DISABLED, convert_html
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

    owns_fetcher = fetcher is None
    if fetcher is None:
        fetcher = Fetcher()

    try:
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        for seed in seeds:
            n = normalize_url(seed)
            if n in visited:
                continue
            visited.add(n)
            queue.append((seed, 0))

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
                no_extract=no_extract,
                extract_favor=extract_favor,
                min_extract_chars=min_extract_chars,
                min_image_bytes=min_image_bytes,
            )

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
    *,
    no_extract: bool,
    extract_favor: str,
    min_extract_chars: int,
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
            logger.debug(
                "crawl.extract.detail",
                url=url,
                favor=extract_favor,
                title_synthesized=converted.title_synthesized,
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
                source_doc=url,
                min_image_bytes=min_image_bytes,
            )
            if outcome == IMG_SIZE_SKIP:
                markdown = _remove_image_reference(markdown, local_name)

        _write_markdown(
            md_path, markdown, logger, stats,
            url=url, depth=cur_depth, content_type=result.content_type,
            byte_size=len(result.content), elapsed_ms=result.elapsed_ms,
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
