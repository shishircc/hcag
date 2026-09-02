"""`gen_ai.chat` and friends carry real attributes, not empty spans (§2.11.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hcag.config import AgentConfig
from hcag.runtime.agent import AgentRuntime
from hcag.runtime.llm import LLMResponse, ToolCall, _extract_usage

opentelemetry = pytest.importorskip("opentelemetry.sdk.trace")


ROOT = """<!-- HCAG:COMPILED id=_root -->
---
id: ''
title: Root
short_description: r
long_description: r
token_size_estimate: 10
kind: node
source_files: []
children: [billing]
---

# Root

## Sub-topics

#### `billing`
- **path**: `billing/`
- **depth**: 1
- **parent**: `_root`
- **kind**: leaf
- **title**: Billing
- **short**: money
- **long**: money
- **tokens**: 50
"""

LEAF = """<!-- HCAG:COMPILED id=billing -->
---
id: billing
title: Billing
short_description: money
long_description: money
token_size_estimate: 50
kind: leaf
source_files: [x.md]
children: []
---

# Billing

## Content

<!-- source: x.md -->
Refunds settle in 5 business days.
"""


class _FakeLLM:
    """Turn 1 calls the tool; turn 2 answers."""

    def __init__(self) -> None:
        self.n = 0

    def chat(self, messages, tools=None):  # noqa: ARG002
        self.n += 1
        if self.n == 1:
            return LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="check_and_load_kb",
                        arguments={
                            "context": "refund timing",
                            "requested_packet_ids": ["billing"],
                            "active_packet_ids": [],
                        },
                    )
                ],
                model="claude-3-5-haiku-20241022",
                usage={"input_tokens": 1200, "output_tokens": 40, "cache_read_input_tokens": 1000},
            )
        return LLMResponse(
            text="Refunds settle in 5 business days.",
            tool_calls=[],
            model="claude-3-5-haiku-20241022",
            usage={"input_tokens": 2100, "output_tokens": 18},
        )


@pytest.fixture
def spans(tmp_path: Path):
    """Run one turn against an in-memory exporter; yield the finished spans."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def _run(**cfg_overrides):
        (tmp_path / "compiled.md").write_text(ROOT, encoding="utf-8")
        leaf = tmp_path / "billing"
        leaf.mkdir(exist_ok=True)
        (leaf / "compiled.md").write_text(LEAF, encoding="utf-8")

        cfg = AgentConfig(kb_root=str(tmp_path))
        cfg.observability.log.file_path = str(tmp_path / "a.log")
        for k, v in cfg_overrides.items():
            setattr(cfg.observability, k, v)

        runtime = AgentRuntime(cfg=cfg, llm=_FakeLLM(), session_id="sess-42")
        runtime.tracer = provider.get_tracer("hcag")
        answer = runtime.run_turn("how long do refunds take?")
        return answer, exporter.get_finished_spans()

    return _run


def _by_name(spans, name):
    return [s for s in spans if s.name == name]


def test_generation_span_carries_model_params_and_usage(spans) -> None:
    _, finished = spans()
    chat = _by_name(finished, "gen_ai.chat")
    assert len(chat) == 2

    a = chat[0].attributes
    # Marks it as a generation rather than a bare span in Langfuse.
    assert a["langfuse.observation.type"] == "generation"
    assert a["gen_ai.system"] == "anthropic"
    assert a["gen_ai.request.model"] == "claude-3-5-haiku-20241022"
    assert a["gen_ai.response.model"] == "claude-3-5-haiku-20241022"
    assert a["gen_ai.request.max_tokens"] == 4096
    assert a["gen_ai.usage.input_tokens"] == 1200
    assert a["gen_ai.usage.output_tokens"] == 40
    assert a["gen_ai.usage.cache_read_input_tokens"] == 1000


