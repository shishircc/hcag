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
    # Bypass the 10 KB size filter — this test verifies the extraction path,
    # not the size gate.
    stats = crawl(
        [seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=fetcher,
        min_image_bytes=0,
    )

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
# --min-image-bytes filter (§4.4.3)
# ---------------------------------------------------------------------------


def test_remove_image_reference_strips_various_shapes() -> None:
    from hcag.crawl.core import _remove_image_reference

    md = (
        "Intro paragraph with ![inline](p-foo.jpg) inside.\n"
        "\n"
        "![](p-bar.png)\n"
        "\n"
        '![Alt "quoted"](p-baz.gif "title text")\n'
        "\n"
        "Trailing paragraph."
    )
    md = _remove_image_reference(md, "p-foo.jpg")
    md = _remove_image_reference(md, "p-bar.png")
    md = _remove_image_reference(md, "p-baz.gif")
    assert "p-foo.jpg" not in md
    assert "p-bar.png" not in md
    assert "p-baz.gif" not in md
    # Two spaces because the ref sat between two spaces and only the ref itself
    # is stripped — surrounding whitespace is left alone.
    assert "Intro paragraph with  inside." in md
    assert "Trailing paragraph." in md


def test_small_image_is_dropped_by_default(tmp_path: Path) -> None:
    """Tiny image (< 10 KB default) → not written; Markdown reference removed."""
    seed = "https://ex.com/page.html"
    small_img = b"\x89PNG\r\n\x1a\n" + b"tiny"  # 12 bytes
    pages = {
        seed: _Canned(b'<html><body><img src="/logo.png" /></body></html>'),
        "https://ex.com/logo.png": _Canned(small_img, content_type="image/png"),
    }
    kb = tmp_path / "kb"
    stats = crawl(
        [seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages),
    )
    assert stats.images_extracted == 0
    assert stats.images_skipped_small == 1
    assert not (kb / "ex.com" / "page-logo.png").exists()
    md = (kb / "ex.com" / "page.md").read_text()
    assert "page-logo.png" not in md
    # Log line surfaces the skip with size + threshold.
    skips = [e for e in _read_log(tmp_path) if e["event"] == "crawl.image.skipped_small"]
    assert len(skips) == 1
    assert skips[0]["byte_size"] == 12
    assert skips[0]["threshold"] == 10240


def test_large_image_is_kept_at_default_threshold(tmp_path: Path) -> None:
    """Image ≥ 10 KB → written; Markdown reference preserved."""
    seed = "https://ex.com/page.html"
    big_img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20_000
    pages = {
        seed: _Canned(b'<html><body><img src="/chart.png" /></body></html>'),
        "https://ex.com/chart.png": _Canned(big_img, content_type="image/png"),
    }
    kb = tmp_path / "kb"
    stats = crawl(
        [seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages),
    )
    assert stats.images_extracted == 1
    assert stats.images_skipped_small == 0
    assert (kb / "ex.com" / "page-chart.png").is_file()
    md = (kb / "ex.com" / "page.md").read_text()
    assert "page-chart.png" in md


def test_min_image_bytes_zero_disables_filter(tmp_path: Path) -> None:
    """--min-image-bytes 0 → every image kept regardless of size."""
    seed = "https://ex.com/page.html"
    tiny = b"\x89PNG\r\n\x1a\n"  # 8 bytes
    pages = {
        seed: _Canned(b'<html><body><img src="/ico.png" /></body></html>'),
        "https://ex.com/ico.png": _Canned(tiny, content_type="image/png"),
    }
    kb = tmp_path / "kb"
    stats = crawl(
        [seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages),
        min_image_bytes=0,
    )
    assert stats.images_extracted == 1
    assert stats.images_skipped_small == 0
    assert (kb / "ex.com" / "page-ico.png").is_file()


def test_size_filter_removes_ref_for_html_but_keeps_other_images(tmp_path: Path) -> None:
    """A page with one small + one large image keeps only the large one and its ref."""
    seed = "https://ex.com/page.html"
    small = b"\x89PNG\r\n\x1a\n" + b"tiny"
    large = b"\x89PNG\r\n\x1a\n" + b"\x00" * 15_000
    pages = {
        seed: _Canned(
            b'<html><body>'
            b'<img src="/icon.png" /><img src="/diagram.png" />'
            b'</body></html>'
        ),
        "https://ex.com/icon.png": _Canned(small, content_type="image/png"),
        "https://ex.com/diagram.png": _Canned(large, content_type="image/png"),
    }
    kb = tmp_path / "kb"
    stats = crawl(
        [seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages),
    )
    assert stats.images_extracted == 1
    assert stats.images_skipped_small == 1
    assert not (kb / "ex.com" / "page-icon.png").exists()
    assert (kb / "ex.com" / "page-diagram.png").is_file()
    md = (kb / "ex.com" / "page.md").read_text()
    assert "page-icon.png" not in md
    assert "page-diagram.png" in md


# ---------------------------------------------------------------------------
# Main-content extraction (§4.4.1) — end-to-end
# ---------------------------------------------------------------------------


def _templated_page(topic: str) -> bytes:
    """A realistic page: chrome around an article long enough to extract."""
    return (
        "<html><head><title>{t}</title></head><body>"
        "<header><a href='/'><img src='/static/logo.png' alt='logo'/></a>"
        "<nav><a href='/a'>Alpha</a> <a href='/b'>Beta</a></nav></header>"
        "<main><article><h1>{t}</h1>"
        "<p>Everything you need to know about {t} in one place. This body is long "
        "enough that the extractor treats it as the page's main content rather than "
        "discarding it as an index page, and it mentions {t} more than once.</p>"
        "<p>Applications for {t} are assessed against published criteria, and the "
        "outcome is communicated by email within three weeks of submission.</p>"
        "<img src='/media/chart.png' alt='{t} chart'/>"
        "</article></main>"
        "<footer><p>Copyright 2026 Widgets Inc. All rights reserved.</p></footer>"
        "</body></html>"
    ).format(t=topic).encode()


def test_extraction_strips_chrome_end_to_end(tmp_path: Path) -> None:
    seed = "https://docs.ex.com/alpha"
    big_chart = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20_000
    pages = {
        seed: _Canned(_templated_page("Alpha")),
        "https://docs.ex.com/media/chart.png": _Canned(big_chart, content_type="image/png"),
    }
    fetcher = FakeFetcher(pages)
    kb = tmp_path / "kb"
    stats = crawl([seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=fetcher)

    assert stats.pages_written == 1
    assert stats.pages_extracted == 1
    assert stats.pages_fallback == 0

    md = (kb / "docs.ex.com" / "alpha.md").read_text()
    assert "# Alpha" in md
    assert "Everything you need to know about Alpha" in md
    assert "Copyright 2026 Widgets Inc" not in md
    assert "Beta" not in md  # nav link text is gone from the output

    # The chrome logo is never even requested; the content image is.
    assert "https://docs.ex.com/static/logo.png" not in fetcher.requested
    assert "https://docs.ex.com/media/chart.png" in fetcher.requested
    assert (kb / "docs.ex.com" / "alpha-chart.png").is_file()

    ok = [e for e in _read_log(tmp_path) if e["event"] == "crawl.extract.ok"]
    assert len(ok) == 1
    assert ok[0]["url"] == seed
    assert ok[0]["retained_pct"] > 0


def test_nav_links_are_still_followed_after_extraction(tmp_path: Path) -> None:
    """Nav is stripped from the output but still drives traversal (§4.3.1)."""
    seed = "https://docs.ex.com/"
    pages = {
        seed: _Canned(_templated_page("Alpha")),
        "https://docs.ex.com/a": _Canned(_templated_page("Alpha topic")),
        "https://docs.ex.com/b": _Canned(_templated_page("Beta topic")),
        "https://docs.ex.com/media/chart.png": _Canned(b"tiny", content_type="image/png"),
        "https://docs.ex.com/static/logo.png": _Canned(b"tiny", content_type="image/png"),
    }
    fetcher = FakeFetcher(pages)
    kb = tmp_path / "kb"
    stats = crawl([seed], depth=1, kb_root=kb, logger=_logger_for(tmp_path), fetcher=fetcher)

    assert stats.pages_written == 3
    assert "https://docs.ex.com/a" in fetcher.requested
    assert "https://docs.ex.com/b" in fetcher.requested


def test_unextractable_page_falls_back_and_warns(tmp_path: Path) -> None:
    seed = "https://docs.ex.com/index"
    pages = {seed: _Canned(b"<html><body><nav>Home | Docs | About</nav></body></html>")}
    kb = tmp_path / "kb"
    stats = crawl([seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages))

    assert stats.pages_written == 1
    assert stats.pages_fallback == 1
    assert stats.pages_extracted == 0
    # Content is kept, chrome and all — a dirty page beats a missing one.
    assert "Home | Docs | About" in (kb / "docs.ex.com" / "index.md").read_text()

    fallbacks = [e for e in _read_log(tmp_path) if e["event"] == "crawl.extract.fallback"]
    assert len(fallbacks) == 1
    assert fallbacks[0]["reason"] in {"no_output", "too_short"}


def test_no_extract_writes_whole_dom(tmp_path: Path) -> None:
    seed = "https://docs.ex.com/alpha"
    pages = {
        seed: _Canned(_templated_page("Alpha")),
        "https://docs.ex.com/media/chart.png": _Canned(b"tiny", content_type="image/png"),
        "https://docs.ex.com/static/logo.png": _Canned(b"tiny", content_type="image/png"),
    }
    kb = tmp_path / "kb"
    stats = crawl(
        [seed], depth=0, kb_root=kb, logger=_logger_for(tmp_path), fetcher=FakeFetcher(pages),
        no_extract=True,
    )

    assert stats.pages_extracted == 0
    assert stats.pages_fallback == 1
    md = (kb / "docs.ex.com" / "alpha.md").read_text()
    assert "Copyright 2026 Widgets Inc" in md

    reasons = [
        e.get("reason") for e in _read_log(tmp_path)
        if e["event"] == "crawl.extract.fallback"
    ]
    assert reasons == ["disabled"]
