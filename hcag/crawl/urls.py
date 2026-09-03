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

import json

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


#: Filename for a page written at its own path's level (§4.5).
OWN_PAGE_STEM = "index"


def url_to_output_paths(url: str, kb_root: Path) -> tuple[Path, str]:
    """Compute (markdown_path, doc_basename) for `url` under `kb_root` (§4.5).

    - The domain becomes the first path segment under `kb_root`.
    - **Every** URL path segment becomes a directory, the last one included, and
      the page is written as ``index.md`` inside it. A page's Markdown belongs
      at the deepest level of its own URL path, not at its parent's — so
      ``/topic/subtopic`` is ``topic/subtopic/index.md``, sitting alongside its
      children rather than beside the folder that holds them.
    - `doc_basename` is therefore always ``index``, and images extracted from
      the page are ``index-<image-name>`` in that same directory.

    Pages that turn out to have no children are flattened back to
    ``<segment>.md`` after the crawl, by `collapse_leaf_dirs` — a decision that
    cannot be made here because breadth-first traversal has not yet discovered
    whether this page has children (§4.5.2).
    """
    p = urlparse(url)
    host = (p.hostname or "unknown").lower()
    raw_path = p.path or "/"

    segments = [s for s in raw_path.split("/") if s]
    if segments and not raw_path.endswith("/"):
        # Strip a file extension from the last segment: `/a/b/c.html` is the
        # page `c`, and its directory should be `c/`, not `c.html/`.
        last = segments[-1]
        segments[-1] = last.rsplit(".", 1)[0] if "." in last else last

    out_dir = kb_root / _sanitize_segment(host)
    for seg in segments:
        sanitized = _sanitize_segment(seg)
        if sanitized:
            out_dir = out_dir / sanitized
    return out_dir / f"{OWN_PAGE_STEM}.md", OWN_PAGE_STEM


IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})


def _is_own_page_asset(name: str) -> bool:
    """True for an image belonging to this directory's own page."""
    from pathlib import PurePosixPath

    return name.startswith(f"{OWN_PAGE_STEM}-") and (
        PurePosixPath(name).suffix.lower() in IMAGE_EXTS
    )


def collapse_leaf_dirs(kb_root: Path, on_collapse=None) -> int:
    """Flatten directories that turned out to hold only their own page (§4.5.2).

    `url_to_output_paths` writes every page deep, because breadth-first
    traversal cannot know at write time whether a page will have children.
    Once the crawl is done the filesystem answers that: a directory holding
    nothing but `index.md` and that page's images is a leaf, and collapses to
    `<name>.md` beside its former parent.

    Keying off the filesystem rather than the URL set keeps this correct when a
    page was skipped mid-crawl — a fetch error, an out-of-scope redirect, an
    unsupported content type. A directory is a branch because it *has* files in
    it, not because some URL suggested it might.

    Returns the number of directories collapsed. `on_collapse(dir, md, images)`
    is called for each, for logging.
    """
    if not kb_root.is_dir():
        return 0

    collapsed = 0
    # Deepest first: a directory can only be judged a leaf after its own
    # children have had their chance to collapse into files within it.
    for directory in sorted(
        (d for d in kb_root.rglob("*") if d.is_dir()),
        key=lambda d: len(d.parts),
        reverse=True,
    ):
        # The host directory is the domain separator (§4.5, "Domain first").
        # Collapsing it would put a bare `<host>.md` at the KB root.
        if directory.parent == kb_root:
            continue

        own_page = directory / f"{OWN_PAGE_STEM}.md"
        if not own_page.is_file():
            continue
        entries = list(directory.iterdir())
        if any(e.is_dir() for e in entries):
            continue
        assets = [e for e in entries if e != own_page]
        if not all(_is_own_page_asset(e.name) for e in assets):
            continue

        name = directory.name
        target_md = directory.parent / f"{name}.md"
        if target_md.exists():
            # Would violate the §4.5 invariant from the other direction. Leave
            # the tree as-is rather than clobbering a sibling page.
            continue

        text = own_page.read_text(encoding="utf-8")
        renamed = []
        moves: dict[Path, Path] = {}
        for asset in assets:
            new_name = f"{name}-{asset.name[len(OWN_PAGE_STEM) + 1:]}"
            target = directory.parent / new_name
            asset.rename(target)
            # The Markdown references images by bare filename (§4.4.1 stage 1),
            # so moving one without rewriting its reference breaks the image.
            text = text.replace(asset.name, new_name)
            renamed.append(new_name)
            moves[asset] = target

        target_md.write_text(text, encoding="utf-8")
        own_page.unlink()
        directory.rmdir()
        moves[own_page] = target_md
        collapsed += 1
        if on_collapse is not None:
            # `moves` lets a caller follow provenance recorded against the
            # pre-collapse paths (§4.5.3) — every fact about where a file came
            # from was learned before the tree took its final shape.
            on_collapse(directory, target_md, renamed, moves)

    return collapsed


