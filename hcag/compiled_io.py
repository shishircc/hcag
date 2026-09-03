"""Read/write the single `compiled.md` artifact per folder (§3.7).

Every folder in a normalized KB — leaf, taxonomy node, mixed, and root alike —
has exactly one ``compiled.md``. Its front-matter carries the folder's own
summary metadata, and its body optionally contains a ``## Sub-topics`` section
and/or a ``## Content`` section (concatenated source markdown for this level).

The ``## Sub-topics`` section is a **subtree index, not a child listing**
(D3a): it holds one record per descendant folder at *every* depth beneath the
catalog owner, in DFS pre-order. At the root that is every folder in the KB,
which is what lets the agent resolve any document in one hop (§2.7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import frontmatter


HCAG_COMPILED_MARKER = "<!-- HCAG:COMPILED"

FolderKind = Literal["leaf", "node", "mixed"]

#: Rendered in place of an empty root id so `parent` fields stay readable.
#: A KB that sets `[compiled] root_id = "_root"` produces the same string, which
#: is the point: `_root` names the root in `parent` fields either way. Parsing
#: leaves it alone rather than guessing it back to "" — that guess would discard
#: a deliberately configured root id. Use `Catalog.root_children()` when you want
#: the top level without caring which of the two a KB uses.
ROOT_DISPLAY_ID = "_root"


@dataclass
class CompiledFrontMatter:
    """Front-matter of a folder's ``compiled.md`` (§3.4.3, §3.7)."""

    id: str
    title: str
    short_description: str
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
    # Subtree roll-up metadata (D3a).
    descendants: int = 0            # entries in this folder's ## Sub-topics section
    subtree_depth: int = 0          # depth of the deepest descendant, relative to here
    content_token_estimate: int = 0  # ## Content + images — the runtime budgeting figure
    catalog_token_estimate: int = 0  # ## Sub-topics section alone


@dataclass
class CatalogRecord:
    """One entry inside a ``## Sub-topics`` section (§2.2).

    ``path`` is relative to the folder that *owns* the catalog, so a record is
    a self-contained locator. ``id`` and ``parent`` are absolute dotted paths
    from the KB root and therefore invariant as the record is rolled up
    (§3.4.4) — which is why an id read from the root catalog can be handed
    straight to ``check_and_load_kb``.
    """

    id: str
    path: str
    title: str
    short: str
    long: str = ""
    tokens: int = 0
    depth: int = 1
    parent: str = ""
    kind: FolderKind = "leaf"


#: Back-compat alias — records used to describe only immediate children.
ChildEntry = CatalogRecord


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


def render_subtopics_section(
    records: list[CatalogRecord],
    *,
    include_tree: bool = True,
) -> str:
    """Render a ``## Sub-topics`` section (header included) from subtree records.

    ``records`` must already be in DFS pre-order and already trimmed for
    ``catalog.max_depth`` / ``catalog.long_depth`` — this function renders
    exactly what it is given so that the CLI's token estimate and the bytes on
    disk cannot drift apart.
    """
    if not records:
        return ""

    parts: list[str] = ["## Sub-topics", ""]

    if include_tree:
        parts.append("#### Tree")
        parts.append("")
        for r in records:
            indent = "  " * max(0, r.depth - 1)
            parts.append(f"{indent}- `{r.id}` — {r.title}")
        parts.append("")

    for r in records:
        parts.append(f"#### `{r.id}`")
        parts.append(f"- **path**: `{_as_dir(r.path)}`")
        parts.append(f"- **depth**: {r.depth}")
        parts.append(f"- **parent**: `{_display_id(r.parent)}`")
        parts.append(f"- **kind**: {r.kind}")
        parts.append(f"- **title**: {r.title}")
        parts.append(f"- **short**: {r.short}")
        if r.long:
            parts.append(f"- **long**: {r.long}")
        parts.append(f"- **tokens**: {r.tokens}")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _render_body(
    fm: CompiledFrontMatter,
    subtopics: list[CatalogRecord],
    own_sections: list[tuple[str, str]],
    *,
    include_tree: bool = True,
) -> str:
    parts: list[str] = [f"# {fm.title}", ""]
    if fm.short_description:
        parts.append(fm.short_description.strip())
        parts.append("")

    section = render_subtopics_section(subtopics, include_tree=include_tree)
    if section:
        parts.append(section.rstrip())
        parts.append("")

    if own_sections:
        parts.append("## Content")
        parts.append("")
        for name, content in own_sections:
            parts.append(f"<!-- source: {name} -->")
            parts.append(content.strip())
            parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def write_compiled_md(
    dest: Path,
    fm: CompiledFrontMatter,
    subtopics: list[CatalogRecord],
    own_sections: list[tuple[str, str]],
    *,
    include_tree: bool = True,
) -> None:
    """Write ``compiled.md`` with marker + front-matter + body sections."""
    marker = f"{HCAG_COMPILED_MARKER} id={_display_id(fm.id)} -->"
    post = frontmatter.Post(
        content=_render_body(fm, subtopics, own_sections, include_tree=include_tree),
        **{
            "id": fm.id,
            "title": fm.title,
            "short_description": fm.short_description,
            "long_description": fm.long_description,
            "token_size_estimate": fm.token_size_estimate,
            "content_token_estimate": fm.content_token_estimate,
            "catalog_token_estimate": fm.catalog_token_estimate,
            "kind": fm.kind,
            "source_files": fm.source_files,
            "source_urls": fm.source_urls,
            "image_urls": fm.image_urls,
            "children": fm.children,
            "descendants": fm.descendants,
            "subtree_depth": fm.subtree_depth,
        },
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
        short_description=str(m.get("short_description", "")),
        long_description=str(m.get("long_description", "")),
        token_size_estimate=total,
        kind=kind,  # type: ignore[arg-type]
        source_files=list(m.get("source_files", []) or []),
        source_urls=[str(u or "") for u in (m.get("source_urls") or [])],
        image_urls={str(k): str(v) for k, v in (m.get("image_urls") or {}).items()},
        children=list(m.get("children", []) or []),
        descendants=int(m.get("descendants", 0) or 0),
        subtree_depth=int(m.get("subtree_depth", 0) or 0),
        # KBs built before the roll-up carry neither split estimate; falling
        # back to the total keeps them loadable and budgeted (conservatively).
        content_token_estimate=int(m.get("content_token_estimate", total) or 0),
        catalog_token_estimate=int(m.get("catalog_token_estimate", 0) or 0),
    )


