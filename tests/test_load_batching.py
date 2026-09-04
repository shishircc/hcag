"""One turn, one load — and packets keep the order they were loaded in.

Two separate concerns, both about what a turn costs:

* Latency. A question with two halves used to produce two (or four) sequential
  `check_and_load_kb` calls, each a full model round-trip. Sibling calls in one
  assistant message now collapse into a single load, and a second sequential
  call is counted, logged and answered with an in-band nudge.
* Order. The active set is ordered by when each packet was FIRST loaded, and
  that order is the module's — a model that mis-reports `active_packet_ids`
  cannot reshuffle the packets already sitting in the conversation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hcag.config import AgentConfig
from hcag.memory import FileSystemMemoryModule, LocalFsStorage, TokenBudget
from hcag.models import CheckAndLoadRequest, Delta
from hcag.runtime import AgentRuntime
from hcag.runtime.llm import LLMResponse, Message, ToolCall


PACKETS = ("d", "a", "b", "c")


def _root() -> str:
    head = (
        "<!-- HCAG:COMPILED id=_root -->\n---\nid: ''\ntitle: Root\n"
        "short_description: KB root\nlong_description: KB root\n"
        "token_size_estimate: 10\nkind: node\nsource_files: []\n"
        f"children: [{', '.join(PACKETS)}]\n---\n\n# Root\n\n## Sub-topics\n"
    )
    for pid in PACKETS:
        head += (
            f"\n#### `{pid}`\n- **path**: `{pid}/`\n- **depth**: 1\n"
            f"- **parent**: `_root`\n- **kind**: leaf\n- **title**: {pid}\n"
            f"- **short**: s\n- **long**: l\n- **tokens**: 50\n"
        )
    return head


def _leaf(pid: str) -> str:
    return (
        f"<!-- HCAG:COMPILED id={pid} -->\n---\nid: {pid}\ntitle: {pid}\n"
        "short_description: s\nlong_description: l\ntoken_size_estimate: 50\n"
        f"kind: leaf\nsource_files: [x.md]\nchildren: []\n---\n\n# {pid}\n\n"
        f"## Content\n\n<!-- source: x.md -->\nBody of {pid}.\n"
    )


def _kb(tmp_path: Path) -> Path:
    (tmp_path / "compiled.md").write_text(_root(), encoding="utf-8")
    for pid in PACKETS:
        d = tmp_path / pid
        d.mkdir()
        (d / "compiled.md").write_text(_leaf(pid), encoding="utf-8")
    return tmp_path


def _module(root: Path) -> FileSystemMemoryModule:
    return FileSystemMemoryModule(storage=LocalFsStorage(root), budget=TokenBudget(10_000))


class _CountingMemory:
    """Delegates to the real module, counting how often the tool is served."""

    def __init__(self, inner: FileSystemMemoryModule) -> None:
        self.inner = inner
        self.requests: list[list[str]] = []

    def get_catalog(self):
        return self.inner.get_catalog()

    def check_and_load_kb(self, request: CheckAndLoadRequest) -> Delta:
        self.requests.append(list(request.requested_packet_ids))
        return self.inner.check_and_load_kb(request)


def _load_call(call_id: str, *ids: str, active: list[str] | None = None) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="check_and_load_kb",
        arguments={
            "context": f"need {','.join(ids)}",
            "requested_packet_ids": list(ids),
            "active_packet_ids": active or [],
        },
    )


class _ScriptedLLM:
    """Replays a list of tool-call batches, then answers."""

    def __init__(self, script: list[list[ToolCall]], answer: str = "Done.") -> None:
        self.script = script
        self.answer = answer
        self.step = 0

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        if self.step < len(self.script):
            calls = self.script[self.step]
            self.step += 1
            return LLMResponse(text="", tool_calls=calls)
        self.step += 1
        return LLMResponse(text=self.answer, tool_calls=[])


def _agent(tmp_path: Path, llm: Any, memory: Any) -> AgentRuntime:
    cfg = AgentConfig(kb_root=str(tmp_path), max_active_tokens=10_000)
    cfg.observability.log.file_path = str(tmp_path / "hcag.log")
    return AgentRuntime(cfg=cfg, llm=llm, memory=memory)


# --- Batching --------------------------------------------------------------


def test_sibling_calls_in_one_message_become_a_single_load(tmp_path: Path) -> None:
    root = _kb(tmp_path)
    memory = _CountingMemory(_module(root))
    llm = _ScriptedLLM([[_load_call("tc-1", "d"), _load_call("tc-2", "a", "b")]])
    agent = _agent(root, llm, memory)

    agent.run_turn("Two halves, one question.")

    # One trip through the memory module, carrying every id in request order.
    assert memory.requests == [["d", "a", "b"]]
    assert agent._active_ids == ["d", "a", "b"]


def test_every_absorbed_call_id_still_gets_a_tool_result(tmp_path: Path) -> None:
    """The provider rejects an assistant message whose tool calls go unanswered."""
    root = _kb(tmp_path)
    memory = _CountingMemory(_module(root))
    llm = _ScriptedLLM([[_load_call("tc-1", "d"), _load_call("tc-2", "a")]])
    agent = _agent(root, llm, memory)

    agent.run_turn("Two halves, one question.")

    answered = [m.tool_call_id for m in agent._history if m.role == "tool"]
    assert answered == ["tc-1", "tc-2"]
    absorbed = next(m for m in agent._history if m.tool_call_id == "tc-2")
    assert "merged" in str(absorbed.content).lower()


def test_a_non_load_tool_call_is_left_alone_by_the_merge(tmp_path: Path) -> None:
    root = _kb(tmp_path)
    memory = _CountingMemory(_module(root))
    catalog_call = ToolCall(id="tc-0", name="get_catalog", arguments={})
    llm = _ScriptedLLM([[catalog_call, _load_call("tc-1", "d"), _load_call("tc-2", "a")]])
    agent = _agent(root, llm, memory)

    agent.run_turn("Two halves plus a stray catalog read.")

    assert memory.requests == [["d", "a"]]
    assert [m.tool_call_id for m in agent._history if m.role == "tool"] == [
        "tc-0",
        "tc-1",
        "tc-2",
    ]


class _Capture(logging.Handler):
    """The `hcag.runtime` logger sets propagate=False, so caplog never sees it."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _run_captured(agent: AgentRuntime, message: str) -> list[logging.LogRecord]:
    capture = _Capture()
    log = logging.getLogger("hcag.runtime")
    prior = log.level
    log.setLevel(logging.DEBUG)
    log.addHandler(capture)
    try:
        agent.run_turn(message)
    finally:
        log.removeHandler(capture)
        log.setLevel(prior)
    return [r for r in capture.records if hasattr(r, "event")]


