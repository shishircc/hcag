"""Trace destination resolution — generic OTLP and direct Langfuse (§2.11.1)."""

from __future__ import annotations

import base64
import tomllib

import pytest

from hcag.config import (
    AgentConfig,
    LangfuseConfig,
    ObservabilityConfig,
    OTELConfig,
)
from hcag.tracing import LANGFUSE_OTLP_PATH, build_tracer, resolve_destination


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)


def _keys(monkeypatch, pub="pk-lf-1", sec="sk-lf-2"):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", pub)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", sec)


# --- Activation ------------------------------------------------------------


def test_no_destination_configured_is_the_default() -> None:
    """Nothing leaves the process unless asked for."""
    assert resolve_destination(ObservabilityConfig()) is None


def test_generic_otel_endpoint_unchanged() -> None:
    obs = ObservabilityConfig(
        otel=OTELConfig(
            endpoint="http://localhost:4318",
            protocol="grpc",
            headers={"x-api-key": "abc"},
            service_name="svc",
        )
    )
    dest = resolve_destination(obs)
    assert dest is not None
    assert dest.source == "otel"
    assert dest.endpoint == "http://localhost:4318"
    assert dest.protocol == "grpc"
    assert dest.headers == {"x-api-key": "abc"}
    assert dest.service_name == "svc"


def test_langfuse_derives_endpoint_protocol_and_auth(monkeypatch) -> None:
    _keys(monkeypatch)
    obs = ObservabilityConfig(langfuse=LangfuseConfig())
    dest = resolve_destination(obs)

    assert dest is not None
    assert dest.source == "langfuse"
    assert dest.endpoint == "https://cloud.langfuse.com" + LANGFUSE_OTLP_PATH
    # Langfuse's OTLP ingest is HTTP; otel.protocol is not consulted.
    assert dest.protocol == "http/protobuf"
    token = base64.b64encode(b"pk-lf-1:sk-lf-2").decode()
    assert dest.headers == {"Authorization": f"Basic {token}"}


def test_langfuse_protocol_is_pinned_even_if_otel_protocol_says_grpc(monkeypatch) -> None:
    _keys(monkeypatch)
    obs = ObservabilityConfig(
        otel=OTELConfig(protocol="grpc"), langfuse=LangfuseConfig()
    )
    assert resolve_destination(obs).protocol == "http/protobuf"


def test_self_hosted_host_and_trailing_slash(monkeypatch) -> None:
    _keys(monkeypatch)
    obs = ObservabilityConfig(langfuse=LangfuseConfig(host="https://lf.internal:3000/"))
    assert resolve_destination(obs).endpoint == "https://lf.internal:3000" + LANGFUSE_OTLP_PATH


def test_custom_key_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("MY_PUB", "p")
    monkeypatch.setenv("MY_SEC", "s")
    obs = ObservabilityConfig(
        langfuse=LangfuseConfig(public_key_env="MY_PUB", secret_key_env="MY_SEC")
    )
    token = base64.b64encode(b"p:s").decode()
    assert resolve_destination(obs).headers["Authorization"] == f"Basic {token}"


# --- Fail closed -----------------------------------------------------------


def test_configuring_both_destinations_is_a_startup_error() -> None:
    """Silently preferring one would send traces where the operator did not intend."""
    with pytest.raises(ValueError, match="Pick one"):
        ObservabilityConfig(
            otel=OTELConfig(endpoint="http://localhost:4318"),
            langfuse=LangfuseConfig(),
        )


def test_both_destinations_rejected_when_loading_a_config_file(tmp_path) -> None:
    """The error surfaces at config-load time, i.e. at startup."""
    raw = tomllib.loads(
        'kb_root = "./kb"\n'
        "[observability.otel]\n"
        'endpoint = "http://localhost:4318"\n'
        "[observability.langfuse]\n"
        'host = "https://cloud.langfuse.com"\n'
    )
    with pytest.raises(ValueError, match="Pick one"):
        AgentConfig.model_validate(raw)


@pytest.mark.parametrize(
    "present,missing",
    [(None, "LANGFUSE_PUBLIC_KEY"), ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")],
)
def test_missing_langfuse_key_is_an_error_not_a_silent_no_op(
    monkeypatch, present, missing
) -> None:
    if present:
        monkeypatch.setenv(present, "x")
    obs = ObservabilityConfig(langfuse=LangfuseConfig())
    with pytest.raises(ValueError) as exc:
        resolve_destination(obs)
    assert missing in str(exc.value)
    assert "read from the environment" in str(exc.value)


def test_blank_key_counts_as_missing(monkeypatch) -> None:
    _keys(monkeypatch, pub="   ", sec="sk")
    with pytest.raises(ValueError, match="LANGFUSE_PUBLIC_KEY"):
        resolve_destination(ObservabilityConfig(langfuse=LangfuseConfig()))


def test_inline_keys_are_rejected_so_a_secret_cannot_be_committed() -> None:
    with pytest.raises(ValueError):
        LangfuseConfig(public_key="pk-should-not-be-here")
    with pytest.raises(ValueError):
        LangfuseConfig(secret_key="sk-should-not-be-here")


# --- Wiring ----------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event, **fields):
        self.events.append((event, fields))

    def warn(self, event, **fields):
        self.events.append((event, fields))


def test_build_tracer_is_a_noop_without_a_destination() -> None:
    log = _Recorder()
    tracer = build_tracer(ObservabilityConfig(), logger=log)
    with tracer.start_as_current_span("x") as span:
        span.set_attribute("k", "v")  # must not raise
    assert log.events[0][0] == "tracing.disabled"


def test_build_tracer_logs_the_destination_without_the_auth_token(monkeypatch) -> None:
    _keys(monkeypatch)
    log = _Recorder()
    build_tracer(ObservabilityConfig(langfuse=LangfuseConfig()), logger=log)

    event, fields = log.events[0]
    assert event == "tracing.enabled"
    assert fields["source"] == "langfuse"
    assert fields["endpoint"].endswith(LANGFUSE_OTLP_PATH)
    # The Basic token must never reach the log.
    blob = repr(log.events)
    assert "Authorization" not in blob
    assert "sk-lf-2" not in blob


def test_build_tracer_still_accepts_a_bare_otel_config() -> None:
    """Callers holding only the OTEL block keep working."""
    assert build_tracer(OTELConfig()) is not None
    assert resolve_destination(ObservabilityConfig(otel=OTELConfig())) is None


def test_agent_runtime_surfaces_a_missing_key_at_construction(tmp_path) -> None:
    """A configured-but-broken destination stops startup (§2.11.1), it does not
    quietly downgrade to no-op tracing."""
    from hcag.runtime.agent import AgentRuntime

    (tmp_path / "compiled.md").write_text(
        "<!-- HCAG:COMPILED id=_root -->\n---\nid: ''\ntitle: R\n"
        "short_description: r\nlong_description: r\ntoken_size_estimate: 1\n"
        "kind: node\nsource_files: []\nchildren: []\n---\n\n# R\n",
        encoding="utf-8",
    )
    cfg = AgentConfig(kb_root=str(tmp_path))
    cfg.observability.log.file_path = str(tmp_path / "a.log")
    cfg.observability.langfuse = LangfuseConfig()

    with pytest.raises(ValueError, match="LANGFUSE_PUBLIC_KEY"):
        AgentRuntime(cfg=cfg, llm=object())
