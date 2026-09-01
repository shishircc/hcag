"""HTML → Markdown conversion for `crawl` (§4.4.1, §4.4.3).

Three stages, per §4.4.1:

1. **DOM pre-pass (BeautifulSoup).** Absolutize every ``<a href>`` (both for
   the traversal loop and so in-body links survive as absolute URLs), promote
   lazy-loading image attributes into ``src``, and rewrite every ``<img src>``
   in place to a local filename of the form ``<doc-basename>-<remote-basename>``
   (with in-doc collision disambiguation). Nothing is removed here.
2. **Reading-mode extraction (trafilatura).** The mutated DOM is handed to
   ``trafilatura.extract()``, which returns Markdown for the page's main
   content only — navigation, breadcrumbs, sidebars, cookie banners, comment
   threads, and footers are dropped by construction, while headings, bold /
   italic, lists, tables, code, in-body links, and content images are kept.
3. **Fallback.** If extraction yields nothing (or less than
   ``min_extract_chars``), or the caller passed ``extract=False``, the whole
   DOM is converted with markdownify and returned verbatim, chrome included.
   The caller logs the fallback; `crawl` never runs a second stripping pass.

Only images still referenced by the resulting Markdown are reported back to
the caller, so chrome images cost zero HTTP requests (§4.4.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify

DEFAULT_MIN_EXTRACT_CHARS = 200  # §4.2 --min-extract-chars

FAVOR_CHOICES = ("balanced", "precision", "recall")

# Attributes lazy-loading templates use instead of a real `src` (§4.4.1 stage 1).
_LAZY_SRC_ATTRS = ("data-src", "data-original", "data-lazy-src")

# Reasons a page took the whole-DOM path (§4.4.1 stage 3).
FALLBACK_NO_OUTPUT = "no_output"
FALLBACK_TOO_SHORT = "too_short"
FALLBACK_DISABLED = "disabled"


@dataclass
class ConvertedHtml:
    markdown: str
    links: list[str] = field(default_factory=list)
    # (remote_url, local_filename) — only images the final Markdown references.
    images: list[tuple[str, str]] = field(default_factory=list)
    # True when the Markdown is trafilatura's main content; False when the page
    # fell back to whole-DOM conversion (§4.4.1 stage 3).
    extracted: bool = True
    fallback_reason: str | None = None
    markdown_chars: int = 0
    text_chars: int = 0          # DOM visible-text length — denominator of retained_pct
    title_synthesized: bool = False
    feature_counts: dict[str, int] = field(default_factory=dict)

    @property
    def retained_pct(self) -> float:
        """Extracted Markdown chars ÷ the DOM's visible-text chars (§4.7)."""
        if self.text_chars <= 0:
            return 0.0
        return round(100.0 * self.markdown_chars / self.text_chars, 1)


def _local_image_name(doc_basename: str, remote_url: str, taken: set[str]) -> str:
    """Build a filename `<doc-basename>-<remote-basename>` unique within a doc.

    If the derived name is already used in this document, append `-2`, `-3`,
    etc. before the extension. Cross-document uniqueness is guaranteed by
    the doc-basename prefix (§4.5).
    """
    remote_path = urlparse(remote_url).path
    raw = remote_path.rsplit("/", 1)[-1] or "image"
    if "." in raw:
        stem, ext = raw.rsplit(".", 1)
        ext = "." + ext
    else:
        stem, ext = raw, ""
    candidate = f"{doc_basename}-{stem}{ext}"
    if candidate not in taken:
        taken.add(candidate)
        return candidate
    n = 2
    while True:
        candidate = f"{doc_basename}-{stem}-{n}{ext}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
        n += 1


def _image_source(img) -> str | None:
    """Best `src` for an `<img>`, promoting lazy-loading attributes (§4.4.1)."""
    src = (img.get("src") or "").strip()
    if src and not src.startswith("data:"):
        return src
    for attr in _LAZY_SRC_ATTRS:
        candidate = (img.get(attr) or "").strip()
        if candidate and not candidate.startswith("data:"):
            return candidate
    srcset = (img.get("srcset") or "").strip()
    if srcset:
        first = srcset.split(",", 1)[0].strip().split(" ", 1)[0].strip()
        if first and not first.startswith("data:"):
            return first
    return None


