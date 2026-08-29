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