def find_layout_collisions(kb_root: Path) -> list[Path]:
    """Directories `X/` that sit beside a file `X.md` — forbidden by §4.5.

    The postcondition of `collapse_leaf_dirs`, and the one condition that
    distinguishes this layout from the one it replaces.
    """
    if not kb_root.is_dir():
        return []
    return [
        d
        for d in sorted(kb_root.rglob("*"))
        if d.is_dir() and (d.parent / f"{d.name}.md").is_file()
    ]


#: Per-folder provenance written by `crawl`, read by `hcag preprocess` (§4.5.3).
SIDECAR_NAME = ".hcag-crawl.json"


def _slug_of(url: str) -> str:
    """Last meaningful path segment of `url`, extension stripped."""
    path = urlparse(url).path or ""
    segments = [s for s in path.split("/") if s]
    if not segments:
        return ""
    last = segments[-1]
    stem = last.rsplit(".", 1)[0] if "." in last else last
    return _sanitize_segment(stem)


def write_sidecar(
    folder: Path,
    source_url: str | None = None,
    links: list[str] | None = None,
    documents: dict[str, str] | None = None,
    images: dict[str, str] | None = None,
) -> list[str]:
    """Write `folder`'s crawl sidecar (§4.5.3); return the link order recorded.

    Two jobs, and provenance is the one that applies everywhere:

    - `documents` / `images` map each file the crawl wrote here to the URL it
      came from. A filename is a sanitized, collapsed, extension-stripped
      derivative of its URL and cannot be inverted, so without this a mirrored
      tree has no way back to its sources — nothing can verify a generated
      eval question (§6.7.1), or tell whether a page has changed since.
    - `link_order` records the child slugs the folder's index page linked, in
      full-DOM document order (§3.4.3). Only meaningful where an `index.md`
      survives, so it is simply absent otherwise.

    A sidecar is written for **every folder holding documents**, not only those
    that kept an index page: a folder of collapsed leaves still holds files
    whose origin someone will want.
    """
    own_page = folder / f"{OWN_PAGE_STEM}.md"
    recorded: list[str] = []
    if own_page.is_file() and links:
        present = {p.stem for p in folder.glob("*.md") if p.name != f"{OWN_PAGE_STEM}.md"}
        present |= {p.name for p in folder.iterdir() if p.is_dir()}
        for link in links:
            slug = _slug_of(link)
            # A slug naming nothing that was written is dropped, so the sidecar
            # can never point at a file that does not exist.
            if slug and slug in present and slug not in recorded:
                recorded.append(slug)

    payload: dict[str, object] = {}
    if source_url:
        payload["source_url"] = source_url
    if recorded:
        payload["link_order"] = recorded
    if documents:
        payload["documents"] = dict(sorted(documents.items()))
    if images:
        payload["images"] = dict(sorted(images.items()))
    if not payload:
        return recorded

    (folder / SIDECAR_NAME).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return recorded


def read_sidecar(folder: Path) -> dict:
    """Read a folder's sidecar, or ``{}``. Never raises — provenance is a
    convenience, and a malformed one must not fail a build."""
    path = folder / SIDECAR_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def read_link_order(folder: Path) -> list[str]:
    """Read `folder`'s recorded link order, or ``[]`` if there is none.

    Never raises: a sidecar is an ordering *preference* (§3.4.3), so a missing,
    unreadable or malformed one degrades to no signal rather than failing a
    build over provenance.
    """
    return [str(x) for x in read_sidecar(folder).get("link_order", [])]


PDF_EXTS = frozenset({".pdf"})


def is_asset_url(url: str) -> bool:
    """True for a URL that is an asset rather than a page (§4.3.4).

    Assets are terminal — fetched, converted, written, never crawled *from* —
    which is what makes exempting them from prefix scope and the depth limit
    safe: nothing is ever discovered through one, so the frontier cannot grow.
    """
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in PDF_EXTS)


def asset_host_allowed(url: str, referrer: str, extra_hosts: frozenset[str] | set[str]) -> bool:
    """Is `url` on a host this crawl may fetch assets from (§4.3.4)?

    Lifting the *path* restriction is not lifting the *host* restriction:
    following a citation off-domain by default is a different risk class from
    mirroring a site. Default is the citing page's own host; `--asset-hosts`
    widens it to a CDN or media subdomain.
    """
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return host == (urlparse(referrer).hostname or "").lower() or host in extra_hosts
