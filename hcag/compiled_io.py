"""Read/write the single `compiled.md` artifact per folder (§3.7).

Every folder in a normalized KB — leaf, taxonomy node, mixed, and root alike —
has exactly one ``compiled.md``. Its front-matter carries the folder's own
metadata and its body carries a delimited ``## Content`` section holding that
folder's own source markdown. The **root's** additionally carries the KB's one
``## Catalog`` table (D3a).

Two things are deliberate here and were not true of the design this replaces:

- **The catalog lives only at the root.** There is no per-folder subtree index
  to roll up, re-parent, or elide at load time. One index, in one place, that
  cannot disagree with itself.
- **Sections are delimited, not merely headed.** Locating a section by scanning
  for its ``##`` heading works until a crawled source document contains a
  heading called "Content" — and crawled corpora do. The consequence of that
  mis-parse is silent: content read as catalog, or a truncated packet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import frontmatter


HCAG_COMPILED_MARKER = "<!-- HCAG:COMPILED"

CATALOG_BEGIN = "<!-- HCAG:CATALOG BEGIN -->"
CATALOG_END = "<!-- HCAG:CATALOG END -->"
CONTENT_BEGIN = "<!-- HCAG:CONTENT BEGIN -->"
CONTENT_END = "<!-- HCAG:CONTENT END -->"

FolderKind = Literal["leaf", "node", "mixed"]

#: Rendered in place of an empty root id so ids stay readable in a table cell.
#: A KB that sets `[compiled] root_id = "_root"` produces the same string.
ROOT_DISPLAY_ID = "_root"

CATALOG_COLUMNS = ["id", "path", "depth", "title", "long"]


@dataclass
class CompiledFrontMatter:
    """Front-matter of a folder's ``compiled.md`` (§3.4.3, §3.7)."""

    id: str
    title: str
    long_description: str
    token_size_estimate: int
    kind: FolderKind
    source_files: list[str] = field(default_factory=list)  # empty for pure nodes
    children: list[str] = field(default_factory=list)      # IMMEDIATE children only
    # Crawl provenance (§4.5.3), copied not verified. `source_urls` is
    # positionally aligned with `source_files`; an entry is "" where the origin
    # is unknown — a hand-authored file, or a KB crawled before provenance.
    source_urls: list[str] = field(default_factory=list)
    image_urls: dict[str, str] = field(default_factory=dict)
    descendants: int = 0            # folders in this subtree, excluding self
    subtree_depth: int = 0          # depth of the deepest descendant, relative to here
    content_token_estimate: int = 0  # ## Content + images — the runtime budgeting figure
    catalog_token_estimate: int = 0  # ## Catalog table — root only, 0 elsewhere


@dataclass
class CatalogRecord:
    """One row of the root's ``## Catalog`` table (§2.2.1).

    ``id`` and ``path`` are absolute from the KB root, so a row is created once
    — at the folder it describes, with its final coordinates — and travels up
    the recursion unchanged (§3.4.1). An id read from the catalog is the id
    ``check_and_load_kb`` resolves.

    There is deliberately no ``parent`` (an id minus its last segment), no
    ``kind`` (every row is a loadable, content-bearing folder) and no token
    count (the memory module budgets from front-matter, and a number the model
    cannot act on is noise in several hundred rows).
    """

    id: str
    path: str
    depth: int
    title: str
    long: str = ""


def is_hcag_generated(path: Path) -> bool:
    """True iff ``path`` starts with the HCAG:COMPILED marker."""
    if not path.is_file():
        return False
    with path.open("r", encoding="utf-8") as f:
        head = f.read(2048)
    return HCAG_COMPILED_MARKER in head


# --- Rendering --------------------------------------------------------------


def _display_id(packet_id: str) -> str:
    return packet_id or ROOT_DISPLAY_ID


def _as_dir(path: str) -> str:
    path = path.strip("/")
    return f"{path}/" if path else ""


def _cell(text: str) -> str:
    """Make prose safe for a table cell without shortening it.

    A `long_description` is prose and will contain `|`; a newline inside a cell
    would end the row. Both are neutralized. What is *not* done is truncation:
    a table that quietly dropped the second half of a description would
    reintroduce, as a rendering detail, the information loss D3a removed.
    """
    return " ".join(str(text).split()).replace("|", r"\|")


def render_catalog_table(records: list[CatalogRecord]) -> str:
    """Render the ``## Catalog`` section (header and delimiters included)."""
    if not records:
        return ""
    lines = [
        CATALOG_BEGIN,
        "## Catalog",
        "",
        "| " + " | ".join(CATALOG_COLUMNS) + " |",
        "|" + "---|" * len(CATALOG_COLUMNS),
    ]
    for r in records:
        lines.append(
            f"| `{_display_id(r.id)}` | `{_as_dir(r.path)}` | {r.depth} "
            f"| {_cell(r.title)} | {_cell(r.long)} |"
        )
    lines.append(CATALOG_END)
    return "\n".join(lines) + "\n"


