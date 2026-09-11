"""HTTP fetching for `crawl` (§4.2, §4.3.5, §4.7 WARN cases).

Thin wrapper around httpx.Client that:
- follows redirects up to a safety cap,
- paces requests per host (§4.3.5),
- retries transient network failures and rate-limit responses a small number
  of times,
- normalizes the returned content type,
- exposes elapsed time for the INFO fetch line.

The pacing rides on httpx's request/response event hooks rather than sitting in
`get`, because that is the only place that sees *every* request: each hop of a
redirect chain fires the hooks separately, and so does each retry. A delay
applied around `get` would let a four-hop redirect issue four requests back to
back and call it one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from .pacing import RETRYABLE_STATUSES, RateLimiter


@dataclass
class FetchResult:
    url: str           # final URL after redirects
    status_code: int
    content_type: str  # lowercased, parameters stripped (e.g. "text/html")
    content: bytes
    elapsed_ms: int


def _elapsed_ms(resp: httpx.Response) -> int:
    """Fetch duration for the INFO line, or 0 if httpx cannot tell us.

    `Response.elapsed` raises unless the response was read or closed, which is
    true of every response this client produces and not true of one handed back
    by a stubbed transport. A timing number is telemetry; it must never be the
    reason a page fails to be fetched.
    """
    try:
        return int(resp.elapsed.total_seconds() * 1000)
    except RuntimeError:
        return 0


class FetcherProtocol(Protocol):
    def get(self, url: str) -> FetchResult: ...
    def close(self) -> None: ...


class Fetcher:
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        max_redirects: int = 10,
        user_agent: str = "hcag-crawl/0.1",
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.rate_limiter = rate_limiter
        hooks: dict[str, list] = {}
        if rate_limiter is not None:
            hooks = {
                "request": [lambda r: rate_limiter.before_request(str(r.url))],
                "response": [
                    lambda r: rate_limiter.after_response(
                        str(r.request.url), r.status_code, r.headers
                    )
                ],
            }
        self._client = httpx.Client(
            follow_redirects=True,
            max_redirects=max_redirects,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": user_agent},
            event_hooks=hooks,
        )

    def get(self, url: str) -> FetchResult:
        last_exc: Exception | None = None
        last_rate_limited: FetchResult | None = None
        attempts = max(1, self.max_retries)
        for attempt in range(attempts):
            try:
                resp = self._client.get(url)
                ct = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                elapsed_ms = _elapsed_ms(resp)
                result = FetchResult(
                    url=str(resp.url),
                    status_code=resp.status_code,
                    content_type=ct,
                    content=resp.content,
                    elapsed_ms=elapsed_ms,
                )
                # A 429/503 is the server saying "too fast" or "not now", and
                # dropping the URL on the first one silently loses a page from
                # the KB. Retrying is safe here and only here: the response hook
                # has already pushed this host's next slot out, by `Retry-After`
                # when the server sent one, so the next attempt waits rather
                # than piling on. Retries exhausted, the result is returned as
                # it stands and the caller drops it like any other non-2xx.
                if result.status_code in RETRYABLE_STATUSES and attempt + 1 < attempts:
                    last_rate_limited = result
                    continue
                return result
            except (httpx.HTTPError, OSError) as e:
                last_exc = e
        if last_rate_limited is not None:
            return last_rate_limited
        raise RuntimeError(
            f"fetch failed after {self.max_retries} attempt(s): {url}"
        ) from last_exc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
