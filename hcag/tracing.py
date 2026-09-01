"""OpenTelemetry setup — activated only when a trace destination is configured.

Two configuration forms, one exporter (§2.11.1): the generic `otel.*` keys, or
the direct `[observability.langfuse]` block, which derives the endpoint,
protocol and Basic auth header from a key pair. Neither configured is the
default, and it exports nothing.
"""

from __future__ import annotations

import base64
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from .config import LangfuseConfig, ObservabilityConfig, OTELConfig

#: Langfuse's OTLP ingest path, appended to the configured host.
LANGFUSE_OTLP_PATH = "/api/public/otel"


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D401
        pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, name: str, attributes: dict[str, Any] | None = None) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


@dataclass(frozen=True)
class TraceDestination:
    """A resolved OTLP export target — whichever form configured it."""

    endpoint: str
    protocol: str
    headers: dict[str, str]
    service_name: str
    #: "otel" or "langfuse" — for the startup log line only.
    source: str


def _langfuse_destination(lf: LangfuseConfig, service_name: str) -> TraceDestination:
    """Derive the OTLP exporter settings from a Langfuse key pair (§2.11.1).

    Raises `ValueError` when a key env var is unset: observability that was
    explicitly requested but silently exports nothing is the exact failure the
    direct form exists to prevent, so it is a startup error, not a downgrade.
    """
    missing = [
        name
        for name in (lf.public_key_env, lf.secret_key_env)
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        raise ValueError(
            "observability.langfuse is configured but "
            f"{' and '.join(repr(m) for m in missing)} "
            f"{'are' if len(missing) > 1 else 'is'} unset or empty; Langfuse keys are "
            "read from the environment, not from the config file"
        )
    public = os.environ[lf.public_key_env].strip()
    secret = os.environ[lf.secret_key_env].strip()
    token = base64.b64encode(f"{public}:{secret}".encode()).decode()
    return TraceDestination(
        endpoint=lf.host.rstrip("/") + LANGFUSE_OTLP_PATH,
        # Langfuse's OTLP ingest is HTTP; otel.protocol is not consulted.
        protocol="http/protobuf",
        headers={"Authorization": f"Basic {token}"},
        service_name=service_name,
        source="langfuse",
    )


def resolve_destination(
    obs: ObservabilityConfig, service_name: str | None = None
) -> TraceDestination | None:
    """Resolve the configured trace destination, or None when there is none.

    Exactly one form may be configured; `ObservabilityConfig` rejects both at
    load time (§2.11.1), so this only has to pick.
    """
    name = service_name or obs.otel.service_name
    if obs.langfuse is not None:
        return _langfuse_destination(obs.langfuse, name)
    if obs.otel.endpoint:
        return TraceDestination(
            endpoint=obs.otel.endpoint,
            protocol=obs.otel.protocol,
            headers=dict(obs.otel.headers or {}),
            service_name=name,
            source="otel",
        )
    return None


def build_tracer(
    cfg: ObservabilityConfig | OTELConfig,
    service_name: str | None = None,
    logger: Any | None = None,
) -> Any:
    """Return a tracer, or a no-op when no destination is configured.

    Accepts an `ObservabilityConfig` (both forms) or a bare `OTELConfig` (the
    generic form only), so existing callers that hold just the OTEL block keep
    working.
    """
    obs = cfg if isinstance(cfg, ObservabilityConfig) else ObservabilityConfig(otel=cfg)
    dest = resolve_destination(obs, service_name)
    if dest is None:
        if logger is not None:
            logger.info("tracing.disabled", reason="no trace destination configured")
        return _NoopTracer()
    if logger is not None:
        # Never log the resolved headers — they carry the Basic auth token.
        logger.info(
            "tracing.enabled",
            source=dest.source,
            endpoint=dest.endpoint,
            protocol=dest.protocol,
            service_name=dest.service_name,
        )
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        if dest.protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        resource = Resource.create({"service.name": dest.service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=dest.endpoint, headers=dest.headers or None)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        return trace.get_tracer("hcag")
    except ImportError:
        # Tracing is an optional extra (§2.13.6). A missing optional dependency
        # is not a reason to refuse to answer questions.
        logging.getLogger("hcag").warning(
            "tracing requested (%s -> %s) but the OpenTelemetry SDK is not installed; "
            "spans will not be exported",
            dest.source,
            dest.endpoint,
        )
        return _NoopTracer()


def current_trace_id() -> str | None:
    """Return the current OTEL trace ID as a hex string, or None if no active span."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except ImportError:
        pass
    return None
