"""Human-readable console output for `crawl` (§4.7.1).

Separate surface from the JSON-lines log (§4.7), and neither replaces the
other: the log answers "what happened, exactly" after the fact; this answers
"what is it doing now, and what did I end up with" while someone watches.

Both are derived from the same counters, so they cannot disagree.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from urllib.parse import urlparse

#: Skip reasons, in the order they are declared, each with its display label.
#: Every one is printed in the report even at zero — a reason showing `0` is
#: information that the absence of a line could not convey.
SKIP_REASONS: dict[str, str] = {
    "out-of-scope": "out-of-scope",
    "depth-limit": "depth-limit",
    "already-visited": "already-visited",
    "image-too-small": "image-too-small",
    "non-2xx": "non-2xx",
    "unsupported-type": "unsupported-type",
    "asset-host-not-allowed": "asset-host-not-allowed",
    "unparseable": "unparseable",
}

#: Listing these would invite the wrong conclusion: every URL in the group was
#: crawled via another link, so it is a dedup tally, not a list of misses.
COUNT_ONLY_REASONS = frozenset({"already-visited"})


@dataclass
class CrawlReport:
    """What the run collected and what it passed over (§4.7.1)."""

    included: list[tuple[str, str]] = field(default_factory=list)   # (kind, url)
    skipped: dict[str, list[str]] = field(default_factory=dict)     # reason -> urls
    skipped_counts: dict[str, int] = field(default_factory=dict)

    def include(self, kind: str, url: str) -> None:
        self.included.append((kind, url))

    def skip(self, reason: str, url: str, detail: str = "") -> None:
        self.skipped_counts[reason] = self.skipped_counts.get(reason, 0) + 1
        if reason in COUNT_ONLY_REASONS:
            return
        self.skipped.setdefault(reason, []).append(f"{url}  → {detail}" if detail else url)


class Console:
    """Progress on stderr, report on stdout.

    Progress is transient status; redirecting stdout should capture the report
    without it. The report is the run's result and belongs in whatever the
    operator redirects to a file.
    """

    def __init__(self, quiet: bool = False, report_limit: int = 20) -> None:
        self.quiet = quiet
        self.report_limit = report_limit
        self._n = 0

    # ---- live progress ---------------------------------------------------

    def fetching(self, url: str, depth: int, kind: str) -> None:
        """Print as the fetch *starts*.

        A crawl that hangs then leaves the responsible URL as the last thing on
        screen, which is the entire reason to watch one live. Printing on
        completion would show everything except the one that matters.
        """
        self._n += 1
        if self.quiet:
            return
        print(f"[{self._n:>4}] d{depth} {kind:<5} {url}", file=sys.stderr, flush=True)

    def failed(self, url: str, detail: str) -> None:
        """Failures print immediately rather than waiting for the report — a run
        that is failing every fetch should be obvious in the first seconds."""
        if self.quiet:
            return
        print(f"       !  {detail:<5} {url}", file=sys.stderr, flush=True)

    # ---- end-of-run report ----------------------------------------------

    def report(self, report: CrawlReport) -> None:
        by_kind: dict[str, list[str]] = {}
        for kind, url in report.included:
            by_kind.setdefault(kind, []).append(url)
        counts = ", ".join(f"{len(v)} {k}" for k, v in sorted(by_kind.items()))
        print(f"\nIncluded ({counts or 'nothing'})\n")

        for kind in sorted(by_kind):
            urls = sorted(set(by_kind[kind]))
            host = _common_host(urls)
            if host:
                print(f"  [{kind}] {host}")
            for url in urls:
                # The full URLs are mostly a repeated prefix; showing the host
                # once per group leaves the part that differs.
                print(f"    {_strip_host(url) if host else url}")
            print()

        total_skipped = sum(report.skipped_counts.values())
        print(f"Skipped ({total_skipped:,})\n")
        # Descending count, so the reasons that shaped the crawl come first;
        # ties broken by name so two runs of the same crawl diff cleanly.
        for reason in sorted(
            SKIP_REASONS, key=lambda r: (-report.skipped_counts.get(r, 0), r)
        ):
            count = report.skipped_counts.get(reason, 0)
            urls = sorted(set(report.skipped.get(reason, [])))
            note = ""
            if reason in COUNT_ONLY_REASONS:
                note = "   (count only — every one was crawled via another link)"
            elif self.report_limit == 0 and count:
                note = "   (counts only)"
            elif 0 <= self.report_limit < len(urls):
                note = f"   (showing {self.report_limit})"
            print(f"  {reason:<24}{count:>7,}{note}")

            if reason in COUNT_ONLY_REASONS or self.report_limit == 0:
                continue
            shown = urls if self.report_limit < 0 else urls[: self.report_limit]
            for url in shown:
                print(f"      {url}")
            if len(urls) > len(shown):
                print(f"      …and {len(urls) - len(shown):,} more — see the log")
        print()


def _common_host(urls: list[str]) -> str:
    hosts = {urlparse(u).hostname for u in urls}
    if len(hosts) == 1:
        host = hosts.pop()
        return host or ""
    return ""


def _strip_host(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "") or "/"
