"""Catalog entries must route by their own content, not their children's."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from hcag.cli import metadata_llm
from hcag.config import AgentConfig, LLMConfig

_REPLY = json.dumps({"title": "T", "short_description": "s", "long_description": "l"})


def _prompt_for(**kwargs) -> str:
    seen: list[str] = []

    def _fake(cfg, prompt):  # noqa: ARG001
        seen.append(prompt)
        return _REPLY

    with patch.object(metadata_llm, "_complete", side_effect=_fake):
        metadata_llm.generate_folder_metadata(LLMConfig(), **kwargs)
    return seen[0]


CHILD = [("x.compass-c1", "Salary benchmarks by sector and age. Sectors: Insurance, Banking, ICT.")]


# --- Fix 2: summaries are scoped to the folder's own content ---------------


@pytest.mark.parametrize("kind", ["leaf", "mixed"])
def test_a_folder_with_content_is_told_to_describe_its_own(kind: str) -> None:
    """A parent that advertises its children's particulars matches questions its
    own ## Content cannot answer — and is often the stronger lexical match,
    because particulars are what queries contain."""
    prompt = _prompt_for(own_content="# E\nThreshold is $11,800.", children_longs=CHILD, kind=kind)
    assert "Describe what THIS folder's own content says" in prompt
    assert "do NOT describe their contents or borrow their" in prompt
    # Routing depends on rules being stated, so the summarizer is told to —
    # but only rules this folder DEFINES, not ones it merely cites (see
    # test_cross_references_are_excluded_from_the_summary).
    assert "DEFINES a rule" in prompt and "definition" in prompt
    assert "merely invokes, cites, or links out for" in prompt
    # Children are still supplied, as framing.
    assert "Insurance" in prompt


def test_a_waypoint_still_summarizes_across_its_children() -> None:
    """A node has nothing else to describe — the old instruction is correct there."""
    prompt = _prompt_for(own_content="", children_longs=CHILD, kind="node")
    assert "no content of its own: it is a waypoint" in prompt
    assert "summarize ACROSS them" in prompt
    assert "Describe what THIS folder's own content says" not in prompt


def test_kind_is_inferred_when_not_supplied() -> None:
    """Callers predating the `kind` argument still get the right scoping."""
    assert "waypoint" in _prompt_for(own_content="", children_longs=CHILD)
    assert "THIS folder's own content" in _prompt_for(own_content="# X\nreal text", children_longs=[])


def test_preprocess_passes_the_folder_kind_through(tmp_path) -> None:
    from hcag.cli.preprocess import preprocess_tree
    from hcag.cli.metadata_llm import FolderMetadata
    from hcag.config import CliConfig
    from hcag.logger import build_logger

    seen: list[str] = []

    def _fake(cfg, *, own_content="", children_longs=None, kind="", **kw):  # noqa: ARG001
        seen.append(kind)
        return FolderMetadata(title="T", short_description="s", long_description="l")

    kb = tmp_path / "kb"
    (kb / "topic").mkdir(parents=True)
    (kb / "topic" / "own.md").write_text("# T\nbody\n", encoding="utf-8")
    (kb / "topic" / "child").mkdir()
    (kb / "topic" / "child" / "c.md").write_text("# C\nbody\n", encoding="utf-8")

    cfg = CliConfig()
    cfg.tokenizer.kind = "rough"
    cfg.llm.preflight = False
    cfg.log.file_path = str(tmp_path / "b.log")
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_fake):
        preprocess_tree(kb, cfg, build_logger(cfg.log, name="t"), force=True)

    # leaf child, mixed parent, node root — each scoped correctly.
    assert seen == ["leaf", "mixed", "node"]


# --- Fix 1: the ranking rule no longer inverts on a `mixed` ancestor -------


def test_prompt_ranks_by_own_content_not_by_depth() -> None:
    """`mixed` holds content its children do not, so a deeper entry never
    supersedes it. The old 'prefer the most specific entry' rule was true for a
    `node` ancestor and false for a `mixed` one."""
    from hcag.prompting import load_prompts

    prompt = load_prompts().get("agent.system", catalog="")

    assert "Prefer the most specific entries" not in prompt
    assert "not by how deep they sit" in prompt
    assert "a deeper entry never supersedes it" in prompt
    # And warns about the exact trap: a narrow child that matches keywords.
    assert "names your exact keywords but is a narrow sub-document" in prompt
    # `node` is still the one kind you should skip past.
    assert "waypoint with no content of its own" in prompt


def test_cross_references_are_excluded_from_the_summary() -> None:
    """A description is read by something choosing ONE folder to open, so a topic
    it names is a promise that opening this folder answers questions about it.

    The case this comes from: `compass-c1-salary-benchmarks` is 47KB of COMPASS
    sector benchmark tables, and contains two cross-reference bullets — "candidates
    who do not meet the EP qualifying salary will not be eligible" and the $22,500
    COMPASS exemption. Both link to the *parent* `eligibility` folder, which is where
    the qualifying-salary tables actually live. The summarizer named them, accurately,
    and the resulting entry read "...with rules on EP qualifying salary and
    exemptions" — so a question about qualifying salary routed to the child that
    defers it instead of the parent that answers it.
    """
    prompt = _prompt_for(own_content="# C1\nBenchmarks.", children_longs="", kind="leaf")

    # The distinction the summarizer has to make.
    assert "ABOUT" in prompt
    assert "merely references, defers, or links elsewhere for" in prompt

    # The test it applies to decide.
    assert "find the answer here — or only a pointer somewhere else?" in prompt

    # Cross-references to a parent are called out as the costly case, because the
    # referenced topic is precisely the one that should have routed elsewhere.
    assert "parent or sibling" in prompt

    # Proportion: a one-line caveat must not read like a co-equal subject.
    assert "single sentence or bullet" in prompt

    # A reference worth keeping is phrased as direction, not possession.
    assert "pointer it is" in prompt


def test_titles_lead_with_the_searchable_subject() -> None:
    """`eligibility` holds the qualifying-salary tables but was titled "Employment
    Pass Eligibility & COMPASS Framework" — neither "salary" nor "renewal" — while
    a sibling's title said "Salary Benchmarks by Sector". Titles carry lexical
    signal, so the one that reads like the answer wins."""
    prompt = _prompt_for(own_content="# E\nThreshold is $11,800.", children_longs="", kind="leaf")
    assert "the words a reader would search for" in prompt
