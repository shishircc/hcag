"""Unit tests for the cross-page boilerplate module (§4.4.4).

Pure logic, no I/O. Integration through the crawl core is exercised
separately in test_crawl_core.
"""

from __future__ import annotations

from hcag.crawl.boilerplate import (
    BoilerplateSets,
    FingerprintIndex,
    blocks_to_markdown,
    fingerprint,
    identify_boilerplate,
    split_blocks,
    strip_page,
)


# --- split_blocks ---------------------------------------------------------


def test_split_blocks_separates_on_blank_lines() -> None:
    text = "first para\nline two\n\nsecond para\n\nthird"
    assert split_blocks(text) == ["first para\nline two", "second para", "third"]


def test_split_blocks_keeps_fenced_code_atomic() -> None:
    text = (
        "intro line\n"
        "\n"
        "```python\n"
        "def f():\n"
        "\n"  # blank line INSIDE the fence — must not split
        "    return 1\n"
        "```\n"
        "\n"
        "outro"
    )
    blocks = split_blocks(text)
    assert len(blocks) == 3
    assert blocks[0] == "intro line"
    assert blocks[1].startswith("```python") and blocks[1].endswith("```")
    assert "def f():" in blocks[1] and "return 1" in blocks[1]
    assert blocks[2] == "outro"


def test_split_blocks_empty_and_whitespace() -> None:
    assert split_blocks("") == []
    assert split_blocks("\n\n\n") == []
    assert split_blocks("   \n\n") == []


def test_split_blocks_normalizes_crlf() -> None:
    assert split_blocks("a\r\n\r\nb") == ["a", "b"]


# --- fingerprint ----------------------------------------------------------


def test_fingerprint_normalizes_case_and_whitespace() -> None:
    assert fingerprint("  Home  ") == fingerprint("home")
    assert fingerprint("Foo    Bar") == fingerprint("foo bar")
    assert fingerprint("Line one\nLine two") == fingerprint("line one line two")


def test_fingerprint_drops_trailing_punctuation() -> None:
    assert fingerprint("Home.") == "home"
    assert fingerprint("© 2026 Widgets Inc.") == fingerprint("© 2026 widgets inc")


def test_fingerprint_empty_string_is_empty() -> None:
    assert fingerprint("") == ""
    assert fingerprint("   ") == ""


# --- identify_boilerplate -------------------------------------------------


def _index_with_pages(pages: dict[str, list[str]]) -> FingerprintIndex:
    idx = FingerprintIndex()
    for page_id, blocks in pages.items():
        idx.add_page(page_id, blocks)
    return idx


def test_identify_flags_header_repeating_across_all_pages() -> None:
    pages = {
        "p1": ["Nav | Home | About", "unique content of p1", "Footer stuff"],
        "p2": ["Nav | Home | About", "unique content of p2", "Footer stuff"],
        "p3": ["Nav | Home | About", "unique content of p3", "Footer stuff"],
    }
    sets = identify_boilerplate(_index_with_pages(pages), threshold=0.7, window=5)
    assert fingerprint("Nav | Home | About") in sets.headers
    assert fingerprint("Footer stuff") in sets.footers
    assert fingerprint("unique content of p1") not in sets.headers
    assert fingerprint("unique content of p1") not in sets.footers


def test_identify_respects_threshold() -> None:
    pages = {
        "p1": ["shared", "body1"],
        "p2": ["shared", "body2"],
        "p3": ["different", "body3"],
        "p4": ["different2", "body4"],
    }
    # "shared" appears in 2/4 = 0.5 of pages, below 0.7 threshold.
    sets = identify_boilerplate(_index_with_pages(pages), threshold=0.7, window=5)
    assert fingerprint("shared") not in sets.headers
    # Lower the threshold and it qualifies.
    sets2 = identify_boilerplate(_index_with_pages(pages), threshold=0.5, window=5)
    assert fingerprint("shared") in sets2.headers


def test_identify_position_window_excludes_middle_matches() -> None:
    # "sig" appears in all pages but never at the top or bottom.
    pages = {
        f"p{i}": ["first", "second", "third", "sig", "fifth", "sixth"]
        for i in range(5)
    }
    sets = identify_boilerplate(_index_with_pages(pages), threshold=0.7, window=2)
    assert fingerprint("sig") not in sets.headers
    assert fingerprint("sig") not in sets.footers


def test_identify_handles_empty_index() -> None:
    sets = identify_boilerplate(FingerprintIndex(), threshold=0.7, window=5)
    assert sets.headers == set()
    assert sets.footers == set()


# --- strip_page -----------------------------------------------------------


def test_strip_removes_leading_and_trailing_boilerplate() -> None:
    blocks = ["nav", "content1", "content2", "footer"]
    sets = BoilerplateSets(headers={fingerprint("nav")}, footers={fingerprint("footer")})
    result = strip_page(blocks, sets)
    assert result.blocks == ["content1", "content2"]
    assert result.header_removed == 1
    assert result.footer_removed == 1
    assert result.guard_tripped is False


def test_strip_stops_at_first_non_boilerplate() -> None:
    # nav-content-nav-content — the second nav is NOT stripped because it's
    # in the middle, not at the top.
    blocks = ["nav", "content1", "nav", "content2"]
    sets = BoilerplateSets(headers={fingerprint("nav")}, footers=set())
    result = strip_page(blocks, sets)
    assert result.blocks == ["content1", "nav", "content2"]
    assert result.header_removed == 1


def test_strip_guard_trips_when_too_much_would_be_removed() -> None:
    # 3-block page: removing 2 would be 66%, over the 50% cap.
    blocks = ["nav", "content", "footer"]
    sets = BoilerplateSets(headers={fingerprint("nav")}, footers={fingerprint("footer")})
    result = strip_page(blocks, sets, max_strip_ratio=0.5)
    assert result.guard_tripped is True
    assert result.blocks == blocks  # verbatim
    assert result.header_removed == 0
    assert result.footer_removed == 0


def test_strip_at_exactly_cap_is_ok() -> None:
    # 4-block page, remove 2 → exactly 50%, allowed.
    blocks = ["nav", "content1", "content2", "footer"]
    sets = BoilerplateSets(headers={fingerprint("nav")}, footers={fingerprint("footer")})
    result = strip_page(blocks, sets, max_strip_ratio=0.5)
    assert result.guard_tripped is False
    assert result.blocks == ["content1", "content2"]


def test_strip_no_boilerplate_returns_verbatim() -> None:
    blocks = ["a", "b", "c"]
    result = strip_page(blocks, BoilerplateSets())
    assert result.blocks == blocks
    assert result.header_removed == 0
    assert result.footer_removed == 0


def test_strip_empty_page_is_a_noop() -> None:
    sets = BoilerplateSets(headers={fingerprint("nav")}, footers=set())
    result = strip_page([], sets)
    assert result.blocks == []
    assert result.header_removed == 0


# --- blocks_to_markdown ---------------------------------------------------


def test_blocks_to_markdown_joins_with_blank_lines() -> None:
    text = blocks_to_markdown(["one", "two", "three"])
    assert text == "one\n\ntwo\n\nthree\n"


def test_blocks_to_markdown_empty() -> None:
    assert blocks_to_markdown([]) == ""
