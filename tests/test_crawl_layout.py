"""Output layout: a page's Markdown sits at the deepest level of its URL (§4.5)."""

from __future__ import annotations

from pathlib import Path

from hcag.crawl.urls import (
    collapse_leaf_dirs,
    find_layout_collisions,
    url_to_output_paths,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _page(kb: Path, *segments: str, images: dict[str, bytes] | None = None) -> Path:
    """Write a page the way the crawl does: index.md at its own deepest level."""
    d = kb.joinpath(*segments)
    d.mkdir(parents=True, exist_ok=True)
    body = f"# {segments[-1] if segments else 'root'}\n"
    for name in (images or {}):
        body += f"\n![alt]({name})\n"
    (d / "index.md").write_text(body, encoding="utf-8")
    for name, data in (images or {}).items():
        (d / name).write_bytes(data)
    return d / "index.md"


# --- Write phase -----------------------------------------------------------


def test_every_segment_becomes_a_directory(tmp_path: Path) -> None:
    md, base = url_to_output_paths("https://d.com/topic/subtopic", tmp_path)
    assert md == tmp_path / "d.com" / "topic" / "subtopic" / "index.md"
    assert base == "index"


def test_extension_is_stripped_from_the_directory_name(tmp_path: Path) -> None:
    md, _ = url_to_output_paths("https://d.com/a/b/c.html", tmp_path)
    assert md == tmp_path / "d.com" / "a" / "b" / "c" / "index.md"


def test_directory_index_and_extensionless_url_agree(tmp_path: Path) -> None:
    """`/a/b/` and `/a/b` are the same page and must not produce two files."""
    with_slash, _ = url_to_output_paths("https://d.com/a/b/", tmp_path)
    without, _ = url_to_output_paths("https://d.com/a/b", tmp_path)
    assert with_slash == without


# --- Collapse phase --------------------------------------------------------


def test_leaf_directory_collapses(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    _page(kb, "d.com", "topic", "leaf")

    assert collapse_leaf_dirs(kb) == 1
    assert (kb / "d.com" / "topic" / "leaf.md").is_file()
    assert not (kb / "d.com" / "topic" / "leaf").exists()


def test_branch_directory_survives(tmp_path: Path) -> None:
    """A page with children keeps its directory — that is the whole point."""
    kb = tmp_path / "kb"
    _page(kb, "d.com", "topic", "subtopic")
    for child in ("a", "b", "c"):
        _page(kb, "d.com", "topic", "subtopic", child)

    collapse_leaf_dirs(kb)

    sub = kb / "d.com" / "topic" / "subtopic"
    assert (sub / "index.md").is_file()          # the subtopic's own page…
    assert sorted(p.name for p in sub.iterdir()) == ["a.md", "b.md", "c.md", "index.md"]
    assert not (kb / "d.com" / "topic" / "subtopic.md").exists()


def test_the_invariant_holds_after_collapse(tmp_path: Path) -> None:
    """No directory may contain both `X/` and `X.md`. This is the condition
    that distinguishes this layout from the one it replaces."""
    kb = tmp_path / "kb"
    _page(kb, "d.com", "passes", "employment-pass")
    _page(kb, "d.com", "passes", "employment-pass", "eligibility")
    _page(kb, "d.com", "passes", "employment-pass", "eligibility", "compass-c1")
    _page(kb, "d.com", "passes", "employment-pass", "apply")

    collapse_leaf_dirs(kb)

    assert find_layout_collisions(kb) == []
    ep = kb / "d.com" / "passes" / "employment-pass"
    # eligibility has a child, so it keeps its folder and its own page inside it
    assert (ep / "eligibility" / "index.md").is_file()
    assert (ep / "eligibility" / "compass-c1.md").is_file()
    assert not (ep / "eligibility.md").exists()
    # apply is a leaf, so it flattens
    assert (ep / "apply.md").is_file()


def test_deepest_first_so_a_parent_is_judged_after_its_children(tmp_path: Path) -> None:
    """Collapsing a child turns it into a file *inside* the parent, which is
    what stops the parent from also looking like a leaf."""
    kb = tmp_path / "kb"
    _page(kb, "d.com", "a")
    _page(kb, "d.com", "a", "b")

    assert collapse_leaf_dirs(kb) == 1
    assert (kb / "d.com" / "a" / "index.md").is_file()
    assert (kb / "d.com" / "a" / "b.md").is_file()
    assert find_layout_collisions(kb) == []


def test_host_directory_is_never_collapsed(tmp_path: Path) -> None:
    """Domain-first (§4.5): collapsing it would put `<host>.md` at the KB root
    and merge sites that are meant to stay separate."""
    kb = tmp_path / "kb"
    _page(kb, "d.com")

    assert collapse_leaf_dirs(kb) == 0
    assert (kb / "d.com" / "index.md").is_file()
    assert not (kb / "d.com.md").exists()


def test_collapse_is_idempotent(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    _page(kb, "d.com", "topic", "leaf")
    assert collapse_leaf_dirs(kb) == 1
    assert collapse_leaf_dirs(kb) == 0


def test_empty_and_missing_roots_are_safe(tmp_path: Path) -> None:
    assert collapse_leaf_dirs(tmp_path / "nope") == 0
    assert find_layout_collisions(tmp_path / "nope") == []


# --- Images travel with the page ------------------------------------------


def test_images_are_renamed_and_references_rewritten(tmp_path: Path) -> None:
    """Images are referenced by bare filename, so renaming one without
    rewriting its reference in the Markdown leaves a broken image."""
    kb = tmp_path / "kb"
    _page(kb, "d.com", "topic", "leaf", images={"index-apple.jpg": PNG})

    collapse_leaf_dirs(kb)

    md = kb / "d.com" / "topic" / "leaf.md"
    assert (kb / "d.com" / "topic" / "leaf-apple.jpg").is_file()
    assert not (kb / "d.com" / "topic" / "leaf" ).exists()
    assert "leaf-apple.jpg" in md.read_text()
    assert "index-apple.jpg" not in md.read_text()


def test_a_directory_holding_a_foreign_file_does_not_collapse(tmp_path: Path) -> None:
    """Only the page's own assets may ride along; anything else means the
    directory holds more than one page's worth of content."""
    kb = tmp_path / "kb"
    d = _page(kb, "d.com", "topic", "leaf").parent
    (d / "something-else.md").write_text("other", encoding="utf-8")

    assert collapse_leaf_dirs(kb) == 0
    assert (d / "index.md").is_file()


def test_collapse_refuses_to_clobber_an_existing_sibling(tmp_path: Path) -> None:
    """Two URLs can sanitize to the same name; losing a page is worse than
    leaving the tree un-flattened."""
    kb = tmp_path / "kb"
    _page(kb, "d.com", "topic", "leaf")
    (kb / "d.com" / "topic" / "leaf.md").write_text("pre-existing", encoding="utf-8")

    assert collapse_leaf_dirs(kb) == 0
    assert (kb / "d.com" / "topic" / "leaf.md").read_text() == "pre-existing"
    assert (kb / "d.com" / "topic" / "leaf" / "index.md").is_file()


# --- The collision detector itself ----------------------------------------


def test_find_layout_collisions_spots_the_pair(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    d = kb / "d.com" / "topic" / "eligibility"
    d.mkdir(parents=True)
    (d / "compass.md").write_text("x", encoding="utf-8")
    (kb / "d.com" / "topic" / "eligibility.md").write_text("y", encoding="utf-8")

    assert find_layout_collisions(kb) == [d]
