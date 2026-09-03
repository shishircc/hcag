"""Packet source order: index.md first, then the site's own sequence (§3.4.3)."""

from __future__ import annotations

import json
from pathlib import Path

from hcag.cli.preprocess import order_sources
from hcag.crawl.urls import SIDECAR_NAME, read_link_order, write_sidecar

HOST = "https://d.com/topic"


def _folder(tmp_path: Path, *names: str, index: str | None = None) -> Path:
    d = tmp_path / "topic"
    d.mkdir(exist_ok=True)
    for n in names:
        (d / f"{n}.md").write_text(f"# {n}\n", encoding="utf-8")
    if index is not None:
        (d / "index.md").write_text(index, encoding="utf-8")
    return d


def _sources(folder: Path) -> list[Path]:
    """Unordered, as a filesystem scan would hand them over."""
    return sorted(folder.glob("*.md"), reverse=True)


def _names(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


# --- index.md leads --------------------------------------------------------


def test_index_leads_even_when_it_sorts_last(tmp_path: Path) -> None:
    """`index.md` does not sort first among arbitrary slugs — that is the whole
    reason lexicographic order was wrong."""
    d = _folder(tmp_path, "apply", "cancel", "zebra", index="# Topic\n")
    assert _names(order_sources(d, _sources(d)))[0] == "index.md"


def test_without_an_index_order_is_alphabetical(tmp_path: Path) -> None:
    d = _folder(tmp_path, "cancel", "apply", "zebra")
    assert _names(order_sources(d, _sources(d))) == ["apply.md", "cancel.md", "zebra.md"]


# --- order from the sidecar (primary source) -------------------------------


def test_sidecar_drives_the_order(tmp_path: Path) -> None:
    """The sidecar carries full-DOM link order, so it works on a hub page whose
    link list extraction removed — the case index.md alone cannot cover."""
    d = _folder(tmp_path, "apply", "cancel", "key-facts", index="# Topic\n\nNo links here.\n")
    write_sidecar(d, f"{HOST}", [f"{HOST}/key-facts", f"{HOST}/apply", f"{HOST}/cancel"])

    assert _names(order_sources(d, _sources(d))) == [
        "index.md", "key-facts.md", "apply.md", "cancel.md",
    ]


def test_sidecar_wins_over_the_indexs_own_links(tmp_path: Path) -> None:
    d = _folder(
        tmp_path, "apply", "cancel",
        index=f"# T\n[c]({HOST}/cancel) [a]({HOST}/apply)\n",
    )
    write_sidecar(d, HOST, [f"{HOST}/apply", f"{HOST}/cancel"])
    assert _names(order_sources(d, _sources(d))) == ["index.md", "apply.md", "cancel.md"]


def test_unmentioned_files_follow_alphabetically(tmp_path: Path) -> None:
    """A page the index never links to still belongs to the packet."""
    d = _folder(tmp_path, "apply", "cancel", "orphan-b", "orphan-a", index="# T\n")
    write_sidecar(d, HOST, [f"{HOST}/cancel", f"{HOST}/apply"])

    assert _names(order_sources(d, _sources(d))) == [
        "index.md", "cancel.md", "apply.md", "orphan-a.md", "orphan-b.md",
    ]


def test_sidecar_entry_for_a_missing_file_is_skipped(tmp_path: Path) -> None:
    """An edited tree must not break a build or cite a source that is gone."""
    d = _folder(tmp_path, "apply", index="# T\n")
    (d / SIDECAR_NAME).write_text(
        json.dumps({"source_url": HOST, "link_order": ["deleted", "apply"]}), encoding="utf-8"
    )
    assert _names(order_sources(d, _sources(d))) == ["index.md", "apply.md"]


def test_malformed_sidecar_degrades_to_alphabetical(tmp_path: Path) -> None:
    d = _folder(tmp_path, "b", "a", index="# T\n")
    (d / SIDECAR_NAME).write_text("{not json", encoding="utf-8")
    assert read_link_order(d) == []
    assert _names(order_sources(d, _sources(d))) == ["index.md", "a.md", "b.md"]


# --- fallback: links left in index.md --------------------------------------


def test_index_links_used_when_there_is_no_sidecar(tmp_path: Path) -> None:
    """Covers hand-authored folders and crawls predating the sidecar."""
    d = _folder(
        tmp_path, "apply", "cancel", "key-facts",
        index=f"# T\n\n- [Key facts]({HOST}/key-facts)\n- [Apply]({HOST}/apply)\n",
    )
    assert _names(order_sources(d, _sources(d))) == [
        "index.md", "key-facts.md", "apply.md", "cancel.md",
    ]


def test_repeat_links_keep_first_mention(tmp_path: Path) -> None:
    d = _folder(
        tmp_path, "apply", "cancel",
        index=f"# T\n[a]({HOST}/apply) [c]({HOST}/cancel) [a again]({HOST}/apply)\n",
    )
    assert _names(order_sources(d, _sources(d))) == ["index.md", "apply.md", "cancel.md"]


def test_links_outside_the_folder_are_ignored(tmp_path: Path) -> None:
    d = _folder(
        tmp_path, "apply",
        index=f"# T\n[elsewhere](https://other.com/faq/thing) [a]({HOST}/apply)\n",
    )
    assert _names(order_sources(d, _sources(d))) == ["index.md", "apply.md"]


# --- the sidecar writer ----------------------------------------------------


def test_writer_records_only_pages_that_exist(tmp_path: Path) -> None:
    """A link that was out of scope, past the depth limit, or failed to fetch
    must not appear — the sidecar can never name a missing file."""
    d = _folder(tmp_path, "apply", index="# T\n")
    recorded = write_sidecar(d, HOST, [f"{HOST}/apply", f"{HOST}/never-fetched"])
    assert recorded == ["apply"]
    assert json.loads((d / SIDECAR_NAME).read_text())["source_url"] == HOST


def test_writer_records_child_directories_too(tmp_path: Path) -> None:
    d = _folder(tmp_path, index="# T\n")
    (d / "sub").mkdir()
    assert write_sidecar(d, HOST, [f"{HOST}/sub"]) == ["sub"]


def test_a_leaf_records_no_link_order(tmp_path: Path) -> None:
    """No index page means nothing to order — but provenance still applies."""
    d = tmp_path / "leaf"
    d.mkdir()
    assert write_sidecar(d, HOST, [f"{HOST}/x"]) == []
    assert "link_order" not in json.loads((d / SIDECAR_NAME).read_text())


def test_a_leaf_folder_still_gets_provenance(tmp_path: Path) -> None:
    """A folder of collapsed leaves has no index page and still holds files
    whose origin someone will want (§4.5.3)."""
    d = tmp_path / "leaf"
    d.mkdir()
    write_sidecar(d, documents={"a.md": f"{HOST}/a"}, images={"a-x.png": f"{HOST}/x.png"})

    data = json.loads((d / SIDECAR_NAME).read_text())
    assert data["documents"] == {"a.md": f"{HOST}/a"}
    assert data["images"] == {"a-x.png": f"{HOST}/x.png"}
    assert "link_order" not in data


def test_sidecar_is_not_treated_as_content_or_a_stray(tmp_path: Path) -> None:
    """It must not reach `## Content`, and must not WARN on every branch folder."""
    from hcag.cli.preprocess import scan_folder

    d = _folder(tmp_path, "apply", index="# T\n")
    write_sidecar(d, HOST, [f"{HOST}/apply"])

    info = scan_folder(d)
    assert sorted(p.name for p in info.source_md_files) == ["apply.md", "index.md"]
    assert info.ignored_files == []
