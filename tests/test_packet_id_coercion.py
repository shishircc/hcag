"""A stringified `requested_packet_ids` must not be shredded into characters.

Regression: a model returned the array argument as JSON *text*
(``'["www.mom.gov.sg.passes-and-permits.employment-pass"],'``). The tool
boundary called ``list()`` on it, which yields one entry per character, so the
load reported ~50 ``unknown_packet_id`` errors and never fetched the packet the
model actually asked for.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hcag.config import AgentConfig
from hcag.memory import FileSystemMemoryModule, LocalFsStorage, TokenBudget
from hcag.models import CheckAndLoadRequest, coerce_packet_ids, is_well_formed_id_list
from hcag.runtime import AgentRuntime
from hcag.runtime.llm import LLMResponse, Message, ToolCall


REAL_ID = "www.mom.gov.sg.passes-and-permits.employment-pass"
STRINGIFIED = f'["{REAL_ID}"],'


# --- The normalizer --------------------------------------------------------


def test_the_exact_failing_payload_recovers_one_id() -> None:
    assert coerce_packet_ids(STRINGIFIED) == [REAL_ID]


def test_a_well_formed_list_passes_through_unchanged() -> None:
    assert coerce_packet_ids([REAL_ID, "a.b"]) == [REAL_ID, "a.b"]


def test_dots_and_hyphens_inside_an_id_are_never_split() -> None:
    assert coerce_packet_ids(f'"{REAL_ID}"') == [REAL_ID]


def test_other_stringified_shapes() -> None:
    assert coerce_packet_ids('["a.b", "c.d"]') == ["a.b", "c.d"]
    assert coerce_packet_ids("a.b, c.d") == ["a.b", "c.d"]
    assert coerce_packet_ids('["a.b", "c.d"') == ["a.b", "c.d"]  # truncated JSON
    assert coerce_packet_ids([["a.b"], "c.d"]) == ["a.b", "c.d"]  # nested


def test_empty_and_uninterpretable_inputs_yield_nothing() -> None:
    assert coerce_packet_ids(None) == []
    assert coerce_packet_ids("") == []
    assert coerce_packet_ids("  , ") == []
    assert coerce_packet_ids('{"requested": 1}') == []


def test_duplicates_collapse_in_order() -> None:
    assert coerce_packet_ids(["b", "a", "b"]) == ["b", "a"]


def test_malformed_arguments_are_recognizable() -> None:
    assert is_well_formed_id_list([REAL_ID]) is True
    assert is_well_formed_id_list(None) is True
    assert is_well_formed_id_list(STRINGIFIED) is False


# --- Through the memory module --------------------------------------------


ROOT = """<!-- HCAG:COMPILED id=_root -->
---
id: ''
title: Root
short_description: KB root
long_description: KB root
token_size_estimate: 10
kind: node
source_files: []
children: [employment-pass]
---

# Root

## Sub-topics

#### `employment-pass`
- **path**: `employment-pass/`
- **depth**: 1
- **parent**: `_root`
- **kind**: leaf
- **title**: Employment Pass
- **short**: card replacement
- **long**: card replacement
- **tokens**: 50
"""

LEAF = """<!-- HCAG:COMPILED id=employment-pass -->
---
id: employment-pass
title: Employment Pass
short_description: s
long_description: l
token_size_estimate: 50
kind: leaf
source_files: [x.md]
children: []
---

# Employment Pass

## Content

<!-- source: x.md -->
Replacement fee is $65.40.
"""


def _kb(tmp_path: Path) -> Path:
    (tmp_path / "compiled.md").write_text(ROOT, encoding="utf-8")
    d = tmp_path / "employment-pass"
    d.mkdir()
    (d / "compiled.md").write_text(LEAF, encoding="utf-8")
    return tmp_path


def test_module_loads_the_packet_from_a_stringified_request(tmp_path: Path) -> None:
    module = FileSystemMemoryModule(
        storage=LocalFsStorage(_kb(tmp_path)), budget=TokenBudget(10_000)
    )
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="EP card replacement",
            requested_packet_ids='["employment-pass"],',  # type: ignore[arg-type]
            active_packet_ids=[],
        )
    )
    assert [p.id for p in delta.loaded] == ["employment-pass"]
    assert delta.errors == []


# --- Through the tool boundary --------------------------------------------


class _StringArgsLLM:
    """Turn 1 sends the array as JSON text, the way the real model did."""

    def __init__(self) -> None:
        self.step = 0

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.step += 1
        if self.step == 1:
            return LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="tc-1",
                        name="check_and_load_kb",
                        arguments={
                            "context": "EP card replacement",
                            "requested_packet_ids": '["employment-pass"],',
                            "active_packet_ids": "[]",
                        },
                    )
                ],
            )
        return LLMResponse(text="The fee is $65.40.", tool_calls=[])


class _Capture(logging.Handler):
    """The `hcag.runtime` logger sets propagate=False, so caplog never sees it."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_tool_boundary_recovers_and_logs_the_malformation(tmp_path: Path) -> None:
    root = _kb(tmp_path)
    cfg = AgentConfig(kb_root=str(root), max_active_tokens=10_000)
    cfg.observability.log.file_path = str(tmp_path / "hcag.log")
    agent = AgentRuntime(cfg=cfg, llm=_StringArgsLLM())

    capture = _Capture()
    hcag_logger = logging.getLogger("hcag.runtime")
    prior_level = hcag_logger.level
    hcag_logger.setLevel(logging.DEBUG)
    hcag_logger.addHandler(capture)
    try:
        reply = agent.run_turn("What does a replacement EP card cost?")
    finally:
        hcag_logger.removeHandler(capture)
        hcag_logger.setLevel(prior_level)
    assert "65.40" in reply

    events = [r for r in capture.records if hasattr(r, "event")]
    results = [r for r in events if r.event == "check_and_load_kb.result"]
    assert results and results[-1].loaded == ["employment-pass"]
    # No character was ever mistaken for a packet id.
    assert results[-1].errors == []
    # The model-side slip is still visible rather than silently papered over.
    malformed = [r for r in events if r.event == "check_and_load_kb.malformed_args"]
    assert {r.field for r in malformed} == {"requested_packet_ids", "active_packet_ids"}
    assert next(r for r in malformed if r.field == "requested_packet_ids").recovered == [
        "employment-pass"
    ]