def _render_body(
    fm: CompiledFrontMatter,
    own_sections: list[tuple[str, str]],
    catalog: list[CatalogRecord] | None,
) -> str:
    parts: list[str] = [f"# {fm.title}", ""]

    table = render_catalog_table(catalog or [])
    if table:
        parts.append(table.rstrip())
        parts.append("")

    if own_sections:
        parts.append(CONTENT_BEGIN)
        parts.append("## Content")
        parts.append("")
        for name, content in own_sections:
            parts.append(f"<!-- source: {name} -->")
            parts.append(content.strip())
            parts.append("")
        parts.append(CONTENT_END)

    return "\n".join(parts).rstrip() + "\n"


def write_compiled_md(
    dest: Path,
    fm: CompiledFrontMatter,
    own_sections: list[tuple[str, str]],
    *,
    catalog: list[CatalogRecord] | None = None,
) -> None:
    """Write ``compiled.md``: marker, front-matter, then delimited sections.

    ``catalog`` is passed only for the root (D3a); every other folder writes
    its own content and nothing else.
    """
    marker = f"{HCAG_COMPILED_MARKER} id={_display_id(fm.id)} -->"
    metadata = {
        "id": fm.id,
        "title": fm.title,
        "token_size_estimate": fm.token_size_estimate,
        "content_token_estimate": fm.content_token_estimate,
        "kind": fm.kind,
        "source_files": fm.source_files,
        "source_urls": fm.source_urls,
        "image_urls": fm.image_urls,
        "children": fm.children,
        "descendants": fm.descendants,
        "subtree_depth": fm.subtree_depth,
    }
    # Absent rather than empty where the field has no meaning: a pure taxonomy
    # node has no content to describe, and only the root has a catalog.
    if fm.long_description:
        metadata["long_description"] = fm.long_description
    if fm.catalog_token_estimate:
        metadata["catalog_token_estimate"] = fm.catalog_token_estimate

    post = frontmatter.Post(
        content=_render_body(fm, own_sections, catalog), **metadata
    )
    text = marker + "\n" + frontmatter.dumps(post) + "\n"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


# --- Parsing ----------------------------------------------------------------


def _strip_marker(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith(HCAG_COMPILED_MARKER):
        return "\n".join(lines[1:])
    return text


def strip_compiled_frontmatter(raw: str) -> str:
    """Return the body of a compiled.md — marker + YAML front-matter stripped."""
    lines = _strip_marker(raw).splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1 :]
                break
    return "\n".join(lines).lstrip("\n")


def _frontmatter_to_model(m: dict) -> CompiledFrontMatter:
    kind = str(m.get("kind", "leaf"))
    if kind not in ("leaf", "node", "mixed"):
        kind = "leaf"
    total = int(m.get("token_size_estimate", 0) or 0)
    return CompiledFrontMatter(
        id=str(m.get("id", "")),
        title=str(m.get("title", "")),
        long_description=str(m.get("long_description", "") or ""),
        token_size_estimate=total,
        kind=kind,  # type: ignore[arg-type]
        source_files=list(m.get("source_files", []) or []),
        source_urls=[str(u or "") for u in (m.get("source_urls") or [])],
        image_urls={str(k): str(v) for k, v in (m.get("image_urls") or {}).items()},
        children=list(m.get("children", []) or []),
        descendants=int(m.get("descendants", 0) or 0),
        subtree_depth=int(m.get("subtree_depth", 0) or 0),
        content_token_estimate=int(m.get("content_token_estimate", total) or 0),
        catalog_token_estimate=int(m.get("catalog_token_estimate", 0) or 0),
    )


def read_compiled_frontmatter(path: Path) -> CompiledFrontMatter | None:
    """Load just the front-matter — cheap when only metadata is needed."""
    if not path.is_file():
        return None
    post = frontmatter.loads(_strip_marker(path.read_text(encoding="utf-8")))
    return _frontmatter_to_model(post.metadata)


