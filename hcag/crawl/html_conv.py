"""HTML → Markdown conversion for `crawl` (§4.4.1, §4.4.3).

Given raw HTML bytes:

- Parse with BeautifulSoup.
- Extract every `<a href>` target (resolved to an absolute URL) for the
  traversal loop.
- Extract every `<img src>` target, assign a local filename of the form
  `<doc-basename>-<remote-basename>` (with in-doc collision disambiguation),
  and rewrite the `<img src>` attribute in-place so the resulting Markdown
  points at the local file.
- Convert the (mutated) HTML to Markdown with markdownify.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify


@dataclass
class ConvertedHtml:
    markdown: str
    links: list[str] = field(default_factory=list)
    images: list[tuple[str, str]] = field(default_factory=list)


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


def convert_html(html: bytes, base_url: str, doc_basename: str) -> ConvertedHtml:
    """Convert HTML to Markdown, returning links to follow and images to fetch.

    `base_url` is the URL the HTML was fetched from (after redirects); it is
    used to resolve relative hrefs and image srcs before they leave this
    function — so the caller sees absolute URLs everywhere.
    """
    soup = BeautifulSoup(html, "html.parser")

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
        links.append(absolute)

    images: list[tuple[str, str]] = []
    taken: set[str] = set()
    remote_to_local: dict[str, str] = {}
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        src = src.strip()
        if not src or src.startswith("data:"):
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

    md = markdownify(str(soup), heading_style="ATX")
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    return ConvertedHtml(markdown=md, links=links, images=images)
