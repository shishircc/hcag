"""Per-host request pacing (§4.3.5).

Time is injected throughout: the point of these tests is what *would* have been
waited, and a test suite that actually sleeps five seconds per request is one
nobody runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from hcag.config import LogConfig
from hcag.crawl.core import crawl
from hcag.crawl.fetch import Fetcher
from hcag.crawl.pacing import (
    DEFAULT_REQUEST_DELAY_MS,
    RETRYABLE_STATUSES,
    RateLimiter,
    parse_retry_after,
)
from hcag.logger import build_logger


class FakeClock:
    """A monotonic clock that only moves when something sleeps on it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _limiter(delay_ms: int = DEFAULT_REQUEST_DELAY_MS, **kw) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    return (
        RateLimiter(delay_ms, sleep=clock.sleep, monotonic=clock.monotonic, **kw),
        clock,
    )


def _ok(limiter: RateLimiter, url: str) -> None:
    limiter.before_request(url)
    limiter.after_response(url, 200, httpx.Headers({}))


# --- The default -----------------------------------------------------------


def test_the_default_is_five_seconds() -> None:
    assert DEFAULT_REQUEST_DELAY_MS == 5000


# --- The wait itself -------------------------------------------------------


def test_the_first_request_to_a_host_does_not_wait() -> None:
    """Nothing to be polite about yet — making every seed fetch sit out a delay
    would slow every crawl down while protecting nothing."""
    limiter, clock = _limiter()
    _ok(limiter, "https://example.gov/a")
    assert clock.slept == []


def test_the_second_request_waits_the_configured_delay() -> None:
    limiter, clock = _limiter()
    _ok(limiter, "https://example.gov/a")
    limiter.before_request("https://example.gov/b")
    assert clock.slept == [5.0]
    assert limiter.total_wait_ms == 5000


def test_the_wait_runs_from_the_end_of_the_previous_request() -> None:
    """A host taking six seconds to answer is a host under load, and that is the
    moment to give it more room. A start-anchored clock would fire immediately."""
    limiter, clock = _limiter()
    limiter.before_request("https://example.gov/a")
    clock.advance(6.0)  # the response took six seconds
    limiter.after_response("https://example.gov/a", 200, httpx.Headers({}))

    limiter.before_request("https://example.gov/b")
    assert clock.slept == [5.0]


def test_time_already_spent_elsewhere_counts_towards_the_wait() -> None:
    """The delay is a floor on the gap, not an unconditional sleep: converting a
    page for three seconds means only two are left to wait."""
    limiter, clock = _limiter()
    _ok(limiter, "https://example.gov/a")
    clock.advance(3.0)
    limiter.before_request("https://example.gov/b")
    assert clock.slept == [pytest.approx(2.0)]


def test_a_host_is_not_waited_on_at_all_once_the_interval_has_passed() -> None:
    limiter, clock = _limiter()
    _ok(limiter, "https://example.gov/a")
    clock.advance(9.0)
    limiter.before_request("https://example.gov/b")
    assert clock.slept == []


def test_hosts_have_separate_budgets() -> None:
    """Politeness is owed to a server, not to a run: two seeds on two sites do
    not queue behind each other."""
    limiter, clock = _limiter()
    _ok(limiter, "https://a.gov/one")
    _ok(limiter, "https://b.gov/one")  # different host — no wait
    assert clock.slept == []

    limiter.before_request("https://a.gov/two")
    assert clock.slept == [5.0]


def test_the_host_comparison_ignores_case() -> None:
    limiter, clock = _limiter()
    _ok(limiter, "https://Example.GOV/a")
    limiter.before_request("https://example.gov/b")
    assert clock.slept == [5.0]


def test_a_zero_delay_never_waits() -> None:
    """Legitimate against a host the operator runs."""
    limiter, clock = _limiter(0)
    for i in range(5):
        _ok(limiter, f"https://example.gov/{i}")
    assert clock.slept == []
    assert limiter.total_wait_ms == 0


def test_total_wait_accumulates_for_the_run_summary() -> None:
    limiter, clock = _limiter(2000)
    for i in range(4):
        _ok(limiter, f"https://example.gov/{i}")
    assert limiter.waits == 3
    assert limiter.total_wait_ms == 6000


# --- Retry-After -----------------------------------------------------------


def test_retry_after_wins_when_it_asks_for_longer() -> None:
    """The server has stated its own limit, and a limiter that ignores an
    explicit request to slow down is not a limiter."""
    limiter, clock = _limiter()
    limiter.before_request("https://example.gov/a")
    limiter.after_response("https://example.gov/a", 429, httpx.Headers({"retry-after": "30"}))

    limiter.before_request("https://example.gov/b")
    assert clock.slept == [30.0]


def test_the_configured_delay_wins_when_retry_after_is_shorter() -> None:
    limiter, clock = _limiter()
    limiter.before_request("https://example.gov/a")
    limiter.after_response("https://example.gov/a", 503, httpx.Headers({"retry-after": "1"}))

    limiter.before_request("https://example.gov/b")
    assert clock.slept == [5.0]