def test_a_second_sequential_load_is_counted_logged_and_nudged(tmp_path: Path) -> None:
    """Sequential calls cannot be merged — they are already two round-trips —
    so they are surfaced instead."""
    root = _kb(tmp_path)
    memory = _CountingMemory(_module(root))
    llm = _ScriptedLLM(
        [[_load_call("tc-1", "d")], [_load_call("tc-2", "a", active=["d"])]]
    )
    agent = _agent(root, llm, memory)

    events = _run_captured(agent, "Ask, then discover a second need.")

    assert len(memory.requests) == 2
    extra = [e for e in events if e.event == "check_and_load_kb.extra_call_in_turn"]
    assert [e.calls for e in extra] == [2]
    end = next(e for e in events if e.event == "turn.end")
    assert end.turn_reload_calls == 2
    # The nudge rides back with the second load, where the model will read it.
    second = next(m for m in agent._history if m.tool_call_id == "tc-2")
    assert "single check_and_load_kb call" in str(second.content)
    # ...and not with the first.
    first = next(m for m in agent._history if m.tool_call_id == "tc-1")
    assert "single check_and_load_kb call" not in str(first.content)


def test_one_batched_call_is_not_nudged(tmp_path: Path) -> None:
    root = _kb(tmp_path)
    memory = _CountingMemory(_module(root))
    llm = _ScriptedLLM([[_load_call("tc-1", "d", "a", "b")]])
    agent = _agent(root, llm, memory)

    events = _run_captured(agent, "One well-formed batch.")

    assert not [e for e in events if e.event == "check_and_load_kb.extra_call_in_turn"]
    assert next(e for e in events if e.event == "turn.end").turn_reload_calls == 1


# --- Load order ------------------------------------------------------------


def test_packets_keep_first_load_order_across_turns(tmp_path: Path) -> None:
    """d loaded first, then a and b — the active set reads d, a, b."""
    module = _module(_kb(tmp_path))
    module.check_and_load_kb(
        CheckAndLoadRequest(context="", requested_packet_ids=["d"], active_packet_ids=[])
    )
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="", requested_packet_ids=["a", "b"], active_packet_ids=["d"]
        )
    )
    assert [p.id for p in delta.loaded] == ["a", "b"]
    assert delta.active_after == ["d", "a", "b"]


def test_a_reordered_claim_cannot_reshuffle_the_active_set(tmp_path: Path) -> None:
    """The packet blocks are already in the conversation in load order; the
    model's bookkeeping does not get to contradict that."""
    module = _module(_kb(tmp_path))
    for pid in ("d", "a"):
        module.check_and_load_kb(
            CheckAndLoadRequest(
                context="", requested_packet_ids=[pid], active_packet_ids=[]
            )
        )
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="",
            requested_packet_ids=["b"],
            active_packet_ids=["a", "d"],  # claimed backwards
        )
    )
    assert delta.active_after == ["d", "a", "b"]


def test_an_omitted_claim_does_not_drop_a_loaded_packet(tmp_path: Path) -> None:
    module = _module(_kb(tmp_path))
    module.check_and_load_kb(
        CheckAndLoadRequest(context="", requested_packet_ids=["d"], active_packet_ids=[])
    )
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(context="", requested_packet_ids=["a"], active_packet_ids=[])
    )
    assert delta.active_after == ["d", "a"]


def test_a_claim_the_module_never_loaded_is_kept_at_the_tail(tmp_path: Path) -> None:
    """A caller that preloaded elsewhere (voice startup, a resumed session)
    still owns membership for ids the module has no record of."""
    module = _module(_kb(tmp_path))
    delta = module.check_and_load_kb(
        CheckAndLoadRequest(
            context="", requested_packet_ids=["a"], active_packet_ids=["d"]
        )
    )
    assert delta.active_after == ["d", "a"]


def test_batched_load_lands_in_the_conversation_in_request_order(tmp_path: Path) -> None:
    root = _kb(tmp_path)
    memory = _CountingMemory(_module(root))
    llm = _ScriptedLLM([[_load_call("tc-1", "d")], [_load_call("tc-2", "a", "b")]])
    agent = _agent(root, llm, memory)

    agent.run_turn("Two turns' worth in one.")

    body = "\n".join(str(m.content) for m in agent._history if m.role == "tool")
    assert body.index("Body of d.") < body.index("Body of a.") < body.index("Body of b.")
    assert agent._active_ids == ["d", "a", "b"]
