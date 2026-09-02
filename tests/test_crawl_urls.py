"""URL utilities for `crawl` (§4.3.1, §4.5)."""

from __future__ import annotations

from pathlib import Path

from hcag.crawl.urls import in_scope, normalize_url, url_to_output_paths


def test_normalize_lowercases_scheme_and_host() -> None:
    assert normalize_url("HTTPS://Example.COM/A") == "https://example.com/A"


def test_normalize_strips_default_ports_and_fragment() -> None:
    assert normalize_url("https://ex.com:443/a#frag") == "https://ex.com/a"
    assert normalize_url("http://ex.com:80/a") == "http://ex.com/a"
    assert normalize_url("https://ex.com:8443/a") == "https://ex.com:8443/a"


def test_normalize_preserves_query_and_path_case() -> None:
    assert normalize_url("https://Ex.com/Path?Q=1") == "https://ex.com/Path?Q=1"


def test_in_scope_prefix_match() -> None:
    seeds = [normalize_url("https://docs.example.com/api/v2/")]
    assert in_scope("https://docs.example.com/api/v2/auth.html", seeds)
    assert not in_scope("https://docs.example.com/api/v1/x", seeds)
    assert not in_scope("https://blog.example.com/", seeds)


def test_in_scope_multiple_seeds_are_union() -> None:
    seeds = [
        normalize_url("https://a.example.com/"),
        normalize_url("https://b.example.com/docs/"),
    ]
    assert in_scope("https://a.example.com/x", seeds)
    assert in_scope("https://b.example.com/docs/y", seeds)
    assert not in_scope("https://b.example.com/blog/z", seeds)


def test_url_to_output_paths_html(tmp_path: Path) -> None:
    """Every segment becomes a directory, the last one included: a page is
    written at the deepest level of its own URL path (§4.5). The extension is
    stripped so `/…/something.html` yields `something/`, not `something.html/`.
    Leaves are flattened back afterwards by `collapse_leaf_dirs` (§4.5.2)."""
    md, base = url_to_output_paths(
        "https://webdomain/topic-domain/topic/subtopic/something.html",
        tmp_path,
    )
    assert md == (
        tmp_path / "webdomain" / "topic-domain" / "topic" / "subtopic" / "something" / "index.md"
    )
    assert base == "index"


def test_url_to_output_paths_pdf_extension_becomes_md(tmp_path: Path) -> None:
    md, base = url_to_output_paths("https://ex.com/paper.pdf", tmp_path)
    assert md == tmp_path / "ex.com" / "paper" / "index.md"
    assert base == "index"


def test_url_to_output_paths_directory_index(tmp_path: Path) -> None:
    md, base = url_to_output_paths("https://ex.com/guide/", tmp_path)
    assert md == tmp_path / "ex.com" / "guide" / "index.md"
    assert base == "index"


def test_url_to_output_paths_bare_domain(tmp_path: Path) -> None:
    md, base = url_to_output_paths("https://ex.com", tmp_path)
    assert md == tmp_path / "ex.com" / "index.md"
    assert base == "index"


def test_url_to_output_paths_domain_case_folded(tmp_path: Path) -> None:
    md, _ = url_to_output_paths("https://Example.COM/a.html", tmp_path)
    assert md == tmp_path / "example.com" / "a" / "index.md"
