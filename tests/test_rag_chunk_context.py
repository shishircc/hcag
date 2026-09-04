"""A chunk must carry the name of the thing it is about (§8).

Two defects, both found while chasing "the RAG agent can't answer what ONE
Pass is":

1. A bare `#` in the crawled Markdown — MOM's pages have one — was pushed onto
   the heading stack as an empty level-1 title, superseding the document's real
   name. From that chunk on, `headings` read `['', 'Who is eligible']`.
2. Only a document's first chunk contains its title, and `text` is both the
   embedded string and the FTS-indexed column. Every later chunk was therefore
   unreachable by a query naming the document it belongs to.
"""

from __future__ import annotations

from pathlib import Path

from hcag.rag.chunker import chunks_for, make_token_estimator
from hcag.rag.config import RagConfig
from hcag.rag.runner import _process_text_file, with_heading_path
from hcag.rag.walker import Candidate

EST = make_token_estimator()

# The shape of a crawled MOM page: real title, then the empty heading the
# page's markup produces, then the sections.
PAGE = """# Eligibility for Overseas Networks & Expertise Pass

Intro paragraph about the pass.

#

## Who is eligible

You qualify if your salary meets the bar.

## How to apply

Submit the form.
"""


def _chunks(text: str, target: int = 40):
    return chunks_for(
        text, source_kind="markdown", target_tokens=target, overlap_tokens=0, est=EST
    )


# --- Defect 1: the empty heading ------------------------------------------


def test_an_empty_heading_does_not_erase_the_document_title() -> None:
    chunks = _chunks(PAGE)

    assert len(chunks) > 1, "need a multi-chunk document to test this"
    for ch in chunks:
        assert "" not in ch.headings, ch.headings
    # Every chunk still knows which document it came from.
    titled = [c for c in chunks if c.headings]
    assert titled
    for ch in titled:
        assert ch.headings[0] == "Eligibility for Overseas Networks & Expertise Pass"


def test_a_real_heading_still_supersedes_its_level() -> None:
    """Skipping empty headings must not also skip the pop of deeper levels."""
    from hcag.rag.chunker import _current_headings, _push_heading

    stack: dict[int, str] = {}
    _push_heading(stack, 1, "One")
    _push_heading(stack, 2, "Sub")
    assert _current_headings(stack) == ["One", "Sub"]

    _push_heading(stack, 1, "Two")
    assert _current_headings(stack) == ["Two"]  # "Sub" is gone with its parent


def test_an_empty_heading_leaves_the_stack_exactly_as_it_was() -> None:
    from hcag.rag.chunker import _current_headings, _push_heading

    stack: dict[int, str] = {}
    _push_heading(stack, 1, "One")
    _push_heading(stack, 2, "Sub")
    _push_heading(stack, 1, "   ")  # the bare `#` in the crawled page

    assert _current_headings(stack) == ["One", "Sub"]


# --- Defect 2: the heading path in the indexed text ------------------------


def test_the_heading_path_is_prefixed_to_the_text() -> None:
    out = with_heading_path("You qualify if…", ["ONE Pass", "Who is eligible"])
    assert out == "ONE Pass > Who is eligible\n\nYou qualify if…"


def test_a_title_the_text_already_opens_with_is_not_repeated() -> None:
    out = with_heading_path("# Who is eligible\n\nbody", ["ONE Pass", "Who is eligible"])
    assert out == "ONE Pass\n\n# Who is eligible\n\nbody"


def test_no_headings_leaves_the_text_alone() -> None:
    assert with_heading_path("body", []) == "body"
    assert with_heading_path("body", ["", "  "]) == "body"


def test_a_single_heading_the_text_opens_with_is_a_no_op() -> None:
    """Chunk 0 of a document already leads with its own title."""
    text = "# Overseas Networks & Expertise Pass\n\nThe pass is for top talent."
    assert with_heading_path(text, ["Overseas Networks & Expertise Pass"]) == text


# --- End to end through the indexer's row builder --------------------------


def test_every_chunk_of_a_page_names_the_page(tmp_path: Path) -> None:
    src = tmp_path / "eligibility.md"
    src.write_text(PAGE, encoding="utf-8")
    candidate = Candidate(
        kb_path="one-pass/eligibility.md",
        abs_path=src,
        source_kind="markdown",
        bytes=src.stat().st_size,
        mtime=src.stat().st_mtime,
    )
    cfg = RagConfig()
    cfg.chunking.target_tokens = 40
    cfg.chunking.overlap_tokens = 0

    rows, _hash = _process_text_file(candidate, cfg, EST)

    assert len(rows) > 1
    # The point of the whole change: the document's name is searchable from
    # every one of its chunks, not just the first.
    for r in rows:
        assert "Overseas Networks & Expertise Pass" in r.text, (r.chunk_index, r.text[:80])
    # The budget must count what was actually stored.
    for r in rows:
        assert r.token_estimate == EST(r.text)
