"""Per-host request pacing for `crawl` (§4.3.5).

A crawler is the only client that asks a site for every page it has, one after
another, as fast as the link graph allows — and the person running it is almost
never the person operating the site. Back-to-back, that traffic is
indistinguishable at the server from a denial-of-service attempt. This module
is what keeps it from being one.

Two properties are load-bearing and easy to get wrong:

- **Per host**, because politeness is owed to a server, not to a run. Two seeds
  on two sites do not queue behind each other.
- **Measured from the end of the previous request**, not its start. A host
  taking six seconds to answer is a host under load, and that is the moment to
  give it more room rather than less. Anchoring at the previous request's start
  would count the site's own slowness as part of the courtesy interval and fire
  again the instant a slow response returned — backing off least exactly when
  it should back off most.
"""

from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..logger import HcagLogger


#: Statuses that mean "you are asking too fast" or "not right now". Both are
#: worth retrying *because* the wait between attempts is enforced; retrying on
#: error without pacing is the standard way a crawler turns a struggling server
#: into an unreachable one.
RETRYABLE_STATUSES = frozenset({429, 503})

#: Milliseconds to wait between requests to one host (§4.3.5). Five seconds is
#: deliberately conservative: a KB is crawled once per revision and nothing
#: downstream is waiting on it, so the cost of being too slow is a coffee while
#: the cost of being too fast is borne by a third party who never agreed to it.
DEFAULT_REQUEST_DELAY_MS = 5000

#: A wait longer than this many times the configured delay is announced on the
#: console as well as logged. An unexplained silent pause looks like a hang,
#: and a `Retry-After` of several minutes is honoured in full (§4.3.5).
_ANNOUNCE_FACTOR = 2


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def parse_retry_after(value: str, *, now: datetime | None = None) -> float | None:
    """`Retry-After` as seconds. Accepts both forms the RFC allows.

    Returns None for anything unparseable — a malformed header is not a reason
    to stall, and the configured delay still applies.
    """
    value = value.strip()
    if not value:
        return None
    try:
        return max(0.0, float(int(value)))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return max(0.0, (when - reference).total_seconds())


class RateLimiter:
    """Enforces `--request-delay-ms` between requests to one host.

    `sleep` and `monotonic` are injectable so tests can assert what *would*
    have been waited without spending the wall-clock on it.
    """

    def __init__(
        self,
        delay_ms: int,
        *,
        logger: HcagLogger | None = None,
        console=None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ) -> None:
        self.delay_ms = max(0, delay_ms)
        self._logger = logger
        self._console = console
        self._sleep = sleep
        self._monotonic = monotonic
        #: host -> the monotonic time before which no request may be sent.
        self._next_allowed: dict[str, float] = {}
        self.total_wait_ms = 0
        self.waits = 0

    # ---- the two hooks httpx calls --------------------------------------

    def before_request(self, url: str) -> None:
        """Block until this host may be asked again.

        A host's first request never waits: there is nothing to be polite about
        yet, and making the seed fetch sit out a delay would only make every
        crawl start slower without protecting anything.
        """
        host = host_of(url)
        earliest = self._next_allowed.get(host)
        if earliest is None:
            return
        wait = earliest - self._monotonic()
        if wait <= 0:
            return

        wait_ms = int(wait * 1000)
        if self._console is not None and wait_ms > self.delay_ms * _ANNOUNCE_FACTOR:
            self._console.waiting(host, wait_ms)
        self._sleep(wait)
        self.total_wait_ms += wait_ms
        self.waits += 1
        if self._logger is not None:
            # DEBUG because on a default run this fires for every request and
            # says nothing surprising. It earns its place when a crawl is
            # slower than the delay alone explains.
            self._logger.debug(
                "crawl.fetch.throttled",
                host=host,
                waited_ms=wait_ms,
                delay_ms=self.delay_ms,
            )

    def after_response(self, url: str, status_code: int, headers) -> None:
        """Start this host's next interval, and honour an explicit ask."""
        host = host_of(url)
        now = self._monotonic()
        next_allowed = now + self.delay_ms / 1000.0

        retry_after_ms: int | None = None
        if status_code in RETRYABLE_STATUSES:
            raw = headers.get("retry-after") if headers is not None else None
            seconds = parse_retry_after(raw) if raw else None
            if seconds is not None:
                retry_after_ms = int(seconds * 1000)
                # The server has stated its own limit. A limiter that ignores an
                # explicit request to slow down is not a limiter, so the longer
                # of the two wins.
                next_allowed = max(next_allowed, now + seconds)

        self._next_allowed[host] = next_allowed
        if retry_after_ms is not None and self._logger is not None:
            applied_ms = int((next_allowed - now) * 1000)
            self._logger.warn(
                "crawl.fetch.retry_after",
                host=host,
                url=url,
                status=status_code,
                retry_after_ms=retry_after_ms,
                applied_ms=applied_ms,
            )
