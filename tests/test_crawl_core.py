"""End-to-end `crawl` orchestration (§4.3, §4.5, §4.7).

Uses a `FakeFetcher` to serve canned pages so the traversal, scope, depth,
visited-dedup, image extraction, and output layout can be exercised without
touching the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from hcag.config import LogConfig
from hcag.crawl.core import crawl
from hcag.crawl.fetch import FetchResult
from hcag.logger import build_logger


@dataclass
class _Canned:
    content: bytes
    content_type: str = "text/html"
    status: int = 200


class FakeFetcher:
    def __init__(self, pages: dict[str, _Canned]) -> None:
        self._pages = pages
        self.requested: list[str] = []

    def get(self, url: str) -> FetchResult:
        self.requested.append(url)
        if url not in self._pages:
            raise RuntimeError(f"unexpected fetch: {url}")
        page = self._pages[url]
        return FetchResult(
            url=url,
            status_code=page.status,
            content_type=page.content_type,
            content=page.content,
            elapsed_ms=1,
        )

    def close(self) -> None:
        pass


def _logger_for(tmp_path: Path):
    cfg = LogConfig(file_path=str(tmp_path / "crawl.log"), level="DEBUG")  # type: ignore[arg-type]
    return build_logger(cfg, name=f"crawl.test.{tmp_path.name}")


def _read_log(tmp_path: Path) -> list[dict]:
    lines = (tmp_path / "crawl.log").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_seed_only_at_depth_zero(tmp_path: Path) -> None:
    seed = "https://ex.com/index.html"
    pages = {
        seed: _Canned(b'<html><body><a href="/other.html">Other</a></body></html>'),
    }
    kb = tmp_path / "kb"
    stats = crawl([seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages))

    assert stats.pages_written == 1
    assert stats.links_skipped_depth == 1
    assert (kb / "ex.com" / "index.md").is_file()


def test_bfs_follows_links_within_scope(tmp_path: Path) -> None:
    seed = "https://ex.com/docs/"
    pages = {
        seed: _Canned(
            b'<html><body>'
            b'<a href="/docs/a.html">A</a>'
            b'<a href="/other/z.html">Z</a>'  # out of scope (not under /docs/)
            b'</body></html>'
        ),
        "https://ex.com/docs/a.html": _Canned(
            b'<html><body><a href="/docs/b.html">B</a></body></html>'
        ),
        "https://ex.com/docs/b.html": _Canned(b"<html><body>terminal</body></html>"),
    }
    fetcher = FakeFetcher(pages)
    kb = tmp_path / "kb"
    stats = crawl([seed], depth=5, kb_root=kb, logger=_logger_for(tmp_path), fetcher=fetcher)

    assert stats.pages_written == 3
    assert stats.links_skipped_scope == 1
    assert (kb / "ex.com" / "docs" / "index.md").is_file()
    assert (kb / "ex.com" / "docs" / "a.md").is_file()
    assert (kb / "ex.com" / "docs" / "b.md").is_file()
    # out-of-scope link was never fetched
    assert "https://ex.com/other/z.html" not in fetcher.requested


def test_visited_dedup_prevents_refetch_of_cycle(tmp_path: Path) -> None:
    # Seed is the site root so both pages are in scope.
    seed = "https://ex.com/"
    pages = {
        seed: _Canned(b'<html><body><a href="/a.html">A</a></body></html>'),
        "https://ex.com/a.html": _Canned(b'<html><body><a href="/b.html">B</a></body></html>'),
        "https://ex.com/b.html": _Canned(b'<html><body><a href="/a.html">A</a></body></html>'),
    }
    fetcher = FakeFetcher(pages)
    kb = tmp_path / "kb"
    stats = crawl([seed], depth=5, kb_root=kb, logger=_logger_for(tmp_path), fetcher=fetcher)

    assert stats.pages_fetched == 3
    assert stats.pages_written == 3
    assert stats.links_skipped_visited >= 1
    # Each URL fetched at most once even though b.html links back to a.html
    assert fetcher.requested.count("https://ex.com/a.html") == 1
    assert fetcher.requested.count("https://ex.com/b.html") == 1


def test_depth_cap_stops_at_max_depth(tmp_path: Path) -> None:
    seed = "https://ex.com/"
    pages = {
        seed: _Canned(b'<html><body><a href="/1.html">1</a></body></html>'),
        "https://ex.com/1.html": _Canned(b'<html><body><a href="/2.html">2</a></body></html>'),
        "https://ex.com/2.html": _Canned(b'<html><body><a href="/3.html">3</a></body></html>'),
        "https://ex.com/3.html": _Canned(b"<html><body>bottom</body></html>"),
    }
    fetcher = FakeFetcher(pages)
    kb = tmp_path / "kb"
    stats = crawl([seed], depth=2, kb_root=kb, logger=_logger_for(tmp_path), fetcher=fetcher)

    # depth 0 (seed), depth 1, depth 2 all fetched; the link found on depth-2
    # page pointing at depth-3 is NOT followed.
    assert stats.pages_written == 3
    assert "https://ex.com/3.html" not in fetcher.requested
    assert stats.links_skipped_depth == 1


def test_image_extracted_and_written_next_to_markdown(tmp_path: Path) -> None:
    seed = "https://ex.com/page.html"
    apple_bytes = b"\x89PNG\r\n\x1a\n" + b"faked"
    pages = {
        seed: _Canned(b'<html><body><img src="/media/apple.jpg" /></body></html>'),
        "https://ex.com/media/apple.jpg": _Canned(apple_bytes, content_type="image/jpeg"),
    }
    fetcher = FakeFetcher(pages)
    kb = tmp_path / "kb"
    stats = crawl([seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=fetcher)

    assert stats.images_extracted == 1
    img_path = kb / "ex.com" / "page-apple.jpg"
    assert img_path.is_file()
    assert img_path.read_bytes() == apple_bytes
    md_body = (kb / "ex.com" / "page.md").read_text()
    assert "page-apple.jpg" in md_body
    assert "https://ex.com/media/apple.jpg" not in md_body


def test_unsupported_content_type_is_warned_and_skipped(tmp_path: Path) -> None:
    seed = "https://ex.com/thing"
    pages = {seed: _Canned(b"binary", content_type="application/octet-stream")}
    kb = tmp_path / "kb"
    stats = crawl([seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages))
    assert stats.pages_written == 0
    assert stats.warnings >= 1


def test_non_2xx_is_warned_and_skipped(tmp_path: Path) -> None:
    seed = "https://ex.com/gone"
    pages = {seed: _Canned(b"nope", status=404)}
    kb = tmp_path / "kb"
    stats = crawl([seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages))
    assert stats.pages_written == 0
    assert stats.warnings >= 1


def test_no_seeds_is_start_error(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    stats = crawl([], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher({}))
    assert stats.errors == 1


def test_log_records_start_written_and_done(tmp_path: Path) -> None:
    seed = "https://ex.com/x.html"
    pages = {seed: _Canned(b"<html><body>hi</body></html>")}
    kb = tmp_path / "kb"
    crawl([seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages))
    events = [e["event"] for e in _read_log(tmp_path)]
    assert "crawl.start" in events
    assert "crawl.page.written" in events
    assert "crawl.done" in events


def test_log_records_skip_disposition(tmp_path: Path) -> None:
    seed = "https://ex.com/docs/"
    pages = {
        seed: _Canned(
            b'<html><body>'
            b'<a href="/other/z.html">Z</a>'
            b'</body></html>'
        ),
    }
    kb = tmp_path / "kb"
    crawl([seed], depth=5, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages))
    events = _read_log(tmp_path)
    skip = [e for e in events if e["event"] == "crawl.link.skipped"]
    assert any(e.get("disposition") == "skipped:out-of-scope" for e in skip)


# ---------------------------------------------------------------------------
# Boilerplate detection (§4.4.4) — end-to-end
# ---------------------------------------------------------------------------


def _templated_page(unique_body: str) -> bytes:
    """Small HTML page with the same nav header + footer around unique body."""
    return (
        b"<html><body>"
        b"<nav>Nav | Home | Docs | About</nav>"
        b"<h1>" + unique_body.encode() + b"</h1>"
        b"<p>Details about " + unique_body.encode() + b".</p>"
        b"<footer>Copyright 2026 Widgets Inc.</footer>"
        b"</body></html>"
    )


def test_boilerplate_strips_shared_nav_and_footer(tmp_path: Path) -> None:
    seeds = ["https://docs.ex.com/a", "https://docs.ex.com/b", "https://docs.ex.com/c"]
    pages = {
        seeds[0]: _Canned(_templated_page("Alpha")),
        seeds[1]: _Canned(_templated_page("Beta")),
        seeds[2]: _Canned(_templated_page("Gamma")),
    }
    kb = tmp_path / "kb"
    stats = crawl(
        seeds, depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages),
    )
    assert stats.pages_written == 3
    assert stats.boilerplate_pages_scanned == 3
    # Nav should be stripped from every page's Markdown, footer likewise.
    for name in ("a", "b", "c"):
        md = (kb / "docs.ex.com" / f"{name}.md").read_text()
        assert "Home | Docs | About" not in md
        assert "Copyright 2026 Widgets Inc" not in md
    # Unique body survives in each page.
    assert "Alpha" in (kb / "docs.ex.com" / "a.md").read_text()
    assert "Beta" in (kb / "docs.ex.com" / "b.md").read_text()
    assert "Gamma" in (kb / "docs.ex.com" / "c.md").read_text()
    # Header + footer accounting.
    assert stats.boilerplate_header_blocks_stripped >= 3
    assert stats.boilerplate_footer_blocks_stripped >= 3


def test_boilerplate_disabled_writes_verbatim(tmp_path: Path) -> None:
    seeds = ["https://docs.ex.com/a", "https://docs.ex.com/b", "https://docs.ex.com/c"]
    pages = {u: _Canned(_templated_page(u.rsplit("/", 1)[1])) for u in seeds}
    kb = tmp_path / "kb"
    stats = crawl(
        seeds, depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages),
        no_boilerplate=True,
    )
    assert stats.pages_written == 3
    md = (kb / "docs.ex.com" / "a.md").read_text()
    # Nav and footer are still present because detection was disabled.
    assert "Home | Docs | About" in md
    assert "Copyright 2026 Widgets Inc" in md
    assert stats.boilerplate_headers_detected == 0
    assert stats.boilerplate_footers_detected == 0
    # Log carries the "disabled" reason.
    reasons = [
        e.get("reason") for e in _read_log(tmp_path)
        if e["event"] == "crawl.boilerplate.skipped"
    ]
    assert "disabled" in reasons


def test_boilerplate_skipped_when_corpus_below_min(tmp_path: Path) -> None:
    seed = "https://docs.ex.com/only"
    pages = {seed: _Canned(_templated_page("Only"))}
    kb = tmp_path / "kb"
    stats = crawl(
        [seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages),
    )
    assert stats.pages_written == 1
    md = (kb / "docs.ex.com" / "only.md").read_text()
    # Only one page — detection can't run, so verbatim.
    assert "Home | Docs | About" in md
    reasons = [
        e.get("reason") for e in _read_log(tmp_path)
        if e["event"] == "crawl.boilerplate.skipped"
    ]
    assert "min_corpus" in reasons