_CATALOG_HEADER_RE = re.compile(r"^##\s+Catalog\s*$", re.MULTILINE)
_CONTENT_HEADER_RE = re.compile(r"^##\s+Content\s*$", re.MULTILINE)
_ROW_RE = re.compile(r"^\|(?P<cells>.*)\|\s*$")
_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")
#: Cells are separated by pipes the renderer did not escape. Splitting on every
#: pipe would tear a description containing one into extra columns and shift
#: every field after it — which is exactly what `_cell` escapes to prevent.
_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _between(body: str, begin: str, end: str) -> str | None:
    """Text between two markers, or None when they are absent or unbalanced.

    An unterminated section — a BEGIN with no END — reads as absent rather than
    as "everything to end of file", because a truncated write is exactly how it
    happens and reading past the end would serve half a file as though it were
    whole.
    """
    i = body.find(begin)
    if i < 0:
        return None
    j = body.find(end, i + len(begin))
    if j < 0:
        return None
    return body[i + len(begin) : j].strip()


def _section(body: str, begin: str, end: str, heading: re.Pattern[str]) -> str:
    """Extract a delimited section, falling back to its heading.

    The fallback is for artifacts written before the markers existed. It is the
    ambiguous read the markers were introduced to end, and it applies only to
    files that already had it.
    """
    marked = _between(body, begin, end)
    if marked is not None:
        return re.sub(heading, "", marked, count=1).strip()
    m = heading.search(body)
    if not m:
        return ""
    start = m.end()
    nxt = re.search(r"^##\s+\S", body[start:], re.MULTILINE)
    stop = start + nxt.start() if nxt else len(body)
    return body[start:stop].strip()


def extract_catalog_section(body: str) -> str:
    """Raw text of the ``## Catalog`` section, or ``""`` (root only)."""
    return _section(body, CATALOG_BEGIN, CATALOG_END, _CATALOG_HEADER_RE)


def extract_content_section(body: str) -> str:
    """Raw text of the ``## Content`` section, or ``""``.

    This is what a loaded packet ships (§2.6). Nothing has to be elided: only
    the root carries a catalog, and the root is not served as a packet.
    """
    return _section(body, CONTENT_BEGIN, CONTENT_END, _CONTENT_HEADER_RE)


def _uncell(text: str) -> str:
    return text.strip().replace(r"\|", "|")


def parse_catalog_table(body: str) -> list[CatalogRecord]:
    """Parse the ``## Catalog`` table into records, in the order written.

    Rows whose columns do not line up are skipped rather than guessed at: a
    half-parsed row becomes a packet id the agent cannot load.
    """
    section = extract_catalog_section(body)
    if not section:
        return []

    records: list[CatalogRecord] = []
    seen_header = False
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if _SEPARATOR_RE.match(line):
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in _CELL_SPLIT_RE.split(m.group("cells"))]
        if not seen_header:
            # The first row is the header; everything after it is data.
            seen_header = True
            if [c.lower() for c in cells[: len(CATALOG_COLUMNS)]] == CATALOG_COLUMNS:
                continue
        if len(cells) < 4:
            continue
        rid = _uncell(cells[0]).strip("`")
        if rid == ROOT_DISPLAY_ID:
            rid = ""
        try:
            depth = int(_uncell(cells[2]))
        except ValueError:
            continue
        records.append(
            CatalogRecord(
                id=rid,
                path=_uncell(cells[1]).strip("`/ "),
                depth=depth,
                title=_uncell(cells[3]),
                long=_uncell(cells[4]) if len(cells) > 4 else "",
            )
        )
    return records


def parse_compiled(raw: str) -> tuple[CompiledFrontMatter, list[CatalogRecord], str]:
    """Parse compiled.md *text* into front-matter + catalog rows + body.

    The string-level entry point, for callers (the memory module) that get
    bytes from a ``KBStorage`` rather than a path. Catalog rows are empty for
    every folder but the root.
    """
    post = frontmatter.loads(_strip_marker(raw))
    return (
        _frontmatter_to_model(post.metadata),
        parse_catalog_table(post.content),
        post.content,
    )


def read_compiled(path: Path) -> tuple[CompiledFrontMatter, list[CatalogRecord], str] | None:
    """Load front-matter + parsed catalog rows + raw body text (marker stripped).

    Returns ``None`` if ``path`` doesn't exist.
    """
    if not path.is_file():
        return None
    return parse_compiled(path.read_text(encoding="utf-8"))


__all__ = [
    "CATALOG_BEGIN",
    "CATALOG_COLUMNS",
    "CATALOG_END",
    "CONTENT_BEGIN",
    "CONTENT_END",
    "HCAG_COMPILED_MARKER",
    "ROOT_DISPLAY_ID",
    "CatalogRecord",
    "CompiledFrontMatter",
    "FolderKind",
    "extract_catalog_section",
    "extract_content_section",
    "is_hcag_generated",
    "parse_catalog_table",
    "parse_compiled",
    "read_compiled",
    "read_compiled_frontmatter",
    "render_catalog_table",
    "strip_compiled_frontmatter",
    "write_compiled_md",
]
