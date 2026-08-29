"""URL utilities for `crawl` (§4.3.1, §4.3.2, §4.5).

Three concerns live here:

- `normalize_url` — canonical form used for the visited set and for prefix
  comparison. Lowercases scheme/host, strips default ports, strips the
  fragment. Path and query are preserved verbatim.

- `in_scope` — is a discovered URL covered by any seed's prefix (§4.3.1)?

- `url_to_output_paths` — maps a URL to its local `./kb/<domain>/<path>/…`
  Markdown target (§4.5) and returns the document basename used to prefix
  image filenames.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse, urlunparse

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    """Canonicalize a URL for visited-set and prefix comparison.

    Only fields that don't change document identity are touched: scheme and
    host are lowercased, the default port is stripped, and the fragment is
    dropped. Path and query are kept exactly as given so distinct URLs are
    not collapsed.
    """
    p = urlparse(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    port = p.port
    if port is not None and _DEFAULT_PORTS.get(scheme) == port:
        port = None
    netloc = host + (f":{port}" if port else "")
    path = p.path or "/"
    return urlunparse((scheme, netloc, path, p.params, p.query, ""))


def in_scope(url: str, normalized_seeds: list[str]) -> bool:
    """True iff `url` begins with any seed's normalized form (§4.3.1)."""
    n = normalize_url(url)
    return any(n.startswith(seed) for seed in normalized_seeds)


def _sanitize_segment(segment: str) -> str:
    """Make a URL path segment safe as a filesystem name.

    Replaces path-separator characters, strips leading dots (so a segment
    like `.hidden` doesn't produce a hidden directory), and empties collapse
    to `_`.
    """
    seg = segment.replace("/", "_").replace("\\", "_")
    seg = seg.lstrip(".")
    return seg or "_"


def url_to_output_paths(url: str, kb_root: Path) -> tuple[Path, str]:
    """Compute (markdown_path, doc_basename) for `url` under `kb_root` (§4.5).

    - The domain becomes the first path segment under `kb_root`.
    - URL path segments become directories, mirroring the site's shape.
    - The final segment (with any extension stripped) becomes both the
      Markdown filename (`.md`) and the basename prefix used for image
      filenames extracted from this document.
    - A URL ending in `/` or empty (directory index) gets the synthetic
      basename `index`.
    """
    p = urlparse(url)
    host = (p.hostname or "unknown").lower()
    raw_path = p.path or "/"

    if raw_path.endswith("/") or raw_path == "":
        dir_segments = [_sanitize_segment(s) for s in raw_path.split("/") if s]
        basename = "index"
    else:
        segments = [s for s in raw_path.split("/") if s]
        last = segments[-1]
        dir_segments = [_sanitize_segment(s) for s in segments[:-1]]
        stem = last.rsplit(".", 1)[0] if "." in last else last
        basename = _sanitize_segment(stem)

    out_dir = kb_root / _sanitize_segment(host)
    for seg in dir_segments:
        out_dir = out_dir / seg
    return out_dir / f"{basename}.md", basename
