"""Markdown-aware chunker (§8.4.2).

Splits the source into a stream of *segments* — headings, paragraphs, and
fenced code blocks are the atomic units — then greedily packs segments into
chunks up to ``target_tokens``, with ``overlap_tokens`` of the previous
chunk's tail prepended to each new chunk. Code fences are never split.

For non-Markdown text, the segmenter degrades to blank-line-separated
paragraphs, which behaves like a sliding window for prose-only content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


@dataclass
class Chunk:
    text: str
    char_start: int              # in the ORIGINAL source
    char_end: int                # exclusive
    headings: list[str] = field(default_factory=list)
    token_estimate: int = 0


@dataclass
class _Segment:
    text: str                    # raw source slice (with trailing newlines preserved)
    char_start: int
    char_end: int
    kind: str                    # "heading" | "paragraph" | "code" | "blank"
    heading_level: int = 0       # 1..6 for headings, 0 otherwise
    heading_title: str = ""


# --- Token estimator ------------------------------------------------------


def _rough_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def make_token_estimator(encoding: str = "cl100k_base") -> Callable[[str], int]:
    """Return a callable that estimates tokens for a string.

    Falls back to a chars/4 heuristic when tiktoken is unavailable or the
    encoding isn't recognized. The estimator is called once per candidate
    chunk during packing and once per emitted chunk for the row's
    ``token_estimate`` column, so it's on a moderately hot path.
    """
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding(encoding)

        def _est(s: str) -> int:
            if not s:
                return 0
            return len(enc.encode(s))

        return _est
    except Exception:
        return _rough_tokens


# --- Segmentation ---------------------------------------------------------


def _segments(source: str, *, markdown: bool) -> list[_Segment]:
    """Break ``source`` into ordered segments with byte offsets preserved."""
    out: list[_Segment] = []
    if not source:
        return out

    # Normalize CRLF -> LF for offset math; downstream consumers get LF too.
    text = source.replace("\r\n", "\n")

    lines = text.split("\n")
    # Precompute cumulative byte offsets — offsets[i] is the start of line i.
    offsets: list[int] = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)  # +1 for the trailing '\n'

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            # Blank line — recorded as its own segment so paragraph boundaries
            # survive packing decisions later.
            out.append(
                _Segment(
                    text="\n",
                    char_start=offsets[i],
                    char_end=offsets[i + 1],
                    kind="blank",
                )
            )
            i += 1
            continue

        if markdown:
            m = HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                title = m.group(2)
                out.append(
                    _Segment(
                        text=line + "\n",
                        char_start=offsets[i],
                        char_end=offsets[i + 1],
                        kind="heading",
                        heading_level=level,
                        heading_title=title,
                    )
                )
                i += 1
                continue

            if FENCE_RE.match(line):
                # Fenced code block — consume until the matching fence.
                fence = FENCE_RE.match(line).group(1)  # type: ignore[union-attr]
                start_i = i
                j = i + 1
                while j < n and not (FENCE_RE.match(lines[j]) and lines[j].startswith(fence)):
                    j += 1
                # Include the closing fence (or run to EOF if not found).
                end_i = j + 1 if j < n else n
                seg_text = "\n".join(lines[start_i:end_i])
                if end_i < n:
                    seg_text += "\n"
                out.append(
                    _Segment(
                        text=seg_text,
                        char_start=offsets[start_i],
                        char_end=offsets[end_i] if end_i < len(offsets) else offsets[-1],
                        kind="code",
                    )
                )
                i = end_i
                continue

        # Prose paragraph — consume until a blank line, heading, or fence.
        start_i = i
        j = i + 1
        while j < n:
            l2 = lines[j]
            if not l2.strip():
                break
            if markdown and (HEADING_RE.match(l2) or FENCE_RE.match(l2)):
                break
            j += 1
        seg_text = "\n".join(lines[start_i:j])
        if j < n:
            seg_text += "\n"
        out.append(
            _Segment(
                text=seg_text,
                char_start=offsets[start_i],
                char_end=offsets[j] if j < len(offsets) else offsets[-1],
                kind="paragraph",
            )
        )
        i = j

    return out


# --- Heading tracking + packing ------------------------------------------


def _current_headings(stack: dict[int, str]) -> list[str]:
    return [stack[k] for k in sorted(stack.keys()) if stack[k].strip()]


def _push_heading(stack: dict[int, str], level: int, title: str) -> None:
    # An empty heading (`#` with no text) is a rendering artifact of the
    # crawled page, not a section boundary. Pushing it would supersede the
    # document's real title with "" for every chunk that follows — which is
    # how a page called "Eligibility for the Overseas Networks & Expertise
    # Pass" ended up with `headings = ['', 'Who is eligible']` from its second
    # chunk on, unfindable by the name of the thing it is about.
    if not title.strip():
        return
    # A heading at level N supersedes N and everything deeper.
    for k in [k for k in stack if k >= level]:
        stack.pop(k, None)
    stack[level] = title


def _slice_tail(text: str, target_tokens: int, est: Callable[[str], int]) -> str:
    """Return the trailing ~target_tokens of ``text``, cut on a word boundary."""
    if target_tokens <= 0 or not text:
        return ""
    # Char/token ratio is ~4 for English; overshoot a bit and then trim.
    approx_chars = target_tokens * 6
    tail = text[-approx_chars:] if len(text) > approx_chars else text
    # Snap to the nearest whitespace so we don't cut mid-word.
    space = tail.find(" ")
    if space != -1 and space < len(tail) - 1:
        tail = tail[space + 1 :]
    # Trim down to actual token budget.
    while est(tail) > target_tokens and " " in tail:
        tail = tail.split(" ", 1)[1]
    return tail


def chunk_text(
    source: str,
    *,
    markdown: bool,
    target_tokens: int,
    overlap_tokens: int,
    est: Callable[[str], int] | None = None,
) -> list[Chunk]:
    """Chunk ``source`` into a list of ``Chunk`` records."""
    if est is None:
        est = make_token_estimator()

    segs = _segments(source, markdown=markdown)
    if not segs:
        return []

    chunks: list[Chunk] = []
    buf: list[_Segment] = []
    buf_tokens = 0
    heading_stack: dict[int, str] = {}
    chunk_headings_snapshot: list[str] = []

    def _emit(prev_tail: str) -> None:
        """Materialize the current buffer into a Chunk and clear the buffer."""
        nonlocal buf_tokens
        if not buf:
            return
        # Strip leading + trailing blank segments so char_start/end land on real content.
        while buf and buf[0].kind == "blank":
            buf.pop(0)
        while buf and buf[-1].kind == "blank":
            buf.pop()
        if not buf:
            buf_tokens = 0
            return
        primary = "".join(s.text for s in buf).rstrip("\n") + "\n"
        text = (prev_tail + ("\n\n" if prev_tail else "") + primary) if prev_tail else primary
        chunk = Chunk(
            text=text.strip("\n"),
            char_start=buf[0].char_start,
            char_end=buf[-1].char_end,
            headings=list(chunk_headings_snapshot),
        )
        chunk.token_estimate = est(chunk.text)
        chunks.append(chunk)
        buf.clear()
        buf_tokens = 0

    prev_tail_text = ""

    def _capture_snapshot() -> None:
        """Freeze the heading path when a chunk starts accumulating."""
        nonlocal chunk_headings_snapshot
        if not buf:
            chunk_headings_snapshot = _current_headings(heading_stack)

    for seg in segs:
        if seg.kind == "heading":
            _push_heading(heading_stack, seg.heading_level, seg.heading_title)

        seg_tokens = est(seg.text)

        # Oversized single segment (e.g., a huge code block) — flush any buffered
        # content, emit the segment as its own chunk, and continue.
        if seg_tokens >= target_tokens and (seg.kind == "code" or seg.kind == "paragraph"):
            if buf:
                _emit(prev_tail_text)
                prev_tail_text = _slice_tail(chunks[-1].text if chunks else "", overlap_tokens, est)
            standalone_headings = _current_headings(heading_stack)
            standalone = Chunk(
                text=(prev_tail_text + ("\n\n" if prev_tail_text else "") + seg.text.rstrip("\n")).strip("\n"),
                char_start=seg.char_start,
                char_end=seg.char_end,
                headings=list(standalone_headings),
            )
            standalone.token_estimate = est(standalone.text)
            chunks.append(standalone)
            prev_tail_text = _slice_tail(standalone.text, overlap_tokens, est)
            continue

        # If adding this segment would blow the budget and we already have content,
        # flush first, then capture a fresh snapshot for the next chunk.
        if buf and (buf_tokens + seg_tokens) > target_tokens:
            _emit(prev_tail_text)
            prev_tail_text = _slice_tail(chunks[-1].text if chunks else "", overlap_tokens, est)

        _capture_snapshot()
        buf.append(seg)
        buf_tokens += seg_tokens

    # Final flush uses the snapshot captured when the last chunk began.
    _emit(prev_tail_text)

    return chunks


def chunks_for(
    text: str,
    *,
    source_kind: str,
    target_tokens: int,
    overlap_tokens: int,
    est: Callable[[str], int] | None = None,
) -> list[Chunk]:
    """Convenience: dispatch on source_kind. Non-Markdown falls back to
    paragraph-based windowing (still uses the segmenter with markdown=False)."""
    return chunk_text(
        text,
        markdown=(source_kind in ("markdown", "html", "pdf")),
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
        est=est,
    )


__all__ = ["Chunk", "chunk_text", "chunks_for", "make_token_estimator"]
