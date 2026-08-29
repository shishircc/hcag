"""HTTP fetching for `crawl` (§4.2, §4.7 WARN cases).

Thin wrapper around httpx.Client that:
- follows redirects up to a safety cap,
- retries transient network failures a small number of times,
- normalizes the returned content type,
- exposes elapsed time for the INFO fetch line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class FetchResult:
    url: str           # final URL after redirects
    status_code: int
    content_type: str  # lowercased, parameters stripped (e.g. "text/html")
    content: bytes
    elapsed_ms: int


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
    ) -> None:
        self.max_retries = max_retries
        self._client = httpx.Client(
            follow_redirects=True,
            max_redirects=max_redirects,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": user_agent},
        )

    def get(self, url: str) -> FetchResult:
        last_exc: Exception | None = None
        for _ in range(max(1, self.max_retries)):
            try:
                resp = self._client.get(url)
                ct = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                elapsed_ms = int(resp.elapsed.total_seconds() * 1000) if resp.elapsed else 0
                return FetchResult(
                    url=str(resp.url),
                    status_code=resp.status_code,
                    content_type=ct,
                    content=resp.content,
                    elapsed_ms=elapsed_ms,
                )
            except (httpx.HTTPError, OSError) as e:
                last_exc = e
        raise RuntimeError(
            f"fetch failed after {self.max_retries} attempt(s): {url}"
        ) from last_exc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
