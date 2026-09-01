"""HTML → Markdown conversion for `crawl` (§4.4.1, §4.4.3)."""

from __future__ import annotations

from hcag.crawl.html_conv import convert_html


def test_extracts_absolute_links_from_relative_hrefs() -> None:
    html = b"""
    <html><body>
      <a href="/other.html">Other</a>
      <a href="https://elsewhere.com/x">Elsewhere</a>
      <a href="#top">Anchor</a>
      <a href="mailto:foo@bar">Mail</a>
    </body></html>
    """
    doc = convert_html(html, base_url="https://ex.com/dir/page.html", doc_basename="page")
    assert "https://ex.com/other.html" in doc.links
    assert "https://elsewhere.com/x" in doc.links
    # anchor and mailto are ignored
    assert not any(l.startswith("mailto:") for l in doc.links)
    assert not any(l.endswith("#top") for l in doc.links)


def test_extracts_images_and_rewrites_src_to_local_prefixed_name() -> None:
    html = b"""
    <html><body>
      <img src="/img/apple.jpg" />
      <img src="https://cdn.example.com/photos/banana.png" />
    </body></html>
    """
    doc = convert_html(html, base_url="https://ex.com/dir/page.html", doc_basename="page")
    remote_urls = {r for r, _ in doc.images}
    local_names = {n for _, n in doc.images}
    assert "https://ex.com/img/apple.jpg" in remote_urls
    assert "https://cdn.example.com/photos/banana.png" in remote_urls
    assert "page-apple.jpg" in local_names
    assert "page-banana.png" in local_names
    # Markdown output points at the local filenames, not the remote URLs
    assert "page-apple.jpg" in doc.markdown
    assert "page-banana.png" in doc.markdown
    assert "https://cdn.example.com" not in doc.markdown


def test_image_name_collision_within_doc_is_disambiguated() -> None:
    html = b"""
    <html><body>
      <img src="/a/apple.jpg" />
      <img src="/b/apple.jpg" />
    </body></html>
    """
    doc = convert_html(html, base_url="https://ex.com/page.html", doc_basename="page")
    local_names = [n for _, n in doc.images]
    assert local_names[0] == "page-apple.jpg"
    assert local_names[1] == "page-apple-2.jpg"


def test_duplicate_image_reference_reuses_same_local_name() -> None:
    html = b"""
    <html><body>
      <img src="/a/apple.jpg" />
      <img src="/a/apple.jpg" />
    </body></html>
    """
    doc = convert_html(html, base_url="https://ex.com/page.html", doc_basename="page")
    # Only ONE image to download, but both refs in markdown point at the same local file.
    assert len(doc.images) == 1
    assert doc.markdown.count("page-apple.jpg") == 2


# ---------------------------------------------------------------------------
# Main-content extraction (§4.4.1)
# ---------------------------------------------------------------------------


TEMPLATED_PAGE = b"""
<html><head><title>Employment Pass eligibility</title></head><body>
<header><a href="/"><img src="/static/logo.png" alt="logo"/></a>
<nav><a href="/passes">Passes</a> <a href="/about">About</a></nav></header>
<main><article>
<p>The <b>Employment Pass</b> allows foreign professionals to work here. Applicants
must earn a fixed monthly salary of at least the amounts below and pass the
<a href="/compass">COMPASS</a> framework. This paragraph is padded so the extractor
has ample text to treat this as the main content of the page.</p>
<h2>Salary benchmarks</h2>
<table><tr><th>Age</th><th>Salary</th></tr><tr><td>23</td><td>$5,600</td></tr></table>
<p>See the diagram below for how the points are awarded across the four
foundational criteria of the framework.</p>
<img src="/media/diagram.png" alt="COMPASS diagram"/>
</article></main>
<section id="comments"><p>Great article, thanks! I have been waiting for this
guidance for a very long time indeed.</p></section>
<footer><p>Ministry of Manpower. All rights reserved.</p></footer>
</body></html>
"""


def _convert_templated(**kwargs):
    return convert_html(
        TEMPLATED_PAGE, base_url="https://ex.com/dir/page.html", doc_basename="page", **kwargs
    )


def test_extraction_drops_nav_comments_and_footer() -> None:
    doc = _convert_templated()
    assert doc.extracted is True
    assert doc.fallback_reason is None
    assert "Employment Pass" in doc.markdown
    assert "About" not in doc.markdown
    assert "All rights reserved" not in doc.markdown
    assert "waiting for this" not in doc.markdown


