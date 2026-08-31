"""Cross-page positional voting for boilerplate detection (DESIGN §4.4.4).

Pure logic — no I/O, no HTTP, no logger. The crawl core calls into this
module during BFS to record every HTML page's block fingerprints, then again
after BFS to identify header + footer fingerprints and strip each buffered
page's leading and trailing runs.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field


DEFAULT_THRESHOLD = 0.7
DEFAULT_WINDOW = 5
DEFAULT_MIN_CORPUS = 3
DEFAULT_MAX_STRIP_RATIO = 0.5


# --- Block splitting -------------------------------------------------------

# Fenced code blocks (```lang ... ``` or ~~~ ... ~~~) are atomic — we never
# split across their delimiters, even if there's blank space inside.
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
_BLANK_LINE_RE = re.compile(r"^\s*$")


def split_blocks(text: str) -> list[str]:
    """Split Markdown into blocks — non-blank line runs separated by blank
    lines, with fenced code blocks treated as atomic units.

    Trailing/leading blank lines around each block are stripped.
    """
    if not text:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    blocks: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        # Trim trailing blanks from buf before joining.
        while buf and _BLANK_LINE_RE.match(buf[-1] or ""):
            buf.pop()
        if buf:
            blocks.append("\n".join(buf).strip("\n"))
        buf.clear()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            # Consume until closing fence with the same marker (or EOF).
            marker = fence_match.group(1)
            fence_lines = [line]
            j = i + 1
            while j < n:
                fence_lines.append(lines[j])
                if lines[j].startswith(marker) and _FENCE_RE.match(lines[j]):
                    j += 1
                    break
                j += 1
            # If there was pending prose in buf, flush it before the fence.
            flush()
            blocks.append("\n".join(fence_lines).strip("\n"))
            i = j
            continue

        if _BLANK_LINE_RE.match(line):
            flush()
            i += 1
            continue

        buf.append(line)
        i += 1

    flush()
    return blocks


# --- Fingerprint normalization --------------------------------------------

_WS_COLLAPSE_RE = re.compile(r"\s+")
_TRAILING_PUNCT_RE = re.compile(r"[\s\.,;:!\?\|\-–—·•]+$")


def fingerprint(block: str) -> str:
    """Normalized string used as a cross-page equality key.

    Rules (§4.4.4): lowercase, collapse consecutive whitespace to a single
    space, drop trailing punctuation. Returns ``""`` for a block that
    normalizes to empty — those never register in the index.
    """
    if not block:
        return ""
    s = block.strip().lower()
    s = _WS_COLLAPSE_RE.sub(" ", s)
    s = _TRAILING_PUNCT_RE.sub("", s)
    return s


# --- Fingerprint index ----------------------------------------------------


@dataclass
class _Occurrence:
    page_id: str
    pos_from_top: int
    pos_from_bottom: int


@dataclass
class FingerprintIndex:
    """In-memory tally of block fingerprints across every buffered HTML page.

    ``add_page`` is called once per page during Phase 1 of the crawl; the
    stored records are then consumed by ``identify_boilerplate`` in Phase 2.
    """

    records: dict[str, list[_Occurrence]] = field(default_factory=lambda: defaultdict(list))
    _pages_seen: set[str] = field(default_factory=set)

    def add_page(self, page_id: str, blocks: list[str]) -> None:
        self._pages_seen.add(page_id)
        n = len(blocks)
        for i, block in enumerate(blocks):
            fp = fingerprint(block)
            if not fp:
                continue
            self.records[fp].append(
                _Occurrence(page_id=page_id, pos_from_top=i, pos_from_bottom=n - 1 - i)
            )

    def page_count(self) -> int:
        return len(self._pages_seen)


# --- Identification -------------------------------------------------------


@dataclass
class BoilerplateSets:
    headers: set[str] = field(default_factory=set)
    footers: set[str] = field(default_factory=set)

    def any(self) -> bool:
        return bool(self.headers or self.footers)


def identify_boilerplate(
    index: FingerprintIndex,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    window: int = DEFAULT_WINDOW,
) -> BoilerplateSets:
    """Classify each recorded fingerprint as a header, a footer, or content.

    A fingerprint is a **header** if it appears in ≥ ``threshold`` fraction
    of pages AND its position from the top is within the first ``window``
    blocks in the majority of its appearances. **Footer** is the symmetric
    rule on position from the bottom. A fingerprint can qualify as both
    (rare — usually a page-name breadcrumb repeated top and bottom).
    """
    result = BoilerplateSets()
    total_pages = index.page_count()
    if total_pages == 0:
        return result

    min_distinct_pages = max(1, int(round(threshold * total_pages)))
    for fp, occurrences in index.records.items():
        distinct_pages = len({occ.page_id for occ in occurrences})
        if distinct_pages < min_distinct_pages:
            continue
        n_top = sum(1 for occ in occurrences if occ.pos_from_top < window)
        n_bot = sum(1 for occ in occurrences if occ.pos_from_bottom < window)
        # Majority-of-appearances rule so a fingerprint that occasionally
        # shows up in the middle doesn't disqualify itself as boilerplate.
        if n_top * 2 > len(occurrences):
            result.headers.add(fp)
        if n_bot * 2 > len(occurrences):
            result.footers.add(fp)
    return result


# --- Stripping ------------------------------------------------------------


@dataclass
class StripResult:
    blocks: list[str]
    header_removed: int
    footer_removed: int
    guard_tripped: bool


def strip_page(
    blocks: list[str],
    sets: BoilerplateSets,
    *,
    max_strip_ratio: float = DEFAULT_MAX_STRIP_RATIO,
) -> StripResult:
    """Strip leading header + trailing footer runs from ``blocks``.

    Each strip stops at the first block whose fingerprint isn't in the
    matching set — nav sandwiched between real paragraphs stays.

    Sanity guard: if the combined removal would exceed ``max_strip_ratio``
    of the page (default 50%), the page is returned verbatim and
    ``guard_tripped`` is True.
    """
    n = len(blocks)
    if n == 0 or not sets.any():
        return StripResult(blocks=list(blocks), header_removed=0, footer_removed=0, guard_tripped=False)

    # Leading headers.
    i = 0
    while i < n and fingerprint(blocks[i]) in sets.headers:
        i += 1
    # Trailing footers — start from the end but never cross into what was
    # already stripped as a header.
    j = n
    while j > i and fingerprint(blocks[j - 1]) in sets.footers:
        j -= 1

    removed = n - (j - i)
    if removed > max_strip_ratio * n:
        return StripResult(blocks=list(blocks), header_removed=0, footer_removed=0, guard_tripped=True)

    return StripResult(
        blocks=blocks[i:j],
        header_removed=i,
        footer_removed=n - j,
        guard_tripped=False,
    )


def blocks_to_markdown(blocks: list[str]) -> str:
    """Rejoin stripped blocks into a Markdown document (blank-line separated)."""
    if not blocks:
        return ""
    return "\n\n".join(b.strip("\n") for b in blocks) + "\n"


__all__ = [
    "DEFAULT_MAX_STRIP_RATIO",
    "DEFAULT_MIN_CORPUS",
    "DEFAULT_THRESHOLD",
    "DEFAULT_WINDOW",
    "BoilerplateSets",
    "FingerprintIndex",
    "StripResult",
    "blocks_to_markdown",
    "fingerprint",
    "identify_boilerplate",
    "split_blocks",
    "strip_page",
]