def _collect_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Absolutize every `<a href>` in place; return the traversal candidates."""
    links: list[str] = []
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue
        href = href.strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        try:
            absolute = urljoin(base_url, href)
        except ValueError:
            continue
        # In-body links survive extraction as absolute source URLs (§4.8).
        a["href"] = absolute
        links.append(absolute)
    return links


def _rewrite_images(soup: BeautifulSoup, base_url: str, doc_basename: str) -> list[tuple[str, str]]:
    """Point every `<img src>` at its local filename; return (remote, local)."""
    images: list[tuple[str, str]] = []
    taken: set[str] = set()
    remote_to_local: dict[str, str] = {}
    for img in soup.find_all("img"):
        src = _image_source(img)
        if not src:
            continue
        try:
            absolute = urljoin(base_url, src)
        except ValueError:
            continue
        if absolute in remote_to_local:
            img["src"] = remote_to_local[absolute]
            continue
        local = _local_image_name(doc_basename, absolute, taken)
        remote_to_local[absolute] = local
        img["src"] = local
        images.append((absolute, local))
    return images


def _favor_flags(favor: str) -> dict[str, bool]:
    if favor == "precision":
        return {"favor_precision": True, "favor_recall": False}
    if favor == "recall":
        return {"favor_precision": False, "favor_recall": True}
    return {"favor_precision": False, "favor_recall": False}


def _extract_main_content(html_text: str, favor: str) -> str | None:
    """Run trafilatura with the option set fixed by §4.4.1 stage 2.

    ``url=`` is deliberately NOT passed: trafilatura resolves relative image
    sources against it, which would undo the local-filename rewriting done in
    stage 1. Links are already absolute by then, so nothing is lost.
    """
    return trafilatura.extract(
        html_text,
        output_format="markdown",
        include_formatting=True,
        include_links=True,
        include_tables=True,
        include_images=True,
        include_comments=False,
        # trafilatura's near-duplicate cache spans calls; cross-page decisions
        # are out of scope for `crawl` (§4.8 "Corpus-level content analysis").
        deduplicate=False,
        **_favor_flags(favor),
    )


def _whole_dom_markdown(soup: BeautifulSoup) -> str:
    md = markdownify(str(soup), heading_style="ATX")
    return _tidy(md)


# A table row, and a GFM delimiter row (`|---|---|`) respectively (§4.4.1).
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_DELIM_LINE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")


def _cell_count(row: str) -> int:
    return max(1, len(row.strip().strip("|").split("|")))


def _normalize_tables(md: str) -> str:
    """Insert the GFM delimiter row a table run is missing.

    Sites that mark header cells up as `<td>` rather than `<th>` yield a run of
    `| … |` rows with no `|---|---|` under the first one, which no Markdown
    renderer (or Markdown-aware chunker) reads as a table. Adding the delimiter
    is a pure formatting repair — no cell content is touched.
    """
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _TABLE_LINE.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        end = i
        while end < len(lines) and _TABLE_LINE.match(lines[end]):
            end += 1
        run = lines[i:end]
        if len(run) >= 2 and not _DELIM_LINE.match(run[1]):
            delim = "|" + "|".join(["---"] * _cell_count(run[0])) + "|"
            run = [run[0], delim, *run[1:]]
        out.extend(run)
        i = end
    return "\n".join(out)


def _tidy(md: str) -> str:
    md = _normalize_tables(md)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def _page_title(html_text: str) -> str | None:
    try:
        meta = trafilatura.extract_metadata(html_text)
    except Exception:  # pragma: no cover — metadata is best-effort
        return None
    title = getattr(meta, "title", None) if meta else None
    title = (title or "").strip()
    return title or None


def _count_features(md: str) -> dict[str, int]:
    return {
        "headings": len(re.findall(r"(?m)^#{1,6} ", md)),
        "tables": len(re.findall(r"(?m)^\|[- :|]+\|\s*$", md)),
        "code_blocks": md.count("```") // 2,
        "links": len(re.findall(r"(?<!!)\[[^\]]*\]\([^)]*\)", md)),
        "images": len(re.findall(r"!\[[^\]]*\]\([^)]*\)", md)),
    }


def _referenced_images(
    md: str, images: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Keep only images the Markdown still points at (§4.4.3)."""
    kept: list[tuple[str, str]] = []
    for remote, local in images:
        if re.search(r"\]\(\s*" + re.escape(local) + r"(?:[\s)\"])", md):
            kept.append((remote, local))
    return kept


def convert_html(
    html: bytes,
    base_url: str,
    doc_basename: str,
    *,
    extract: bool = True,
    favor: str = "balanced",
    min_extract_chars: int = DEFAULT_MIN_EXTRACT_CHARS,
) -> ConvertedHtml:
    """Convert HTML to Markdown, returning links to follow and images to fetch.

    `base_url` is the URL the HTML was fetched from (after redirects); it is
    used to resolve relative hrefs and image srcs before they leave this
    function — so the caller sees absolute URLs everywhere.
    """
    soup = BeautifulSoup(html, "html.parser")

    links = _collect_links(soup, base_url)
    images = _rewrite_images(soup, base_url, doc_basename)
    html_text = str(soup)
    text_chars = len(soup.get_text(" ", strip=True))

    markdown: str | None = None
    fallback_reason: str | None = None
    title_synthesized = False

    if not extract:
        fallback_reason = FALLBACK_DISABLED
    else:
        candidate = _extract_main_content(html_text, favor)
        if candidate is None or not candidate.strip():
            fallback_reason = FALLBACK_NO_OUTPUT
        elif len(candidate.strip()) < min_extract_chars:
            fallback_reason = FALLBACK_TOO_SHORT
            markdown = None
        else:
            markdown = _tidy(candidate)

    if markdown is None:
        markdown = _whole_dom_markdown(soup)
    elif not markdown.lstrip().startswith("# "):
        # The <h1> often lives in the template header, outside the extracted
        # body — restore it from metadata so the packet names its topic (§4.4.1).
        title = _page_title(html_text)
        if title:
            markdown = f"# {title}\n\n{markdown}"
            title_synthesized = True

    return ConvertedHtml(
        markdown=markdown,
        links=links,
        images=_referenced_images(markdown, images),
        extracted=fallback_reason is None,
        fallback_reason=fallback_reason,
        markdown_chars=len(markdown),
        text_chars=text_chars,
        title_synthesized=title_synthesized,
        feature_counts=_count_features(markdown),
    )
