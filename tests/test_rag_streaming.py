"""`RagAgent.run_turn_stream` — the RAG baseline speaks §2.14.1 too (§9.5).

The widget posts every turn to `/chat/stream`, so an agent without the method
answers 501 and the panel is simply broken against `--agent rag`. A RAG turn
has no tool loop, but it does have a retrieval step worth showing, so the
stream carries `tool.start`/`tool.end` around it rather than deltas alone.
"""

from __future__ import annotations

from typing import Any

from hcag.rag.agent import RagAgent, RetrievedChunk, TurnMetrics
from hcag.rag.agent_config import RagAgentConfig
from hcag.runtime.llm import Final, LLMResponse, Message, TextDelta


class _FakeDeps:
    system_prompt = "You are a RAG agent."
    logger = None


def _chunk(kb_path: str, text: str, idx: int = 0) -> RetrievedChunk:
    return RetrievedChunk(
        id=f"{kb_path}#{idx}",
        kb_path=kb_path,
        chunk_index=idx,
        source_kind="text",
        text=text,
        headings=["Employment Pass"],
        image_path="",
        token_estimate=10,
    )


KEPT = [
    _chunk("employment-pass/replace-a-pass-card.md", "The fee is $65.40."),
    _chunk("employment-pass/replace-a-pass-card.md", "Apply within 1 week.", 1),
    _chunk("employment-pass/key-facts/wpspassconditions.md", "First replacement: $100."),
]
METRICS = TurnMetrics(kept_chunks=3, dropped_chunks=2, context_tokens=120)


class _FakeLLM:
    """Streams three fragments, then closes with the assembled Final."""

    def __init__(self, pieces: list[str] | None = None, explode_at: int | None = None) -> None:
        self.pieces = pieces if pieces is not None else ["The fee ", "is ", "$65.40."]
        self.explode_at = explode_at
        self.seen: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.seen.append(messages)
        return LLMResponse(text="".join(self.pieces), tool_calls=[])

    def chat_stream(self, messages: list[Message], tools: list[dict[str, Any]]):
        self.seen.append(messages)
        for i, piece in enumerate(self.pieces):
            if self.explode_at == i:
                raise RuntimeError("provider died")
            yield TextDelta(text=piece)
        yield Final(response=LLMResponse(text="".join(self.pieces), tool_calls=[]))


def _agent(llm: _FakeLLM, *, retrieve=None) -> RagAgent:
    agent = RagAgent(cfg=RagAgentConfig(), deps=_FakeDeps())
    agent._llm = llm
    agent._retrieve = retrieve or (lambda q: (list(KEPT), METRICS))
    return agent


def _kinds(events) -> list[str]:
    return [e.kind for e in events]


def test_a_rag_turn_streams_the_2141_event_vocabulary() -> None:
    agent = _agent(_FakeLLM())
    events = list(agent.run_turn_stream("What does a replacement card cost?"))

    assert _kinds(events) == [
        "assistant.start",
        "tool.start",
        "tool.end",
        "assistant.delta",
        "assistant.delta",
        "assistant.delta",
        "assistant.final",
    ]
    assert [e.seq for e in events] == [1, 2, 3, 4, 5, 6, 7]
    assert len({e.turn_id for e in events}) == 1


def test_the_retrieval_step_reports_what_it_kept_and_cited() -> None:
    agent = _agent(_FakeLLM())
    events = list(agent.run_turn_stream("q"))

    start = next(e for e in events if e.kind == "tool.start")
    end = next(e for e in events if e.kind == "tool.end")
    assert start.data["tool"] == "retrieve"
    assert (end.data["kept"], end.data["dropped"]) == (3, 2)
    assert end.data["context_tokens"] == 120
    # Sources are de-duplicated — two chunks from one file cite it once.
    assert end.data["sources"] == [
        "employment-pass/replace-a-pass-card.md",
        "employment-pass/key-facts/wpspassconditions.md",
    ]


def test_the_deltas_reassemble_into_the_final_answer() -> None:
    agent = _agent(_FakeLLM())
    events = list(agent.run_turn_stream("q"))

    streamed = "".join(e.data["text"] for e in events if e.kind == "assistant.delta")
    final = next(e for e in events if e.kind == "assistant.final")
    assert streamed == "The fee is $65.40." == final.data["text"]
    assert final.data["sources"]


def test_a_streamed_turn_lands_in_history_like_a_synchronous_one() -> None:
    agent = _agent(_FakeLLM())
    list(agent.run_turn_stream("What does a replacement card cost?"))

    assert agent._history == [
        ("user", "What does a replacement card cost?"),
        ("assistant", "The fee is $65.40."),
    ]
    # The next turn therefore sees the first one.
    list(agent.run_turn_stream("And the deadline?"))
    assert len(agent._history) == 4
    assert [m.role for m in agent._llm.seen[-1]] == ["system", "user", "assistant", "user"]


def test_the_context_block_reaches_the_model_on_the_streaming_path() -> None:
    agent = _agent(_FakeLLM())
    list(agent.run_turn_stream("q"))

    sent = agent._llm.seen[-1][-1].content
    assert "CONTEXT" in sent and "The fee is $65.40." in sent
    assert sent.rstrip().endswith("q")


def test_an_empty_retrieval_still_answers_and_says_so() -> None:
    agent = _agent(_FakeLLM(), retrieve=lambda q: ([], TurnMetrics()))
    events = list(agent.run_turn_stream("q"))

    assert next(e for e in events if e.kind == "tool.end").data["sources"] == []
    assert "no relevant excerpts" in agent._llm.seen[-1][-1].content
    assert next(e for e in events if e.kind == "assistant.final").data["text"]


def test_a_retrieval_crash_is_an_in_band_error_not_a_raise() -> None:
    def _boom(q):
        raise RuntimeError("lancedb gone")

    events = list(_agent(_FakeLLM(), retrieve=_boom).run_turn_stream("q"))

    assert _kinds(events) == ["assistant.start", "tool.start", "error"]
    assert events[-1].data["stage"] == "retrieval"
    assert "lancedb gone" in events[-1].data["detail"]


def test_a_generation_crash_mid_stream_is_an_in_band_error() -> None:
    """The 200 is committed by then, so it cannot become a 500 (§2.14.3)."""
    agent = _agent(_FakeLLM(explode_at=1))
    events = list(agent.run_turn_stream("q"))

    assert _kinds(events) == [
        "assistant.start",
        "tool.start",
        "tool.end",
        "assistant.delta",
        "error",
    ]
    assert events[-1].data["stage"] == "generation"
    assert "provider died" in events[-1].data["detail"]
    # A failed turn is not remembered as an answer.
    assert agent._history == []


def test_the_synchronous_route_is_untouched() -> None:
    """`evalrun` runs the §9.4 comparison through `/chat`; it keeps its own
    non-streaming provider call."""
    agent = _agent(_FakeLLM())
    answer = agent.run_turn("q")

    assert answer == "The fee is $65.40."
    assert agent._history == [("user", "q"), ("assistant", "The fee is $65.40.")]