def read_compiled_frontmatter(path: Path) -> CompiledFrontMatter | None:
    """Load just the front-matter — cheap when only metadata is needed."""
    if not path.is_file():
        return None
    post = frontmatter.loads(_strip_marker(path.read_text(encoding="utf-8")))
    return _frontmatter_to_model(post.metadata)


_SUBTOPICS_HEADER_RE = re.compile(r"^##\s+Sub-topics\s*$", re.MULTILINE)
_CONTENT_HEADER_RE = re.compile(r"^##\s+Content\s*$", re.MULTILINE)
# `###` is the pre-roll-up heading level; both are accepted so older KBs parse.
_ENTRY_HEADER_RE = re.compile(r"^#{3,4}\s+`([^`]+)`\s*$", re.MULTILINE)
_FIELD_RE = re.compile(
    r"^-\s*\*\*(?P<key>[^*]+)\*\*\s*:\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)


def extract_subtopics_section(body: str) -> str:
    """Return just the raw text of the ``## Sub-topics`` section, or ``""``."""
    m = _SUBTOPICS_HEADER_RE.search(body)
    if not m:
        return ""
    start = m.end()
    end_match = _CONTENT_HEADER_RE.search(body, start)
    end = end_match.start() if end_match else len(body)
    return body[start:end].strip()


def strip_subtopics_section(body: str) -> str:
    """Return ``body`` with its ``## Sub-topics`` section removed (§2.6).

    Used when serving a non-root packet: its subtree index is a verbatim
    subset of the root catalog already sitting in the agent's system prompt,
    so re-shipping it would duplicate that text inside the active set.
    """
    m = _SUBTOPICS_HEADER_RE.search(body)
    if not m:
        return body
    end_match = _CONTENT_HEADER_RE.search(body, m.end())
    end = end_match.start() if end_match else len(body)
    return (body[: m.start()].rstrip() + "\n\n" + body[end:].lstrip()).strip() + "\n"


def _clean_field(value: str) -> str:
    return value.strip().strip("`").strip()


def parse_subtopics(body: str) -> list[CatalogRecord]:
    """Parse the ``## Sub-topics`` section of a compiled.md body into records.

    Records written before the subtree roll-up carry no ``depth``/``parent``/
    ``kind``; they default to ``1`` / ``""`` / ``leaf``, which is exactly what
    a one-level child listing meant.
    """
    section = extract_subtopics_section(body)
    if not section:
        return []
    records: list[CatalogRecord] = []
    matches = list(_ENTRY_HEADER_RE.finditer(section))
    for i, m in enumerate(matches):
        rid = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        block = section[start:end]
        fields: dict[str, str] = {}
        for fmatch in _FIELD_RE.finditer(block):
            fields[fmatch.group("key").strip().lower()] = fmatch.group("value").strip()
        try:
            tokens = int(fields.get("tokens", "0") or "0")
        except ValueError:
            tokens = 0
        try:
            depth = int(fields.get("depth", "1") or "1")
        except ValueError:
            depth = 1
        kind = _clean_field(fields.get("kind", "leaf"))
        if kind not in ("leaf", "node", "mixed"):
            kind = "leaf"
        parent = _clean_field(fields.get("parent", ""))
        records.append(
            CatalogRecord(
                id=rid,
                path=fields.get("path", "").strip("`/ ").rstrip("/"),
                title=fields.get("title", rid),
                short=fields.get("short", ""),
                long=fields.get("long", ""),
                tokens=tokens,
                depth=depth,
                parent=parent,
                kind=kind,  # type: ignore[arg-type]
            )
        )
    return records


def parse_compiled(raw: str) -> tuple[CompiledFrontMatter, list[CatalogRecord], str]:
    """Parse compiled.md *text* into front-matter + subtree index + body.

    The string-level entry point, for callers (the memory module) that get
    bytes from a ``KBStorage`` rather than a path.
    """
    post = frontmatter.loads(_strip_marker(raw))
    return _frontmatter_to_model(post.metadata), parse_subtopics(post.content), post.content


def read_compiled(path: Path) -> tuple[CompiledFrontMatter, list[CatalogRecord], str] | None:
    """Load front-matter + parsed subtree index + raw body text (marker stripped).

    Returns ``None`` if ``path`` doesn't exist.
    """
    if not path.is_file():
        return None
    return parse_compiled(path.read_text(encoding="utf-8"))


__all__ = [
    "HCAG_COMPILED_MARKER",
    "ROOT_DISPLAY_ID",
    "CatalogRecord",
    "ChildEntry",
    "CompiledFrontMatter",
    "FolderKind",
    "extract_subtopics_section",
    "is_hcag_generated",
    "parse_compiled",
    "parse_subtopics",
    "read_compiled",
    "read_compiled_frontmatter",
    "render_subtopics_section",
    "strip_compiled_frontmatter",
    "strip_subtopics_section",
    "write_compiled_md",
]
