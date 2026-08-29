"""Crawl orchestration (§4.3, §4.7).

`crawl(...)` runs a breadth-first traversal from a set of seed URLs. At each
step it:

1. Fetches the document via a `Fetcher` (retries and redirects handled there).
2. Dispatches on content-type: HTML → `convert_html`, PDF → `convert_pdf`.
3. Writes the produced Markdown under `./kb/` mirroring the URL layout.
4. Writes extracted images alongside the Markdown, rewriting refs to local.
5. For HTML, considers each discovered `<a href>` against three gates —
   depth cap, seed prefix scope, visited set — and enqueues the survivors.

All events (fetches, writes, image extractions, per-link skip decisions,
warnings and errors) are structured-logged per §4.7 through the shared
`HcagLogger`. The final `CrawlStats` mirrors the end-of-run summary line so
callers can also inspect it programmatically.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from ..logger import HcagLogger
from .fetch import FetcherProtocol, Fetcher
from .html_conv import convert_html
from .pdf_conv import convert_pdf
from .urls import normalize_url, url_to_output_paths


HTML_TYPES = {"text/html", "application/xhtml+xml"}
PDF_TYPES = {"application/pdf", "application/x-pdf"}


@dataclass
class CrawlStats:
    pages_fetched: int = 0
    pages_written: int = 0
    images_extracted: int = 0
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
) -> CrawlStats:
    stats = CrawlStats()

    if not seeds:
        logger.error("crawl.start.failed", reason="no_seeds")
        stats.errors += 1
        return stats

    try:
        kb_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("crawl.start.failed", reason="output_root_not_writable", path=str(kb_root), error=str(e))
        stats.errors += 1
        return stats

    normalized_seeds = [normalize_url(s) for s in seeds]

    logger.info(
        "crawl.start",
        seeds=list(seeds),
        depth=depth,
        output_root=str(kb_root),
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
            _process(url, cur_depth, depth, normalized_seeds, visited, queue, kb_root, fetcher, logger, stats)

        logger.info(
            "crawl.done",
            pages_fetched=stats.pages_fetched,
            pages_written=stats.pages_written,
            images_extracted=stats.images_extracted,
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

        _write_markdown(
            md_path, converted.markdown, logger, stats,
            url=url, depth=cur_depth, content_type=result.content_type,
            byte_size=len(result.content), elapsed_ms=result.elapsed_ms,
        )

        for remote_url, local_name in converted.images:
            _download_image(remote_url, md_path.parent / local_name, fetcher, logger, stats, source_doc=url)

        for link in converted.links:
            _consider_link(link, cur_depth, max_depth, normalized_seeds, visited, queue, logger, stats, source_doc=url)

    elif result.content_type in PDF_TYPES or url.lower().endswith(".pdf"):
        try:
            converted_pdf = convert_pdf(result.content, doc_basename=doc_basename)
        except Exception as e:
            logger.error("crawl.convert.failed", url=url, kind="pdf", error=str(e))
            stats.errors += 1
            return

        _write_markdown(
            md_path, converted_pdf.markdown, logger, stats,
            url=url, depth=cur_depth, content_type=result.content_type or "application/pdf",
            byte_size=len(result.content), elapsed_ms=result.elapsed_ms,
        )

        for image in converted_pdf.images:
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


def _download_image(
    remote_url: str,
    target: Path,
    fetcher: FetcherProtocol,
    logger: HcagLogger,
    stats: CrawlStats,
    *,
    source_doc: str,
) -> None:
    try:
        result = fetcher.get(remote_url)
    except Exception as e:
        logger.warn("crawl.image.download_failed", source=source_doc, remote_url=remote_url, error=str(e))
        stats.warnings += 1
        return

    if result.status_code >= 400:
        logger.warn(
            "crawl.image.non_2xx",
            source=source_doc,
            remote_url=remote_url,
            status=result.status_code,
        )
        stats.warnings += 1
        return

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
        return

    stats.images_extracted += 1
    logger.info(
        "crawl.image.extract",
        source=source_doc,
        remote_url=remote_url,
        local_path=str(target),
    )


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
