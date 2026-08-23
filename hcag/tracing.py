"""OpenTelemetry setup — activated only when otel.endpoint is configured (§2.11.1)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from .config import OTELConfig


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


def build_tracer(cfg: OTELConfig, service_name: str | None = None) -> Any:
    """Return a tracer. Falls back to a no-op if OTEL SDK is not installed or endpoint is unset."""
    if not cfg.endpoint:
        return _NoopTracer()
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        if cfg.protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        resource = Resource.create({"service.name": service_name or cfg.service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=cfg.endpoint, headers=cfg.headers or None)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        return trace.get_tracer("hcag")
    except ImportError:
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
