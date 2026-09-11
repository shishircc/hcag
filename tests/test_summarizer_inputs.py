"""A folder is described from its own content and nothing else (§3.4.4, D3a).

This file replaces the bubble-up suite, which pinned the opposite: that a
parent's prompt carried its children's long descriptions. That input is what
made summarization iterated — a compression of a compression, generic by the
time it reached the root — and what let a folder with no text of its own be
described at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from hcag.cli import metadata_llm
from hcag.cli.metadata_llm import (
    FolderMetadata,
    MetadataGenerationError,
    generate_folder_metadata,
)
from hcag.cli.preprocess import preprocess_tree
from hcag.compiled_io import read_compiled, read_compiled_frontmatter
from hcag.config import CliConfig, LLMConfig
from hcag.logger import build_logger


_REPLY = json.dumps({"title": "T", "long_description": "l"})


def _prompt_for(**kwargs) -> str:
    """Run generate_folder_metadata and return the prompt it actually sent."""
    seen: list[str] = []

    def _fake_complete(cfg, prompt):  # noqa: ARG001
        seen.append(prompt)
        return _REPLY

    with patch.object(metadata_llm, "_complete", side_effect=_fake_complete):
        generate_folder_metadata(LLMConfig(), **kwargs)
    return seen[0]


# --- The input rule ---------------------------------------------------------


def test_the_signature_cannot_express_a_child() -> None:
    """The cheapest place to enforce the rule is a parameter list that has
    nowhere to put the thing that must not be passed."""
    with pytest.raises(TypeError):
        generate_folder_metadata(
            LLMConfig(),
            own_content="x",
            children_longs=[("auth.sso", "SAML assertion mapping")],  # type: ignore[call-arg]
        )


def test_the_prompt_carries_the_folders_own_content() -> None:
    prompt = _prompt_for(own_content="# Billing\nInvoice-to-cash sequence.")
    assert "Invoice-to-cash sequence." in prompt
    assert "OWN CONTENT" in prompt


def test_the_prompt_has_no_section_for_anything_else() -> None:
    prompt = _prompt_for(own_content="# Billing\nInvoice-to-cash sequence.")
    assert "CHILD TOPICS" not in prompt
    # And it says so to the model, not just by omission.
    assert "own content says" in prompt


def test_a_folder_with_no_content_is_not_summarized() -> None:
    """A waypoint has nothing to describe. Asking anyway is where the invented
    prose came from."""
    with pytest.raises(MetadataGenerationError, match="not summarized"):
        generate_folder_metadata(LLMConfig(), own_content="   \n\n  ")


def test_the_reply_shape_is_title_and_long_only() -> None:
    with patch.object(metadata_llm, "_complete", return_value=_REPLY):
        meta = generate_folder_metadata(LLMConfig(), own_content="text")
    assert meta == FolderMetadata(title="T", long_description="l")
    assert not hasattr(meta, "short_description")


# --- End to end through the build -------------------------------------------


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    (root / "auth" / "sso").mkdir(parents=True)
    (root / "auth" / "sso" / "saml.md").write_text(
        "# SAML\nAssertion attribute mapping and certificate rotation.\n", encoding="utf-8"
    )
    return root


def test_a_parents_prompt_never_contains_a_childs_words(tmp_path: Path) -> None:
    """The failure this removed: a parent whose description absorbed a child's
    particulars captured questions only the child could answer."""
    root = _tree(tmp_path)
    (root / "auth" / "overview.md").write_text(
        "# Authentication\nHow sign-in is configured for the tenant.\n", encoding="utf-8"
    )

    prompts: list[str] = []

    def capture(cfg, *, own_content="", **kw):  # noqa: ARG001
        prompts.append(own_content)
        return FolderMetadata(title="T", long_description="l")

    cfg = CliConfig()
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(root / "build.log")
    logger = build_logger(cfg.log, name=f"test.summarizer.{tmp_path.name}")
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=capture):
        preprocess_tree(root, cfg, logger, force=True)

    parent = next(p for p in prompts if "sign-in is configured" in p)
    assert "certificate rotation" not in parent.lower()
    assert "assertion" not in parent.lower()


def test_a_waypoint_gets_a_derived_title_and_no_description(tmp_path: Path) -> None:
    root = _tree(tmp_path)

    cfg = CliConfig()
    cfg.tokenizer.kind = "rough"
    cfg.log.file_path = str(root / "build.log")
    logger = build_logger(cfg.log, name=f"test.summarizer.waypoint.{tmp_path.name}")
    with patch(
        "hcag.cli.preprocess.generate_folder_metadata",
        side_effect=lambda cfg_, **kw: FolderMetadata(title="T", long_description="l"),
    ):
        preprocess_tree(root, cfg, logger, force=True)

    fm = read_compiled_frontmatter(root / "auth" / "compiled.md")
    assert fm is not None
    assert fm.long_description == ""
    assert fm.title == "Auth"  # the folder's own name, tidied — it claims nothing

    parsed = read_compiled(root / "compiled.md")
    assert parsed is not None
    assert [r.id for r in parsed[1]] == ["auth.sso"]
