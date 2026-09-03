"""A broken trace destination must never take the agent down (§2.11.1).

Tracing is auxiliary. An agent that refuses to answer questions because it
cannot report on itself trades a large outage for a small one. The rule is
loud, not silent, and not fatal — so these tests assert both halves: a no-op
tracer is returned AND the operator is told on stderr and in the log.
"""

from __future__ import annotations

import io
import contextlib

import pytest

from hcag.config import LangfuseConfig, ObservabilityConfig, OTELConfig
from hcag.tracing import build_tracer, resolve_destination


class _RecordingLogger:
    def __init__(self) -> None:
        self.errors: list[tuple[str, dict]] = []

    def error(self, event: str, **kw) -> None:
        self.errors.append((event, kw))

    def info(self, event: str, **kw) -> None: ...
    def warn(self, event: str, **kw) -> None: ...


def _build(obs, logger=None):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        tracer = build_tracer(obs, logger=logger)
    return tracer, err.getvalue()


def _langfuse_obs() -> ObservabilityConfig:
    return ObservabilityConfig(
        langfuse=LangfuseConfig(
            host="http://localhost:3000",
            public_key_env="HCAG_TEST_LF_PUBLIC",
            secret_key_env="HCAG_TEST_LF_SECRET",
        )
    )


def test_missing_langfuse_keys_do_not_stop_the_agent(monkeypatch) -> None:
    monkeypatch.delenv("HCAG_TEST_LF_PUBLIC", raising=False)
    monkeypatch.delenv("HCAG_TEST_LF_SECRET", raising=False)
    logger = _RecordingLogger()

    tracer, stderr = _build(_langfuse_obs(), logger)

    # It still returns a usable tracer, and spans on it are inert.
    with tracer.start_as_current_span("turn"):
        pass

    assert "tracing disabled" in stderr
    assert [e for e, _ in logger.errors] == ["tracing.disabled"]
    assert "HCAG_TEST_LF_PUBLIC" in logger.errors[0][1]["reason"]


def test_the_underlying_check_still_rejects_missing_keys(monkeypatch) -> None:
    """Degrading is `build_tracer`'s policy, not a weakening of the check."""
    monkeypatch.delenv("HCAG_TEST_LF_PUBLIC", raising=False)
    monkeypatch.delenv("HCAG_TEST_LF_SECRET", raising=False)
    with pytest.raises(ValueError, match="unset or empty"):
        resolve_destination(_langfuse_obs())


def test_no_destination_configured_is_silent() -> None:
    """Not configuring tracing is a choice, not a misconfiguration."""
    tracer, stderr = _build(ObservabilityConfig())
    with tracer.start_as_current_span("turn"):
        pass
    assert stderr == ""


def test_the_sample_agent_config_starts_without_langfuse(monkeypatch) -> None:
    """`examples/agent.toml` must run from scratch with no trace backend."""
    from pathlib import Path
    from hcag.config import load_agent_config

    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    cfg = load_agent_config(Path("examples/agent.toml"))

    assert cfg.observability.langfuse is None, (
        "the sample must not opt a new user into a backend they have not set up"
    )
    _, stderr = _build(cfg.observability)
    assert stderr == ""


def test_partial_langfuse_keys_name_only_the_missing_one(monkeypatch) -> None:
    monkeypatch.setenv("HCAG_TEST_LF_PUBLIC", "pk-lf-set")
    monkeypatch.delenv("HCAG_TEST_LF_SECRET", raising=False)
    logger = _RecordingLogger()
    _build(_langfuse_obs(), logger)
    reason = logger.errors[0][1]["reason"]
    assert "HCAG_TEST_LF_SECRET" in reason and "HCAG_TEST_LF_PUBLIC" not in reason


def test_both_forms_configured_is_still_a_startup_error() -> None:
    """Ambiguity about where traces go stays fatal — it is a config bug, not an outage."""
    with pytest.raises(Exception):
        ObservabilityConfig(
            langfuse=LangfuseConfig(host="http://localhost:3000"),
            otel=OTELConfig(endpoint="http://localhost:4318"),
        )
