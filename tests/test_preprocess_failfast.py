"""`hcag preprocess` fails closed on LLM trouble (§3.4.9)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hcag.cli import preprocess as pp
from hcag.cli.metadata_llm import (
    FolderMetadata,
    LLMUnavailableError,
    check_credentials,
    classify,
)
from hcag.config import CliConfig, LLMConfig
from hcag.logger import build_logger


def _ok(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
    first = (own_content.splitlines() or [""])[0].lstrip("# ").strip() or "Node"
    return FolderMetadata(
        title=first, short_description=f"s-{first}", long_description=f"l-{first}"
    )


def _kb(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    for branch in ("alpha", "beta"):
        (root / branch).mkdir(parents=True)
        (root / branch / "x.md").write_text(f"# {branch}\nbody\n", encoding="utf-8")
    return root


def _cfg(tmp_path: Path) -> CliConfig:
    cfg = CliConfig()
    cfg.tokenizer.kind = "rough"
    cfg.llm.max_retries = 2
    cfg.log.file_path = str(tmp_path / "build.log")
    return cfg


def _logger(tmp_path: Path):
    return build_logger(_cfg(tmp_path).log, name="test.failfast")


@pytest.fixture(autouse=True)
def _no_backoff_sleeps():
    """Retries are exercised for real; only the waiting is stubbed out."""
    with patch.object(pp, "_sleep", lambda attempt: None):
        yield


# --- Failure classification ------------------------------------------------


def test_classification_of_provider_errors() -> None:
    """Matched on class name so LiteLLM need not be imported (or installed)."""
    assert classify(type("RateLimitError", (Exception,), {})()) == "retryable"
    assert classify(type("APIConnectionError", (Exception,), {})()) == "retryable"
    assert classify(type("AuthenticationError", (Exception,), {})()) == "unavailable"
    assert classify(type("NotFoundError", (Exception,), {})()) == "unavailable"
    assert classify(LLMUnavailableError("x")) == "unavailable"
    # Anything unrecognized — an unparseable reply, say — is folder-specific.
    assert classify(ValueError("bad json")) == "item"


def test_missing_api_key_is_caught_before_any_network_call(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailableError, match="ANTHROPIC_API_KEY"):
        check_credentials(LLMConfig(provider="anthropic"))


def test_credentials_check_skipped_for_providers_without_a_key_env() -> None:
    """Bedrock uses the AWS chain and local servers need no key."""
    check_credentials(LLMConfig(provider="bedrock", model="bedrock/x"))
    check_credentials(LLMConfig(provider="ollama", model="ollama/llama3"))


# --- Preflight -------------------------------------------------------------


def test_preflight_failure_writes_nothing(tmp_path: Path) -> None:
    root = _kb(tmp_path)
    boom = type("AuthenticationError", (Exception,), {})("bad key")

    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=boom):
        with pytest.raises(pp.PreprocessAborted) as exc:
            pp.preprocess_tree(root, _cfg(tmp_path), _logger(tmp_path), force=True)

    assert exc.value.folders_written == 0
    assert "preflight" in str(exc.value).lower()
    # The KB is exactly as it was found.
    assert list(root.rglob("compiled.md")) == []


def test_preflight_runs_before_the_walk(tmp_path: Path) -> None:
    """The probe is the first call, and it is a real summarizer request."""
    seen: list[str] = []

    def _record(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        seen.append(own_content)
        return _ok(cfg, own_content=own_content)

    root = _kb(tmp_path)
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_record):
        pp.preprocess_tree(root, _cfg(tmp_path), _logger(tmp_path), force=True)

    assert "Preflight" in seen[0]
    assert len(seen) == 4  # probe + alpha + beta + root


def test_preflight_rejects_a_model_that_cannot_produce_the_contract(tmp_path: Path) -> None:
    """An unparseable reply is worth learning on call one, not call one hundred."""
    root = _kb(tmp_path)
    with patch(
        "hcag.cli.preprocess.generate_folder_metadata",
        side_effect=ValueError("Expecting value: line 1 column 1"),
    ):
        with pytest.raises(pp.PreprocessAborted, match="preflight"):
            pp.preprocess_tree(root, _cfg(tmp_path), _logger(tmp_path), force=True)
    assert list(root.rglob("compiled.md")) == []


def test_preflight_can_be_disabled(tmp_path: Path) -> None:
    root = _kb(tmp_path)
    cfg = _cfg(tmp_path)
    cfg.llm.preflight = False
    calls: list[str] = []

    def _record(cfg_, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        calls.append(own_content)
        return _ok(cfg_, own_content=own_content)

    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_record):
        pp.preprocess_tree(root, cfg, _logger(tmp_path), force=True)

    assert len(calls) == 3  # no probe — alpha + beta + root only
    assert (root / "compiled.md").is_file()


# --- Mid-run policy --------------------------------------------------------


def test_transient_failures_are_retried_then_succeed(tmp_path: Path) -> None:
    flaky = type("RateLimitError", (Exception,), {})
    attempts: list[int] = []

    def _twice_then_ok(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        attempts.append(1)
        if len(attempts) <= 2:
            raise flaky("slow down")
        return _ok(cfg, own_content=own_content)

    root = _kb(tmp_path)
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_twice_then_ok):
        pp.preprocess_tree(root, _cfg(tmp_path), _logger(tmp_path), force=True)

    # max_retries=2 means 3 attempts; the probe burned all of them and passed.
    assert (root / "compiled.md").is_file()


def test_unavailable_mid_run_aborts_and_reports_progress(tmp_path: Path) -> None:
    """Systemic failure after the probe: stop rather than fill the tree with
    placeholders. --allow-partial does not cover this."""
    auth = type("AuthenticationError", (Exception,), {})
    calls: list[int] = []

    def _die_after_two(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        calls.append(1)
        if len(calls) > 2:  # probe + alpha succeed, then the key is revoked
            raise auth("credentials revoked")
        return _ok(cfg, own_content=own_content)

    root = _kb(tmp_path)
    for allow_partial in (False, True):
        calls.clear()
        for stale in root.rglob("compiled.md"):
            stale.unlink()
        with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_die_after_two):
            with pytest.raises(pp.PreprocessAborted) as exc:
                pp.preprocess_tree(
                    root, _cfg(tmp_path), _logger(tmp_path),
                    force=True, allow_partial=allow_partial,
                )
        assert exc.value.folders_written == 1, allow_partial
        # The completed subtree is on disk; the root was never written.
        assert (root / "alpha" / "compiled.md").is_file()
        assert not (root / "compiled.md").is_file()


def test_per_folder_failure_aborts_by_default(tmp_path: Path) -> None:
    """No placeholder: it would silently degrade every ancestor's summary."""
    calls: list[int] = []

    def _bad_reply_for_beta(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        calls.append(1)
        if own_content.startswith("# beta"):
            raise ValueError("unparseable reply")
        return _ok(cfg, own_content=own_content)

    root = _kb(tmp_path)
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_bad_reply_for_beta):
        with pytest.raises(pp.PreprocessAborted) as exc:
            pp.preprocess_tree(root, _cfg(tmp_path), _logger(tmp_path), force=True)

    assert "beta" in str(exc.value)
    assert "--allow-partial" in str(exc.value)
    assert not (root / "compiled.md").is_file()
    # Retried before giving up: 1 probe + 1 alpha + 3 beta attempts.
    assert len(calls) == 5