def test_extraction_preserves_formatting_tables_links_and_images() -> None:
    doc = _convert_templated()
    assert "**Employment Pass**" in doc.markdown          # bold
    assert "## Salary benchmarks" in doc.markdown          # heading
    assert "| Age | Salary |" in doc.markdown              # table
    assert "[COMPASS](https://ex.com/compass)" in doc.markdown  # absolutized in-body link
    assert "![COMPASS diagram](page-diagram.png)" in doc.markdown
    assert doc.feature_counts["tables"] == 1


def test_traversal_links_still_come_from_the_whole_dom() -> None:
    """Nav is dropped from the output but is still how the site is discovered."""
    doc = _convert_templated()
    assert "https://ex.com/passes" in doc.links
    assert "https://ex.com/about" in doc.links


def test_only_images_surviving_extraction_are_reported() -> None:
    """The header logo is chrome — it must never be queued for download."""
    doc = _convert_templated()
    local_names = {n for _, n in doc.images}
    assert local_names == {"page-diagram.png"}


def test_title_is_synthesized_when_body_has_no_h1() -> None:
    doc = _convert_templated()
    assert doc.title_synthesized is True
    assert doc.markdown.startswith("# Employment Pass eligibility")


def test_no_extract_returns_whole_dom_with_chrome() -> None:
    doc = _convert_templated(extract=False)
    assert doc.extracted is False
    assert doc.fallback_reason == "disabled"
    assert "All rights reserved" in doc.markdown
    assert "About" in doc.markdown
    # Whole-DOM path references every image, logo included.
    assert {n for _, n in doc.images} == {"page-logo.png", "page-diagram.png"}


def test_short_page_falls_back_to_whole_dom() -> None:
    html = b"<html><body><nav>Home | Docs</nav><p>Tiny.</p></body></html>"
    doc = convert_html(html, base_url="https://ex.com/p.html", doc_basename="p")
    assert doc.extracted is False
    assert doc.fallback_reason in {"no_output", "too_short"}
    assert "Home | Docs" in doc.markdown


def test_min_extract_chars_gates_short_extractions() -> None:
    """The same short page: rejected at the default threshold, accepted at 0."""
    html = (
        b"<html><body><nav>Home | Docs</nav><article><p>A short but real body of "
        b"text that trafilatura can still find.</p></article></body></html>"
    )
    kw = dict(base_url="https://ex.com/p.html", doc_basename="p")
    strict = convert_html(html, **kw, min_extract_chars=500)
    assert strict.extracted is False
    assert strict.fallback_reason == "too_short"

    lax = convert_html(html, **kw, min_extract_chars=0)
    assert lax.extracted is True
    assert lax.fallback_reason is None
    assert "A short but real body" in lax.markdown


def test_table_missing_delimiter_row_is_repaired() -> None:
    """Sites that use <td> for header cells still yield a renderable table."""
    html = (
        b"<html><body><article>"
        b"<p>Salary benchmarks by age, published annually and used to assess every "
        b"application against the prevailing criteria for the pass in question. The "
        b"figures below are the ones the assessing officer applies, and they are "
        b"revised each year in line with the wider labour market.</p>"
        b"<table><tr><td>Age</td><td>Salary</td></tr>"
        b"<tr><td>23</td><td>$5,600</td></tr>"
        b"<tr><td>45</td><td>$10,700</td></tr></table>"
        b"</article></body></html>"
    )
    doc = convert_html(html, base_url="https://ex.com/p.html", doc_basename="p")
    lines = [l for l in doc.markdown.split("\n") if l.startswith("|")]
    assert lines[0].startswith("| Age | Salary |")
    assert lines[1] == "|---|---|"
    assert doc.feature_counts["tables"] == 1


def test_existing_delimiter_row_is_left_alone() -> None:
    html = (
        b"<html><body><article>"
        b"<p>Salary benchmarks by age, published annually and used to assess every "
        b"application against the prevailing criteria for the pass in question. The "
        b"figures below are the ones the assessing officer applies, and they are "
        b"revised each year in line with the wider labour market.</p>"
        b"<table><tr><th>Age</th><th>Salary</th></tr>"
        b"<tr><td>23</td><td>$5,600</td></tr></table>"
        b"</article></body></html>"
    )
    doc = convert_html(html, base_url="https://ex.com/p.html", doc_basename="p")
    assert doc.markdown.count("|---|---|") == 1
