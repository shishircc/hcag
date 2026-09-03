"""`evalgen` startup: config visibility and LLM preflight (§6.2.2)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from hcag.cli.metadata_llm import LLMUnavailableError
from hcag.config import LLMConfig, load_evalgen_config
from hcag.evalgen import generators as g
from hcag.logger import HcagLogger, build_logger
from hcag.config import LogConfig

GOOD = json.dumps({"title": "ok", "short_description": "ok", "long_description": "ok"})


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    """A key must be present for the tests that are about the *call*, not the
    credential check; the credential test clears it explicitly."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def _logger(tmp_path: Path) -> HcagLogger:
    return build_logger(LogConfig(file_path=str(tmp_path / "e.log")), name="test.evalgen")


# --- Preflight -------------------------------------------------------------


def test_preflight_passes_on_a_well_formed_reply(tmp_path: Path) -> None:
    with patch.object(g, "_complete", return_value=GOOD):
        g.preflight(LLMConfig(), _logger(tmp_path))  # must not raise


def test_preflight_rejects_a_model_that_cannot_produce_json(tmp_path: Path) -> None:
    """A model too small to follow the output contract, learned on call one
    rather than on question 40 of 50."""
    with patch.object(g, "_complete", return_value="Sure! Here you go:"):
        with pytest.raises(LLMUnavailableError, match="not usable"):
            g.preflight(LLMConfig(max_retries=0), _logger(tmp_path))


def test_preflight_fails_fast_on_a_systemic_error(tmp_path: Path) -> None:
    """Retrying a rejected key only delays the same outcome."""
    auth = type("AuthenticationError", (Exception,), {})
    calls: list[int] = []

    def _boom(cfg, content):  # noqa: ARG001
        calls.append(1)
        raise auth("bad key")

    with patch.object(g, "_complete", side_effect=_boom):
        with pytest.raises(LLMUnavailableError, match="bad key"):
            g.preflight(LLMConfig(max_retries=3), _logger(tmp_path))
    assert len(calls) == 1


def test_preflight_retries_a_transient_error(tmp_path: Path) -> None:
    flaky = type("RateLimitError", (Exception,), {})
    calls: list[int] = []

    def _twice_then_ok(cfg, content):  # noqa: ARG001
        calls.append(1)
        if len(calls) <= 2:
            raise flaky("slow down")
        return GOOD

    with patch.object(g, "_complete", side_effect=_twice_then_ok):
        g.preflight(LLMConfig(max_retries=2), _logger(tmp_path))
    assert len(calls) == 3


def test_missing_key_is_caught_before_any_network_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    called: list[int] = []

    with patch.object(g, "_complete", side_effect=lambda *a: called.append(1)):
        with pytest.raises(LLMUnavailableError, match="ANTHROPIC_API_KEY"):
            g.preflight(LLMConfig(provider="anthropic"), _logger(tmp_path))
    assert called == []


# --- Config visibility -----------------------------------------------------


def test_missing_config_still_loads_defaults(tmp_path: Path) -> None:
    """Runnable without a config file — the point is that it says so."""
    cfg = load_evalgen_config(tmp_path / "nope.toml")
    assert cfg.llm.model == LLMConfig().model


def test_cli_warns_when_evalgen_toml_is_absent(tmp_path: Path, monkeypatch) -> None:
    """Silently generating with the small default model produces weak
    questions and no hard-2, with nothing in the output to say why."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    from hcag.evalgen.main import _cli

    import typer

    app = typer.Typer()
    app.command()(_cli)

    with patch.object(g, "_complete", return_value=GOOD):
        result = CliRunner().invoke(
            app, [str(tmp_path), "--total", "1", "--out", str(tmp_path / "o.csv")]
        )

    assert "evalgen.toml" in result.output
    assert "claude-3-5-haiku" in result.output
    assert "hard-2 needs a multimodal model" in result.output


def test_cli_aborts_before_writing_when_preflight_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from hcag.evalgen.main import _cli

    import typer

    app = typer.Typer()
    app.command()(_cli)
    out = tmp_path / "o.csv"

    result = CliRunner().invoke(
        app, [str(tmp_path), "--total", "1", "--out", str(out)]
    )

    assert result.exit_code == 1
    assert "nothing written" in result.output
    assert not out.exists()
