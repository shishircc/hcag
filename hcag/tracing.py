"""OpenTelemetry setup — activated only when a trace destination is configured.

Two configuration forms, one exporter (§2.11.1): the generic `otel.*` keys, or
the direct `[observability.langfuse]` block, which derives the endpoint,
protocol and Basic auth header from a key pair. Neither configured is the
default, and it exports nothing.
"""

from __future__ import annotations

import base64
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from .config import LangfuseConfig, ObservabilityConfig, OTELConfig

#: Langfuse's OTLP ingest base, appended to the configured host.
LANGFUSE_OTLP_PATH = "/api/public/otel"

#: OTLP/HTTP signal path for traces.
#:
#: The OTLP *HTTP* exporter uses an explicitly-passed `endpoint` VERBATIM — it
#: appends this path only when falling back to OTEL_EXPORTER_OTLP_ENDPOINT. So
#: an endpoint given as a base URL (`http://localhost:4318`, or Langfuse's
#: `/api/public/otel`, which is how every vendor documents it) POSTs to a URL
#: that does not exist and the exporter reports `404, reason: Not Found`.
#: `_http_traces_url` appends it for us. gRPC has no path semantics and is left
#: alone.
OTLP_TRACES_PATH = "/v1/traces"


def _http_traces_url(endpoint: str) -> str:
    """Return the OTLP/HTTP traces URL for `endpoint`.

    Accepts either a base URL or one that already names the traces signal, so a
    value copied from vendor docs works either way.
    """
    url = endpoint.rstrip("/")
    if url.endswith(OTLP_TRACES_PATH):
        return url
    return url + OTLP_TRACES_PATH


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

    Raises `ValueError` when a key env var is unset. `build_tracer` catches it,
    reports it loudly, and runs on without tracing: observability that was
    explicitly requested but silently exports nothing is a real failure, and it
    is reported as one — but it is a failure of an auxiliary subsystem, and
    taking a working support agent offline over a missing trace key trades a
    large outage for a small one.
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
        # Langfuse's OTLP ingest is HTTP; otel.protocol is not consulted.
        endpoint=_http_traces_url(lf.host.rstrip("/") + LANGFUSE_OTLP_PATH),
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
        # Same verbatim-endpoint trap as above: a collector configured as
        # `http://localhost:4318` needs the traces path appended too.
        endpoint = (
            obs.otel.endpoint
            if obs.otel.protocol == "grpc"
            else _http_traces_url(obs.otel.endpoint)
        )
        return TraceDestination(
            endpoint=endpoint,
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
    try:
        dest = resolve_destination(obs, service_name)
    except ValueError as e:
        # A misconfigured destination (typically an unset Langfuse key) must not
        # take down the agent. Loud, not silent, and not fatal: the operator
        # asked for traces and is not getting them, which they need to know —
        # but answering questions does not depend on being able to say so.
        _report_tracing_disabled(str(e), logger)
        return _NoopTracer()
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
        _report_tracing_disabled(
            f"tracing requested ({dest.source} -> {dest.endpoint}) but the "
            "OpenTelemetry SDK is not installed; install the `otel` extra",
            logger,
        )
        return _NoopTracer()
    except Exception as e:  # noqa: BLE001
        # Same rule for anything else the exporter raises while being built —
        # a bad endpoint, a TLS problem, an SDK version mismatch. None of it is
        # a reason to stop answering questions.
        _report_tracing_disabled(
            f"could not initialize the {dest.source} exporter for {dest.endpoint}: "
            f"{type(e).__name__}: {e}",
            logger,
        )
        return _NoopTracer()


def _report_tracing_disabled(reason: str, logger: Any | None = None) -> None:
    """Announce that tracing asked for is not happening — on every channel.

    Written to stderr as well as the log because the operator who set a trace
    destination is usually watching a terminal at that moment, and a line in a
    JSON log file they will read tomorrow is not the same as being told now.
    """
    if logger is not None:
        logger.error("tracing.disabled", reason=reason)
    print(f"WARNING: tracing disabled: {reason}", file=sys.stderr)


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


# --- Span attributes (§2.11.2) ---------------------------------------------
#
# Attribute names follow the OpenTelemetry GenAI semantic conventions, which
# Langfuse also recognizes, plus a few `langfuse.*` attributes that tell it to
# render the span as a generation with input/output rather than a bare span.
# Both sets are plain OTEL attributes: nothing here depends on Langfuse, and
# they are inert on any other OTLP backend.

#: Hard ceiling on any single attribute value, as a backstop against a
#: pathological payload. Deliberately far above the configured content caps
#: (§2.11.2) so it never becomes a second, invisible truncation: a value cut
#: here would contradict the limit the operator actually set.
_MAX_ATTR_VALUE = 1_000_000


def truncate(text: str, limit: int) -> str:
    """Cut `text` to `limit`, saying how much was dropped.

    Every truncation in a trace is marked with the original size. A silently
    shortened payload is worse than no payload: it looks complete, so a reader
    concludes the prompt did not contain something it did contain.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}…[truncated: showing {limit} of {len(text)} chars]"


def truncate_middle(text: str, limit: int) -> str:
    """Cut from the middle, keeping both ends.

    For a KB packet neither end is safely droppable: the head identifies what
    the packet is, and the specific fact being checked is as likely to sit at
    the bottom of a procedure as the top. Head-only truncation reliably hides
    the latter.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    marker_len = 60
    if limit <= marker_len * 2:
        return truncate(text, limit)
    half = (limit - marker_len) // 2
    dropped = len(text) - 2 * half
    return f"{text[:half]}\n…[{dropped} chars elided from the middle]…\n{text[-half:]}"


def set_attrs(span: Any, attrs: dict[str, Any]) -> None:
    """Set every non-None attribute on `span`, skipping empties.

    OTEL rejects None and accepts only str/bool/int/float (or sequences), so
    anything else is stringified. A span that fails to record an attribute must
    never break a turn, so errors are swallowed.
    """
    for key, value in attrs.items():
        if value is None or value == "":
            continue
        if not isinstance(value, (str, bool, int, float)):
            value = str(value)
        if isinstance(value, str):
            value = truncate(value, _MAX_ATTR_VALUE)
        try:
            span.set_attribute(key, value)
        except Exception:  # noqa: BLE001 — telemetry must not fail a turn
            pass


def _dumps(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


def json_payload(value: Any, max_chars: int) -> str:
    """Serialize `value` for a `langfuse.observation.input/output` attribute."""
    return truncate(_dumps(value), max_chars)


def messages_payload(messages: list[dict[str, Any]], max_chars: int) -> str:
    """Serialize a message list so the *end* of the conversation always survives.

    Head truncation is the wrong strategy here. An HCAG prompt opens with the
    system prompt and the full KB catalog, and the material that answers "did
    the model actually have the right information?" — the loaded packets,
    arriving as tool results — sits at the end. Cutting the serialized string
    at a character budget keeps the least informative part and drops precisely
    the part being looked for.

    Three stages, each only reached if the previous one did not fit:

    1. Emit as-is.
    2. Shed whole messages from the middle, oldest first, replaced by a marker
       naming how many went. Keeps the system prompt and the last three
       messages — a turn's tail is question, tool call, tool result, and losing
       any of them removes the thing the trace was opened to check.
    3. Budget characters from the **tail backwards**, so the newest messages
       are served in full and the oldest (typically the bulky catalog) are the
       ones that shrink. This is the stage that matters when a single message
       is itself larger than the budget: without it, that one message eats
       everything and the packets vanish.

    Every reduction is marked. A silently shortened payload looks complete, so
    a reader concludes the prompt lacked something it contained.
    """
    if not messages:
        return _dumps(messages)

    text = _dumps(messages)
    if len(text) <= max_chars:
        return text

    # Stage 2 — shed whole messages from the middle, oldest first.
    #
    # Protected: the first message (system prompt + catalog) and the last
    # THREE. Three, because a turn's tail is question -> tool call -> tool
    # result, and shedding any one of them removes the thing a reader opened
    # the trace to check: what was asked, what was loaded, or what came back.
    kept = list(messages)
    elided = 0
    while len(kept) > 4 and len(_dumps(kept)) > max_chars:
        kept.pop(1)
        elided += 1
    if elided:
        kept.insert(
            1,
            {
                "role": "system",
                "content": f"[{elided} earlier message(s) elided from this trace payload]",
            },
        )
        text = _dumps(kept)
        if len(text) <= max_chars:
            return text

    # Stage 3 — allocate what is left from the tail backwards.
    #
    # Budgeting is iterative rather than analytic: per-message JSON scaffolding
    # and truncation markers cost characters that are awkward to predict, and
    # an under-estimate would push the result over the cap and hand it to the
    # final safety truncate — which cuts from the head and would undo exactly
    # the tail-preservation this stage exists for.
    budget = max_chars
    shrunk = kept
    for _ in range(4):
        shrunk = _allocate_from_tail(kept, budget)
        rendered = _dumps(shrunk)
        if len(rendered) <= max_chars:
            return rendered
        budget -= len(rendered) - max_chars + 64

    return truncate(_dumps(shrunk), max_chars)


def _allocate_from_tail(
    messages: list[dict[str, Any]], budget: int
) -> list[dict[str, Any]]:
    """Give each message what the budget allows, newest first."""
    _MIN_USEFUL = 120  # below this a truncation marker outweighs the content
    out: list[dict[str, Any]] = []
    for message in reversed(messages):
        content = message.get("content")
        if not isinstance(content, str):
            out.append(message)
            continue
        if budget <= _MIN_USEFUL:
            out.append({**message, "content": "[elided from this trace payload]"})
            continue
        allowance = min(len(content), budget)
        out.append({**message, "content": truncate_middle(content, allowance)})
        budget -= allowance
    out.reverse()
    return out
