"""A page-wrapping <form> must not take the article down with it (§4.4.1)."""

from __future__ import annotations

from bs4 import BeautifulSoup

from hcag.crawl.html_conv import convert_html, unwrap_form_wrappers


def _webforms_page(body: str) -> bytes:
    """ASP.NET WebForms shape: the entire body inside one <form runat=server>."""
    return (
        "<html><head><title>T</title></head><body>"
        '<form id="mainform" method="post" action="./eligibility">'
        f"{body}"
        "</form></body></html>"
    ).encode()


ARTICLE = """
<h1>Eligibility for Employment Pass</h1>
<h2>Who is eligible</h2>
<p>Candidates must pass a two-stage framework before an EP is granted, and the
first stage is the qualifying salary benchmarked to local PMET salaries by age.</p>
<table>
  <tr><th>Sector</th><th>Minimum qualifying salary</th></tr>
  <tr><td>All except financial services</td><td>$5,600</td></tr>
  <tr><td>Financial services</td><td>$6,200</td></tr>
</table>
<h2>COMPASS</h2>
<p>Applications are scored under COMPASS on four foundational criteria and two
bonus criteria, and need at least forty points in total to pass the framework.</p>
"""


# --- The unwrap itself -----------------------------------------------------


def test_page_wrapping_form_is_unwrapped() -> None:
    soup = BeautifulSoup(_webforms_page(ARTICLE), "html.parser")
    assert soup.find("form") is not None

    assert unwrap_form_wrappers(soup) == 1

    # The tag is gone; every child survives, in place.
    assert soup.find("form") is None
    assert soup.find("h1").get_text() == "Eligibility for Employment Pass"
    assert "$5,600" in soup.get_text()
    assert soup.find("table") is not None


def test_a_real_form_is_left_alone() -> None:
    """A search box is chrome and must stay chrome — detection is by text share,
    not by the tag, so genuine forms keep being discarded downstream."""
    html = (
        "<html><body>"
        f"<article>{ARTICLE}</article>"
        '<form id="search"><label>Search</label><input name="q"></form>'
        "</body></html>"
    ).encode()
    soup = BeautifulSoup(html, "html.parser")

    assert unwrap_form_wrappers(soup) == 0
    assert soup.find("form", id="search") is not None


def test_no_forms_is_a_no_op() -> None:
    soup = BeautifulSoup(b"<html><body><p>hello</p></body></html>", "html.parser")
    assert unwrap_form_wrappers(soup) == 0


def test_empty_document_does_not_divide_by_zero() -> None:
    soup = BeautifulSoup(b"<html><body></body></html>", "html.parser")
    assert unwrap_form_wrappers(soup) == 0


def test_nested_forms_do_not_break_the_walk() -> None:
    """unwrap() mutates the tree being iterated, so the list is materialized."""
    html = (
        "<html><body><form id='outer'>"
        f"<form id='inner'>{ARTICLE}</form>"
        "</form></body></html>"
    ).encode()
    soup = BeautifulSoup(html, "html.parser")
    unwrap_form_wrappers(soup)
    assert "$5,600" in soup.get_text()


# --- End to end through the converter --------------------------------------


def test_webforms_article_survives_extraction() -> None:
    """The regression: reading-mode extractors discard form subtrees as chrome,
    and ASP.NET wraps the whole body in one — so the article was being dropped
    while extraction still reported success."""
    converted = convert_html(
        _webforms_page(ARTICLE), "https://example.gov/eligibility", "eligibility"
    )

    assert converted.forms_unwrapped == 1
    # The substance survives: prose, the qualifying-salary figures, and the
    # table as a table rather than as flattened prose.
    assert "two-stage framework" in converted.markdown
    assert "$5,600" in converted.markdown
    assert "$6,200" in converted.markdown
    assert "COMPASS" in converted.markdown
    assert converted.feature_counts["tables"] >= 1

    # Before the fix this page extracted to nothing at all.
    without_fix = _extract_without_unwrapping(_webforms_page(ARTICLE))
    assert "$5,600" not in (without_fix or "")


def _extract_without_unwrapping(html: bytes) -> str | None:
    """What stage 2 saw before stage 1 learned to unwrap page-wrapping forms."""
    from hcag.crawl.html_conv import _extract_main_content

    return _extract_main_content(html.decode(), "balanced")


def test_same_article_without_the_form_wrapper_is_equivalent() -> None:
    """Unwrapping restores the no-form result rather than producing a new one."""
    wrapped = convert_html(
        _webforms_page(ARTICLE), "https://example.gov/x", "x"
    ).markdown
    plain = convert_html(
        f"<html><body>{ARTICLE}</body></html>".encode(), "https://example.gov/x", "x"
    ).markdown
    assert "$5,600" in plain
    assert wrapped.strip() == plain.strip()


def test_forms_unwrapped_is_zero_on_an_ordinary_page() -> None:
    converted = convert_html(
        f"<html><body>{ARTICLE}</body></html>".encode(), "https://example.gov/x", "x"
    )
    assert converted.forms_unwrapped == 0