def test_allow_partial_degrades_instead_of_aborting(tmp_path: Path) -> None:
    def _bad_reply_for_beta(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        if own_content.startswith("# beta"):
            raise ValueError("unparseable reply")
        return _ok(cfg, own_content=own_content)

    root = _kb(tmp_path)
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_bad_reply_for_beta):
        pp.preprocess_tree(
            root, _cfg(tmp_path), _logger(tmp_path), force=True, allow_partial=True
        )

    from hcag.compiled_io import read_compiled

    assert (root / "compiled.md").is_file()
    fm, records, _ = read_compiled(root / "beta" / "compiled.md")
    assert "summary unavailable" in fm.short_description
    # The whole tree still renders, and beta stays reachable from the root.
    assert {r.id for r in read_compiled(root / "compiled.md")[1]} == {"alpha", "beta"}


def test_rerun_resumes_after_an_abort(tmp_path: Path) -> None:
    """DFS post-order leaves completed subtrees valid; the default
    skip-existing policy resumes without re-spending them (§3.4.9)."""
    auth = type("AuthenticationError", (Exception,), {})
    calls: list[str] = []

    def _die_after_two(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        calls.append(own_content)
        if len(calls) > 2:
            raise auth("credentials revoked")
        return _ok(cfg, own_content=own_content)

    root = _kb(tmp_path)
    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_die_after_two):
        with pytest.raises(pp.PreprocessAborted):
            pp.preprocess_tree(root, _cfg(tmp_path), _logger(tmp_path), force=True)
    assert (root / "alpha" / "compiled.md").is_file()

    # Provider recovers. Re-run WITHOUT --force: alpha is skipped, not re-billed.
    resumed: list[str] = []

    def _record(cfg, *, own_content="", children_longs=None, **kw):  # noqa: ARG001
        resumed.append(own_content)
        return _ok(cfg, own_content=own_content)

    with patch("hcag.cli.preprocess.generate_folder_metadata", side_effect=_record):
        pp.preprocess_tree(root, _cfg(tmp_path), _logger(tmp_path))

    assert not any(c.startswith("# alpha") for c in resumed), "alpha was re-summarized"
    assert any(c.startswith("# beta") for c in resumed)

    from hcag.compiled_io import read_compiled

    # The roll-up completes on the resumed pass, alpha included.
    assert {r.id for r in read_compiled(root / "compiled.md")[1]} == {"alpha", "beta"}