def test_generation_span_carries_input_and_output(spans) -> None:
    _, finished = spans()
    chat = _by_name(finished, "gen_ai.chat")

    prompt = json.loads(chat[0].attributes["langfuse.observation.input"])
    assert prompt[0]["role"] == "system"
    assert prompt[-1]["content"] == "how long do refunds take?"

    # First call emits a tool call; second returns the answer.
    first_out = json.loads(chat[0].attributes["langfuse.observation.output"])
    assert first_out["tool_calls"][0]["name"] == "check_and_load_kb"
    last_out = json.loads(chat[-1].attributes["langfuse.observation.output"])
    assert last_out["content"] == "Refunds settle in 5 business days."


def test_turn_span_is_the_root_and_groups_the_whole_turn(spans) -> None:
    answer, finished = spans()
    turn = _by_name(finished, "conversation.turn")[0]

    assert turn.parent is None
    ids = {s.context.span_id: s.name for s in finished}
    children = [s for s in finished if s.parent and ids[s.parent.span_id] == "conversation.turn"]
    assert sorted(s.name for s in children) == [
        "gen_ai.chat",
        "gen_ai.chat",
        "tool.check_and_load_kb",
    ]
    # One trace per turn, not one per LLM call.
    assert len({s.context.trace_id for s in finished}) == 1

    assert turn.attributes["langfuse.observation.input"] == '"how long do refunds take?"'
    assert json.loads(turn.attributes["langfuse.observation.output"]) == answer
    assert turn.attributes["hcag.turn.reload_calls"] == 1


def test_session_id_is_on_every_span(spans) -> None:
    """Langfuse groups traces sharing a session id into one conversation."""
    _, finished = spans()
    for span in finished:
        assert span.attributes["langfuse.session.id"] == "sess-42"


def test_tool_span_records_the_delta(spans) -> None:
    _, finished = spans()
    a = _by_name(finished, "tool.check_and_load_kb")[0].attributes
    assert a["hcag.tool.requested_ids"] == "billing"
    assert a["hcag.tool.loaded_ids"] == "billing"
    assert a["hcag.tool.active_ids_after"] == "billing"
    assert a["hcag.tool.redundant"] is False
    assert json.loads(a["langfuse.observation.output"])["loaded"] == ["billing"]


def test_capture_content_off_keeps_structure_but_drops_payloads(spans) -> None:
    """Latency and cost stay observable when content must not leave the process."""
    _, finished = spans(capture_content=False)
    for span in finished:
        assert "langfuse.observation.input" not in span.attributes
        assert "langfuse.observation.output" not in span.attributes
    a = _by_name(finished, "gen_ai.chat")[0].attributes
    assert a["gen_ai.usage.input_tokens"] == 1200
    assert a["gen_ai.request.model"] == "claude-3-5-haiku-20241022"


def test_long_payloads_are_reduced_with_a_marker(spans) -> None:
    """Every reduction is marked: a silently shortened payload looks complete,
    so a reader concludes the prompt lacked something it contained."""
    _, finished = spans(max_content_chars=400)
    payload = _by_name(finished, "gen_ai.chat")[0].attributes["langfuse.observation.input"]
    assert "elided" in payload or "truncated" in payload
    assert len(payload) < 800


def test_oversized_prompt_keeps_the_tail_not_the_head(spans) -> None:
    """The question a trace has to answer — did the model have the right
    information? — is answered by the packets at the END of the prompt. Cutting
    a character budget off the tail keeps the least informative part."""
    _, finished = spans(max_content_chars=1500)
    payload = _by_name(finished, "gen_ai.chat")[-1].attributes["langfuse.observation.input"]
    # The loaded packet and the user's question both survive the squeeze...
    assert "Refunds settle in 5 business days" in payload
    assert "how long do refunds take?" in payload
    # ...and the reduction is visible rather than silent.
    assert "elided" in payload or "truncated" in payload


def test_image_bytes_never_reach_the_trace(spans) -> None:
    """Base64 image data would be megabytes per span."""
    _, finished = spans()
    for span in finished:
        payload = span.attributes.get("langfuse.observation.input", "")
        assert "base64" not in payload


# --- usage extraction ------------------------------------------------------