def test_retry_after_on_a_2xx_is_ignored() -> None:
    """It only means anything alongside a status that says "slow down"."""
    limiter, clock = _limiter()
    limiter.before_request("https://example.gov/a")
    limiter.after_response("https://example.gov/a", 200, httpx.Headers({"retry-after": "600"}))

    limiter.before_request("https://example.gov/b")
    assert clock.slept == [5.0]


def test_retry_after_applies_only_to_the_host_that_sent_it() -> None:
    limiter, clock = _limiter()
    limiter.before_request("https://slow.gov/a")
    limiter.after_response("https://slow.gov/a", 429, httpx.Headers({"retry-after": "60"}))
    _ok(limiter, "https://other.gov/a")
    assert clock.slept == []


@pytest.mark.parametrize("raw,expected", [("30", 30.0), ("0", 0.0), (" 12 ", 12.0)])
def test_retry_after_parses_seconds(raw: str, expected: float) -> None:
    assert parse_retry_after(raw) == expected


def test_retry_after_parses_an_http_date() -> None:
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
    later = now + timedelta(seconds=45)
    header = later.strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(header, now=now) == pytest.approx(45.0, abs=1)


def test_a_date_in_the_past_is_not_a_negative_wait() -> None:
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
    past = (now - timedelta(seconds=60)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(past, now=now) == 0.0


@pytest.mark.parametrize("raw", ["", "   ", "soon", "-", "1.5.2"])
def test_an_unparseable_retry_after_is_ignored(raw: str) -> None:
    """A malformed header is not a reason to stall; the configured delay still
    applies."""
    assert parse_retry_after(raw) is None


def test_an_unparseable_retry_after_still_leaves_the_normal_delay() -> None:
    limiter, clock = _limiter()
    limiter.before_request("https://example.gov/a")
    limiter.after_response("https://example.gov/a", 429, httpx.Headers({"retry-after": "soon"}))
    limiter.before_request("https://example.gov/b")
    assert clock.slept == [5.0]


# --- Logging and console ---------------------------------------------------


def test_a_long_wait_is_announced_on_the_console() -> None:
    """An unexplained pause of minutes is indistinguishable from a hang."""

    class SpyConsole:
        def __init__(self) -> None:
            self.said: list[tuple[str, int]] = []

        def waiting(self, host: str, wait_ms: int) -> None:
            self.said.append((host, wait_ms))

    console = SpyConsole()
    limiter, _ = _limiter(console=console)
    limiter.before_request("https://example.gov/a")
    limiter.after_response("https://example.gov/a", 429, httpx.Headers({"retry-after": "120"}))
    limiter.before_request("https://example.gov/b")

    assert console.said == [("example.gov", 120000)]


def test_an_ordinary_wait_is_not_announced() -> None:
    """Saying "waiting 5s" before every line would drown the crawl in its own
    politeness."""

    class SpyConsole:
        def __init__(self) -> None:
            self.said: list[tuple[str, int]] = []

        def waiting(self, host: str, wait_ms: int) -> None:
            self.said.append((host, wait_ms))

    console = SpyConsole()
    limiter, _ = _limiter(console=console)
    _ok(limiter, "https://example.gov/a")
    limiter.before_request("https://example.gov/b")
    assert console.said == []


def test_retry_after_is_logged_at_warn(tmp_path: Path) -> None:
    logger = build_logger(
        LogConfig(file_path=str(tmp_path / "crawl.log"), level="INFO"),
        name=f"test.crawl.pacing.{tmp_path.name}",
    )
    limiter, _ = _limiter(logger=logger)
    limiter.before_request("https://example.gov/a")
    limiter.after_response("https://example.gov/a", 429, httpx.Headers({"retry-after": "30"}))

    log = (tmp_path / "crawl.log").read_text(encoding="utf-8")
    assert "crawl.fetch.retry_after" in log
    assert '"retry_after_ms": 30000' in log
    assert '"applied_ms": 30000' in log


# --- The fetcher wiring ----------------------------------------------------


def _fetcher_on(handler, limiter: RateLimiter, **kw) -> Fetcher:
    """A Fetcher whose transport is a mock, so the event hooks are exercised
    exactly as httpx would call them — including once per redirect hop."""
    fetcher = Fetcher(rate_limiter=limiter, **kw)
    fetcher._client._transport = httpx.MockTransport(handler)
    return fetcher


def test_every_request_is_paced_including_asset_fetches() -> None:
    limiter, clock = _limiter()
    fetcher = _fetcher_on(
        lambda req: httpx.Response(200, text="ok", headers={"content-type": "text/html"}),
        limiter,
    )
    fetcher.get("https://example.gov/page")
    fetcher.get("https://example.gov/doc.pdf")
    fetcher.close()
    assert clock.slept == [5.0]


def test_each_redirect_hop_takes_its_own_turn() -> None:
    """A four-hop redirect is four requests. A delay wrapped around `get` would
    issue them back to back and call it one."""

    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.path.rsplit("/", 1)[-1])
        if n < 3:
            return httpx.Response(302, headers={"location": f"https://example.gov/{n + 1}"})
        return httpx.Response(200, text="ok", headers={"content-type": "text/html"})

    limiter, clock = _limiter()
    fetcher = _fetcher_on(handler, limiter)
    fetcher.get("https://example.gov/0")
    fetcher.close()

    # Four requests, three of them behind a delay.
    assert clock.slept == [5.0, 5.0, 5.0]


