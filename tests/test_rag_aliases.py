"""Query-time vocabulary: a name the corpus never spells (§9.3.2, §9.6).

MOM's pages say "ONE Pass" and "Overseas Networks & Expertise Pass" and never
"onepass", so a question about "onepass" matched nothing lexically and handed
the embedder a coined compound. Fixing retrieval alone was not enough: with the
right excerpt in context the generator still refused, because nothing in the
context said that "onepass" IS that pass. The operator's alias map is the only
thing that knows, so it feeds both halves — the query, and the generator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hcag.rag.agent import RagAgent, RetrievedChunk, TurnMetrics
from hcag.rag.agent_config import RagAgentConfig
from hcag.runtime.llm import LLMResponse, Message

ONE_PASS = "the ONE Pass, formally the Overseas Networks & Expertise Pass"
ALIASES = {"onepass": ONE_PASS, "one pass": ONE_PASS, "ep": "the Employment Pass"}


class _FakeDeps:
    system_prompt = "You are a RAG agent."
    logger = None
    tbl = None
    embedder = None


class _Embedder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed(self, texts: list[str]):
        self.seen.extend(texts)

        class _R:
            vectors = [[0.1, 0.2]]

        return _R()


class _LLM:
    def __init__(self) -> None:
        self.seen: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.seen.append(messages)
        return LLMResponse(text="answer", tool_calls=[])


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        id="one-pass#0",
        kb_path="one-pass/index.md",
        chunk_index=0,
        source_kind="text",
        text="The Overseas Networks & Expertise Pass is a personalised pass.",
        headings=["Overseas Networks & Expertise Pass"],
        image_path="",
        token_estimate=20,
    )


def _agent(aliases: dict[str, str] | None = None) -> RagAgent:
    cfg = RagAgentConfig()
    cfg.retrieval.aliases = dict(aliases if aliases is not None else ALIASES)
    deps = _FakeDeps()
    deps.embedder = _Embedder()
    agent = RagAgent(cfg=cfg, deps=deps)
    agent._llm = _LLM()
    agent._fts_search = lambda q, k: []
    agent._vector_search = lambda v, k: []
    return agent


# --- Expansion -------------------------------------------------------------


def test_a_coined_compound_gets_the_corpus_name_appended() -> None:
    expanded, applied = _agent()._expand_query("what is onepass")

    assert expanded == f"what is onepass {ONE_PASS}"
    assert applied == [("onepass", ONE_PASS)]


def test_a_multi_word_alias_matches() -> None:
    expanded, applied = _agent()._expand_query("What is One Pass?")

    assert ONE_PASS in expanded
    assert applied == [("one pass", ONE_PASS)]


def test_an_alias_only_fires_on_a_whole_word() -> None:
    """`ep` must not fire inside "deep", "sleep", "prep"."""
    expanded, applied = _agent()._expand_query("how deep is the prep for sleep")

    assert applied == []
    assert expanded == "how deep is the prep for sleep"


def test_a_query_already_using_the_corpus_name_is_not_padded() -> None:
    q = f"tell me about {ONE_PASS}"
    expanded, applied = _agent()._expand_query(q)

    assert expanded == q
    assert applied == []


def test_no_alias_map_is_a_no_op() -> None:
    assert _agent({})._expand_query("what is onepass") == ("what is onepass", [])


def test_both_legs_search_the_expanded_query() -> None:
    agent = _agent()
    seen: list[str] = []
    agent._fts_search = lambda q, k: seen.append(q) or []  # type: ignore[func-returns-value]

    agent._retrieve("what is onepass")

    assert seen == [f"what is onepass {ONE_PASS}"]
    assert agent._deps.embedder.seen == [f"what is onepass {ONE_PASS}"]


def test_the_expansion_is_recorded_on_the_turn() -> None:
    _kept, metrics = _agent()._retrieve("what is onepass")

    assert metrics.aliases_applied == [("onepass", ONE_PASS)]


# --- Telling the generator -------------------------------------------------


def test_the_context_block_carries_the_vocabulary() -> None:
    block = RagAgent._context_block([_chunk()], [("onepass", ONE_PASS)])

    assert block.startswith("VOCABULARY")
    assert f'"onepass" refers to: {ONE_PASS}' in block
    assert "personalised pass" in block  # the excerpts still follow


def test_no_vocabulary_block_when_nothing_fired() -> None:
    assert not RagAgent._context_block([_chunk()], []).startswith("VOCABULARY")
    assert not RagAgent._context_block([_chunk()]).startswith("VOCABULARY")


def test_an_empty_retrieval_still_reports_the_vocabulary() -> None:
    """The names are known even when the search found nothing."""
    block = RagAgent._context_block([], [("onepass", ONE_PASS)])

    assert "VOCABULARY" in block and "no relevant excerpts" in block


def test_the_question_the_model_answers_is_the_users_own_wording() -> None:
    agent = _agent()
    agent._retrieve = lambda q: ([_chunk()], TurnMetrics(
        kept_chunks=1, aliases_applied=[("onepass", ONE_PASS)]
    ))

    agent.run_turn("what is onepass")

    sent = agent._llm.seen[-1][-1].content
    assert sent.rstrip().endswith("what is onepass")  # not the expanded query
    assert "VOCABULARY" in sent


# --- The prompt half -------------------------------------------------------


def test_the_system_prompt_forbids_refusing_over_a_name() -> None:
    raw = Path("hcag/rag/prompts/rag_agent_system.md").read_text(encoding="utf-8")
    prompt = " ".join(raw.split())  # the file is hard-wrapped

    assert "VOCABULARY" in prompt
    assert "A name is not a fact" in prompt
    # ...without loosening the grounding rule it sits next to.
    assert "every fact still comes only from the CONTEXT" in prompt
    assert "Answer strictly from the CONTEXT" in prompt
