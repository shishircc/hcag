"""A parent summarizes from its children's long descriptions, not their shorts (§3.4.4)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from hcag.cli import metadata_llm
from hcag.cli.metadata_llm import FolderMetadata, generate_folder_metadata
from hcag.cli.preprocess import preprocess_tree
from hcag.config import CliConfig, LLMConfig
from hcag.logger import build_logger


_REPLY = json.dumps(
    {"title": "T", "short_description": "s", "long_description": "l"}
)


def _prompt_for(**kwargs) -> str:
    """Run generate_folder_metadata and return the prompt it actually sent."""
    seen: list[str] = []

    def _fake_complete(cfg, prompt):  # noqa: ARG001
        seen.append(prompt)
        return _REPLY

    with patch.object(metadata_llm, "_complete", side_effect=_fake_complete):
        generate_folder_metadata(LLMConfig(), **kwargs)
    return seen[0]


def test_child_long_descriptions_reach_the_prompt() -> None:
    prompt = _prompt_for(
        own_content="",
        children_longs=[
            ("auth.sso", "Covers SAML assertion mapping, certificate rotation, and IdP metadata exchange."),
            ("auth.mfa", "TOTP enrollment, recovery codes, and step-up challenges."),
        ],
    )
    assert "=== CHILD TOPICS ===" in prompt
    assert "SAML assertion mapping, certificate rotation" in prompt
    assert "TOTP enrollment, recovery codes" in prompt
    assert "`auth.sso`" in prompt and "`auth.mfa`" in prompt


def test_every_child_is_represented_even_when_trimmed() -> None:
    """Wide folders trim each child rather than dropping any — a dropped child
    would hide a whole branch from the summary."""
    children = [(f"b.{i}", f"Child {i} " + "x" * 5000) for i in range(12)]
    prompt = _prompt_for(own_content="", children_longs=children, max_child_chars=40)

    for i in range(12):
        assert f"`b.{i}`" in prompt, f"child {i} was dropped"
        assert f"Child {i}" in prompt
    # Each child's text is capped, so the section stays bounded.
    assert prompt.count("x" * 41) == 0


def test_children_without_a_description_are_still_listed() -> None:
    prompt = _prompt_for(own_content="", children_longs=[("a", ""), ("b", "Real text.")])
    assert "`a`" in prompt
    assert "(no description)" in prompt
    assert "Real text." in prompt


def test_leaf_prompt_carries_no_child_section() -> None:
    prompt = _prompt_for(own_content="# Refunds\nRefund states.", children_longs=[])
    assert "=== OWN CONTENT ===" in prompt
    assert "=== CHILD TOPICS ===" not in prompt


def test_preprocess_bubbles_the_long_not_the_short(tmp_path: Path) -> None:
    """End to end: the value preprocess hands a parent is the child's
    `long_description`, and the child's `short_description` never appears."""
    calls: list[dict] = []

    def _fake_metadata(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        calls.append({"own": own_content, "children": list(children_longs or [])})
        # Make short and long clearly distinguishable per folder.
        first = (own_content.splitlines() or [""])[0].lstrip("# ").strip() or "Node"
        return FolderMetadata(
            title=first,
            short_description=f"SHORT-{first}",
            long_description=f"LONG-{first}: several sentences of real substance.",
        )

    kb = tmp_path / "kb"
    (kb / "billing" / "refunds").mkdir(parents=True)
    (kb / "billing" / "refunds" / "r.md").write_text("# Refunds\nStates.\n", encoding="utf-8")

    cfg = CliConfig()
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(tmp_path / "build.log")
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_fake_metadata):
        preprocess_tree(kb, cfg, build_logger(cfg.log, name="test.bubble"), force=True)

    # The first call is the §3.4.9 preflight probe, which runs before the walk.
    probe, *walk = calls
    assert probe["children"] == []
    assert "Preflight" in probe["own"]

    # Then DFS post-order: refunds (leaf), billing (pure node), root.
    refunds, billing, root = walk
    assert refunds["children"] == []
    # `billing` has no .md of its own, so its whole summary comes from the
    # child long it was handed.
    assert billing["children"] == [
        ("billing.refunds", "LONG-Refunds: several sentences of real substance.")
    ]
    # ...and that summary, in long form, is what reaches the root.
    assert root["children"] == [
        ("billing", "LONG-Node: several sentences of real substance.")
    ]

    # The short form is never an input to a parent's summarizer.
    for call in calls:
        for _cid, text in call["children"]:
            assert not text.startswith("SHORT-"), text
