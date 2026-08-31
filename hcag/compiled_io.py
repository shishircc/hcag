"""Read/write the single `compiled.md` artifact per folder (§3.7).

Every folder in a normalized KB — leaf, taxonomy node, mixed, and root alike —
has exactly one ``compiled.md``. Its front-matter carries the folder's own
summary metadata, and its body optionally contains a ``## Sub-topics`` section
(catalog of immediate children) and/or a ``## Content`` section (concatenated
source markdown for this level).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import frontmatter


HCAG_COMPILED_MARKER = "<!-- HCAG:COMPILED"

FolderKind = Literal["leaf", "node", "mixed"]


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
    children: list[str] = field(default_factory=list)      # empty for pure leaves


@dataclass
class ChildEntry:
    """One entry inside a ``## Sub-topics`` section (§2.2)."""

    id: str
    path: str
    title: str
    short: str
    long: str
    tokens: int


def is_hcag_generated(path: Path) -> bool:
    """True iff ``path`` starts with the HCAG:COMPILED marker."""
    if not path.is_file():
        return False
    with path.open("r", encoding="utf-8") as f:
        head = f.read(2048)
    return HCAG_COMPILED_MARKER in head


# --- Rendering --------------------------------------------------------------


def _render_body(
    fm: CompiledFrontMatter,
    children: list[ChildEntry],
    own_sections: list[tuple[str, str]],
) -> str:
    parts: list[str] = [f"# {fm.title}", ""]
    if fm.short_description:
        parts.append(fm.short_description.strip())
        parts.append("")

    if children:
        parts.append("## Sub-topics")
        parts.append("")
        for c in children:
            parts.append(f"### `{c.id}`")
            parts.append(f"- **path**: `{c.path}`")
            parts.append(f"- **title**: {c.title}")
            parts.append(f"- **short**: {c.short}")
            parts.append(f"- **long**: {c.long}")
            parts.append(f"- **tokens**: {c.tokens}")
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
    children: list[ChildEntry],
    own_sections: list[tuple[str, str]],
) -> None:
    """Write ``compiled.md`` with marker + front-matter + body sections."""
    marker = f"{HCAG_COMPILED_MARKER} id={fm.id or '_root'} -->"
    post = frontmatter.Post(
        content=_render_body(fm, children, own_sections),
        **{
            "id": fm.id,
            "title": fm.title,
            "short_description": fm.short_description,
            "long_description": fm.long_description,
            "token_size_estimate": fm.token_size_estimate,
            "kind": fm.kind,
            "source_files": fm.source_files,
            "children": fm.children,
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


def read_compiled_frontmatter(path: Path) -> CompiledFrontMatter | None:
    """Load just the front-matter — cheap when only metadata is needed."""
    if not path.is_file():
        return None
    text = _strip_marker(path.read_text(encoding="utf-8"))
    post = frontmatter.loads(text)
    m = post.metadata
    kind = str(m.get("kind", "leaf"))
    if kind not in ("leaf", "node", "mixed"):
        kind = "leaf"
    return CompiledFrontMatter(
        id=str(m.get("id", "")),
        title=str(m.get("title", "")),
        short_description=str(m.get("short_description", "")),
        long_description=str(m.get("long_description", "")),
        token_size_estimate=int(m.get("token_size_estimate", 0) or 0),
        kind=kind,  # type: ignore[arg-type]
        source_files=list(m.get("source_files", []) or []),
        children=list(m.get("children", []) or []),
    )


_SUBTOPICS_HEADER_RE = re.compile(r"^##\s+Sub-topics\s*$", re.MULTILINE)
_CONTENT_HEADER_RE = re.compile(r"^##\s+Content\s*$", re.MULTILINE)
_ENTRY_HEADER_RE = re.compile(r"^###\s+`([^`]+)`\s*$", re.MULTILINE)
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


def parse_subtopics(body: str) -> list[ChildEntry]:
    """Parse the ``## Sub-topics`` section of a compiled.md body into entries."""
    section = extract_subtopics_section(body)
    if not section:
        return []
    entries: list[ChildEntry] = []
    matches = list(_ENTRY_HEADER_RE.finditer(section))
    for i, m in enumerate(matches):
        cid = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        block = section[start:end]
        fields: dict[str, str] = {}
        for fm in _FIELD_RE.finditer(block):
            fields[fm.group("key").strip().lower()] = fm.group("value").strip()
        try:
            tokens = int(fields.get("tokens", "0") or "0")
        except ValueError:
            tokens = 0
        entries.append(
            ChildEntry(
                id=cid,
                path=fields.get("path", "").strip("`/ ").rstrip("/"),
                title=fields.get("title", cid),
                short=fields.get("short", ""),
                long=fields.get("long", ""),
                tokens=tokens,
            )
        )
    return entries


def read_compiled(path: Path) -> tuple[CompiledFrontMatter, list[ChildEntry], str] | None:
    """Load front-matter + parsed sub-topics + raw body text (marker stripped).

    Returns ``None`` if ``path`` doesn't exist.
    """
    if not path.is_file():
        return None
    text = _strip_marker(path.read_text(encoding="utf-8"))
    post = frontmatter.loads(text)
    m = post.metadata
    kind = str(m.get("kind", "leaf"))
    if kind not in ("leaf", "node", "mixed"):
        kind = "leaf"
    fm = CompiledFrontMatter(
        id=str(m.get("id", "")),
        title=str(m.get("title", "")),
        short_description=str(m.get("short_description", "")),
        long_description=str(m.get("long_description", "")),
        token_size_estimate=int(m.get("token_size_estimate", 0) or 0),
        kind=kind,  # type: ignore[arg-type]
        source_files=list(m.get("source_files", []) or []),
        children=list(m.get("children", []) or []),
    )
    return fm, parse_subtopics(post.content), post.content


__all__ = [
    "HCAG_COMPILED_MARKER",
    "CompiledFrontMatter",
    "ChildEntry",
    "FolderKind",
    "extract_subtopics_section",
    "is_hcag_generated",
    "parse_subtopics",
    "read_compiled",
    "read_compiled_frontmatter",
    "write_compiled_md",
]