def test_usage_extraction_normalizes_provider_field_names() -> None:
    class _U:
        prompt_tokens = 100
        completion_tokens = 20
        total_tokens = 120

    class _R:
        usage = _U()

    assert _extract_usage(_R()) == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }


def test_usage_extraction_reads_nested_cache_details() -> None:
    class _Details:
        cached_tokens = 90

    class _U:
        prompt_tokens = 100
        completion_tokens = 5
        prompt_tokens_details = _Details()

    class _R:
        usage = _U()

    assert _extract_usage(_R())["cache_read_input_tokens"] == 90


def test_missing_usage_reports_nothing_rather_than_zeros() -> None:
    """A trace must not claim '0 cache reads' when the truth is 'not reported'."""

    class _R:
        usage = None

    assert _extract_usage(_R()) == {}


# --- Every tool call is traced, whatever the tool ---------------------------


class _CallsTool:
    """Calls `tool_name` once, then answers."""

    def __init__(self, tool_name: str, arguments: dict) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.n = 0

    def chat(self, messages, tools=None):  # noqa: ARG002
        self.n += 1
        if self.n == 1:
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(id="c1", name=self.tool_name, arguments=self.arguments)],
                model="m",
            )
        return LLMResponse(text="done", tool_calls=[], model="m")


@pytest.fixture
def run_with_llm(tmp_path: Path):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    def _run(llm):
        exporter = InMemorySpanExporter()
        provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        (tmp_path / "compiled.md").write_text(ROOT, encoding="utf-8")
        leaf = tmp_path / "billing"
        leaf.mkdir(exist_ok=True)
        (leaf / "compiled.md").write_text(LEAF, encoding="utf-8")

        cfg = AgentConfig(kb_root=str(tmp_path))
        cfg.observability.log.file_path = str(tmp_path / "a.log")
        runtime = AgentRuntime(cfg=cfg, llm=llm, session_id="s1")
        runtime.tracer = provider.get_tracer("hcag")
        runtime.run_turn("q")
        return exporter.get_finished_spans()

    return _run


def test_get_catalog_call_is_traced(run_with_llm) -> None:
    """Instrumenting only check_and_load_kb left a get_catalog call invisible —
    the turn looked like it did nothing between two LLM calls."""
    finished = run_with_llm(_CallsTool("get_catalog", {}))
    span = _by_name(finished, "tool.get_catalog")[0]

    assert span.attributes["gen_ai.tool.name"] == "get_catalog"
    assert span.attributes["gen_ai.operation.name"] == "execute_tool"
    # §2.12 item 6 — the injected catalog is already complete.
    assert span.attributes["hcag.tool.unnecessary"] is True
    assert span.attributes["hcag.catalog.entries"] == 1
    assert "langfuse.observation.output" in span.attributes


def test_unknown_tool_call_is_traced_and_marked_failed(run_with_llm) -> None:
    finished = run_with_llm(_CallsTool("summon_kraken", {"x": 1}))
    span = _by_name(finished, "tool.summon_kraken")[0]

    assert span.attributes["hcag.tool.unknown"] is True
    assert span.status.status_code.name == "ERROR"
    assert json.loads(span.attributes["langfuse.observation.input"]) == {"x": 1}


def test_every_tool_span_hangs_off_the_turn(run_with_llm) -> None:
    finished = run_with_llm(_CallsTool("get_catalog", {}))
    ids = {s.context.span_id: s.name for s in finished}
    tool = _by_name(finished, "tool.get_catalog")[0]
    assert ids[tool.parent.span_id] == "conversation.turn"
    assert len({s.context.trace_id for s in finished}) == 1


def test_tool_span_name_carries_the_tool_name(run_with_llm) -> None:
    """`tool.<name>` so a backend's span list is readable without opening each."""
    finished = run_with_llm(
        _CallsTool(
            "check_and_load_kb",
            {"context": "c", "requested_packet_ids": ["billing"], "active_packet_ids": []},
        )
    )
    assert _by_name(finished, "tool.check_and_load_kb")