def test_a_rate_limited_response_is_retried_behind_the_servers_wait() -> None:
    """Dropping a URL on the first 429 silently loses a page from the KB."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(429, headers={"retry-after": "20"})
        return httpx.Response(200, text="ok", headers={"content-type": "text/html"})

    limiter, clock = _limiter()
    fetcher = _fetcher_on(handler, limiter)
    result = fetcher.get("https://example.gov/page")
    fetcher.close()

    assert result.status_code == 200
    assert clock.slept == [20.0]


def test_a_host_that_keeps_refusing_runs_out_of_retries() -> None:
    """Returned as it stands, so the caller drops it like any other non-2xx."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(503, headers={"retry-after": "1"})

    limiter, _ = _limiter()
    fetcher = _fetcher_on(handler, limiter, max_retries=3)
    result = fetcher.get("https://example.gov/page")
    fetcher.close()

    assert result.status_code == 503
    assert len(calls) == 3


def test_a_non_retryable_status_is_returned_on_the_first_try() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    limiter, _ = _limiter()
    fetcher = _fetcher_on(handler, limiter)
    result = fetcher.get("https://example.gov/gone")
    fetcher.close()

    assert result.status_code == 404
    assert len(calls) == 1
    assert 404 not in RETRYABLE_STATUSES


# --- Wiring through `crawl` ------------------------------------------------


def test_a_negative_delay_fails_at_startup(tmp_path: Path) -> None:
    logger = build_logger(
        LogConfig(file_path=str(tmp_path / "crawl.log"), level="INFO"),
        name=f"test.crawl.pacing.neg.{tmp_path.name}",
    )
    stats = crawl(
        seeds=["https://example.gov/"],
        depth=0,
        kb_root=tmp_path / "kb",
        logger=logger,
        request_delay_ms=-1,
    )
    assert stats.errors == 1
    assert stats.pages_fetched == 0
    log = (tmp_path / "crawl.log").read_text(encoding="utf-8")
    assert "negative_request_delay" in log


def test_disabling_the_delay_is_warned_about_once(tmp_path: Path) -> None:
    """The one setting whose misuse is paid for by somebody else."""

    class NullFetcher:
        def get(self, url: str):  # pragma: no cover - never reached
            raise AssertionError("no fetch expected")

        def close(self) -> None:
            pass

    logger = build_logger(
        LogConfig(file_path=str(tmp_path / "crawl.log"), level="INFO"),
        name=f"test.crawl.pacing.zero.{tmp_path.name}",
    )
    stats = crawl(
        seeds=[],
        depth=0,
        kb_root=tmp_path / "kb",
        logger=logger,
        request_delay_ms=0,
    )
    # No seeds: the run stops before pacing matters, and the warning is not
    # emitted before the fatal check.
    assert stats.errors == 1

    stats = crawl(
        seeds=["https://example.gov/"],
        depth=0,
        kb_root=tmp_path / "kb",
        logger=logger,
        fetcher=NullFetcher(),  # type: ignore[arg-type]
        request_delay_ms=0,
    )
    log = (tmp_path / "crawl.log").read_text(encoding="utf-8")
    assert log.count("crawl.pacing.disabled") == 1


def test_the_run_records_the_delay_and_the_time_it_cost(tmp_path: Path) -> None:
    class StubFetcher:
        def get(self, url: str):
            from hcag.crawl.fetch import FetchResult

            return FetchResult(
                url=url, status_code=200, content_type="text/html",
                content=b"<html><body><p>Hello there, this is a page.</p></body></html>",
                elapsed_ms=1,
            )

        def close(self) -> None:
            pass

    logger = build_logger(
        LogConfig(file_path=str(tmp_path / "crawl.log"), level="INFO"),
        name=f"test.crawl.pacing.done.{tmp_path.name}",
    )
    stats = crawl(
        seeds=["https://example.gov/a"],
        depth=0,
        kb_root=tmp_path / "kb",
        logger=logger,
        fetcher=StubFetcher(),  # type: ignore[arg-type]
        request_delay_ms=250,
    )
    log = (tmp_path / "crawl.log").read_text(encoding="utf-8")
    assert '"request_delay_ms": 250' in log
    assert '"throttle_wait_ms":' in log
    assert '"elapsed_ms":' in log
    assert stats.errors == 0
