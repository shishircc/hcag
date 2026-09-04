"""A dead embedder must not take full-text search down with it (§9.3.2).

Observed: with no `OPENAI_API_KEY`, every turn logged `rag_agent.embed.failed`
and then `kept_chunks: 0` — `_retrieve` returned empty before `_fts_search` was
ever called. The user saw a fluent "I don't have enough information to answer
that from the knowledge base", which reads exactly like a retrieval-quality
problem and was in fact a missing credential taking out the half of the
retriever that never needed one.
"""

from __future__ import annotations

import logging
from typing import Any

from hcag.rag.agent import RagAgent
from hcag.rag.agent_config import RagAgentConfig


class _FakeDeps:
    system_prompt = "You are a RAG agent."
    logger = None
    tbl = None
    embedder = None


class _DeadEmbedder:
    def embed(self, texts: list[str]):
        raise RuntimeError("Missing credentials. Please pass an `api_key`")


class _LiveEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]):
        self.calls += 1

        class _R:
            vectors = [[0.1, 0.2, 0.3]]

        return _R()


def _row(kb_path: str, idx: int = 0) -> dict[str, Any]:
    return {
        "id": f"{kb_path}#{idx}",
        "kb_path": kb_path,
        "chunk_index": idx,
        "source_kind": "text",
        "text": "The Overseas Networks & Expertise Pass is a personalised pass.",
        "headings": ["Overseas Networks & Expertise Pass"],
        "image_path": "",
        "token_estimate": 20,
    }


FTS_ROWS = [_row("one-pass/index.md"), _row("one-pass/key-facts.md")]
VEC_ROWS = [_row("one-pass/eligibility.md")]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _agent(embedder, fts_rows, vec_rows) -> RagAgent:
    deps = _FakeDeps()
    deps.embedder = embedder
    agent = RagAgent(cfg=RagAgentConfig(), deps=deps)
    agent._fts_search = lambda q, k: list(fts_rows)
    agent._vector_search = lambda v, k: list(vec_rows)
    return agent


def test_a_dead_embedder_falls_back_to_full_text_search() -> None:
    agent = _agent(_DeadEmbedder(), FTS_ROWS, VEC_ROWS)

    kept, metrics = agent._retrieve("what is one pass")

    # The whole point: the turn still has context to answer from.
    assert metrics.kept_chunks == 2
    assert [c.kb_path for c in kept] == ["one-pass/index.md", "one-pass/key-facts.md"]
    assert metrics.fts_hits == 2
    assert metrics.vector_hits == 0
    assert metrics.degraded == "vector"


def test_the_vector_search_is_not_attempted_without_a_query_vector() -> None:
    """Passing a None vector into LanceDB would raise inside the search and
    turn a recoverable degradation into a caught-and-swallowed error."""
    attempted: list[Any] = []
    agent = _agent(_DeadEmbedder(), FTS_ROWS, VEC_ROWS)
    agent._vector_search = lambda v, k: attempted.append(v) or []  # type: ignore[func-returns-value]

    agent._retrieve("what is one pass")

    assert attempted == []


def test_both_legs_down_is_reported_as_a_failed_retrieval() -> None:
    agent = _agent(_DeadEmbedder(), [], [])

    kept, metrics = agent._retrieve("what is one pass")

    assert kept == []
    assert metrics.degraded == "all"


def test_an_empty_result_from_a_healthy_retriever_is_not_degraded() -> None:
    """No hits is a fact about the KB; it must not be dressed up as a fault."""
    agent = _agent(_LiveEmbedder(), [], [])

    kept, metrics = agent._retrieve("something absent")

    assert kept == []
    assert metrics.degraded == ""


def test_a_healthy_turn_uses_both_legs() -> None:
    embedder = _LiveEmbedder()
    agent = _agent(embedder, FTS_ROWS, VEC_ROWS)

    _kept, metrics = agent._retrieve("what is one pass")

    assert embedder.calls == 1
    assert (metrics.vector_hits, metrics.fts_hits) == (1, 2)
    assert metrics.degraded == ""


def test_the_degradation_is_logged_where_an_operator_will_see_it() -> None:
    from hcag.logger import HcagLogger

    capture = _Capture()
    log = logging.getLogger("hcag.test.rag")
    log.setLevel(logging.DEBUG)
    log.addHandler(capture)
    try:
        agent = _agent(_DeadEmbedder(), FTS_ROWS, VEC_ROWS)
        agent._logger = HcagLogger(log)
        agent._retrieve("what is one pass")
    finally:
        log.removeHandler(capture)

    events = {r.event: r for r in capture.records if hasattr(r, "event")}
    assert "rag_agent.embed.failed" in events
    assert events["rag_agent.embed.failed"].falling_back_to == "fts"
    # A WARN naming the loss, not just a debug line with a low hit count.
    degraded = events["rag_agent.retrieval.degraded"]
    assert degraded.levelno == logging.WARNING
    assert degraded.lost == "vector"
    assert degraded.kept == 2
