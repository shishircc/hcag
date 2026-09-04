"""The RAG chat agent (§9).

Presents the same public surface as ``AgentRuntime`` (``bootstrap`` +
``run_turn`` + ``run_turn_stream``) so ``hcag-server`` can swap the two behind
one HTTP route, streaming or not.

Retrieval strategy: for each turn we run a vector search and a full-text
search separately against the LanceDB ``kb`` table and fuse the two ranked
lists with reciprocal-rank fusion (§9.3.2). Doing the RRF ourselves — rather
than relying on LanceDB's ``query_type="hybrid"`` — keeps the code
version-stable across LanceDB releases and makes the fusion weights auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..logger import HcagLogger
from ..runtime.events import Event, EventStream
from ..runtime.llm import Final, LiteLLMAdapter, Message, TextDelta
from .agent_config import RagAgentConfig, RerankerKind
from .chunker import make_token_estimator
from .embedder import Embedder

if TYPE_CHECKING:
    from collections.abc import Iterator


class AgentBootstrapError(RuntimeError):
    """Startup-time failure — missing index, missing table, mismatched embed model."""


# --- Retrieved-chunk record --------------------------------------------------


@dataclass
class RetrievedChunk:
    id: str
    kb_path: str
    chunk_index: int
    source_kind: str
    text: str
    headings: list[str]
    image_path: str
    token_estimate: int
    vector_rank: int | None = None  # None if this hit came only from FTS
    fts_rank: int | None = None     # None if this hit came only from vector
    fused_score: float = 0.0


# --- Shared, one-time bootstrap ---------------------------------------------


@dataclass
class RagAgentDeps:
    """State safe to reuse across sessions.

    ``build_deps`` opens the LanceDB connection, sanity-checks the embed-model
    against the manifest, and loads the system prompt. All expensive I/O.
    ``RagAgent`` instances then take ``deps`` by reference — the server keeps
    one deps object and hands it to every session's agent.
    """

    cfg: RagAgentConfig
    tbl: Any                  # lancedb.Table
    embedder: Embedder
    system_prompt: str
    indexed_embed_model: str
    logger: HcagLogger | None


def _packaged_system_prompt() -> str:
    return resources.files("hcag.rag.prompts").joinpath("rag_agent_system.md").read_text(encoding="utf-8")


def _load_system_prompt(path: str) -> str:
    if not path:
        return _packaged_system_prompt()
    return Path(path).read_text(encoding="utf-8")


def _list_table_names(db: Any) -> set[str]:
    """Return the set of table names in a LanceDB connection.

    LanceDB's API has shifted across versions:
    - Older: ``db.table_names()`` returns ``list[str]``.
    - Newer: ``db.list_tables()`` returns a paginated result object whose
      ``.tables`` attribute (or ``tables`` key) is the list.

    We probe in this order and normalize to ``set[str]``.
    """
    # Newer paginated API first — try attribute, then dict-style key.
    if hasattr(db, "list_tables"):
        try:
            result = db.list_tables()
        except Exception:
            result = None
        if result is not None:
            tables = getattr(result, "tables", None)
            if tables is None and hasattr(result, "get"):
                tables = result.get("tables")
            if isinstance(tables, (list, tuple)):
                return {str(t) for t in tables}
            # Some versions may already return a list directly.
            if isinstance(result, (list, tuple)):
                return {str(t) for t in result}
    # Fallback to the deprecated flat API.
    if hasattr(db, "table_names"):
        try:
            names = db.table_names()
            if isinstance(names, (list, tuple)):
                return {str(t) for t in names}
        except Exception:
            pass
    return set()


def _read_indexed_embed_model(tbl: Any) -> str | None:
    """Pull the ``embed_model`` recorded by the indexer from one KB row's metadata.

    Returns None if the table is empty or the metadata is unreadable (in which
    case the caller decides whether to hard-fail or continue).
    """
    try:
        arrow_tbl = tbl.to_arrow() if hasattr(tbl, "to_arrow") else tbl.to_lance().to_table()
        rows = arrow_tbl.slice(0, 1).to_pylist()
    except Exception:
        return None
    if not rows:
        return None
    raw = rows[0].get("metadata")
    if not raw:
        return None
    try:
        return json.loads(raw).get("embed_model")
    except (json.JSONDecodeError, TypeError):
        return None


def build_deps(cfg: RagAgentConfig, logger: HcagLogger | None = None) -> RagAgentDeps:
    """One-time bootstrap — connect, verify, load prompt.

    Called by the server at startup (or by any script that wants to hold one
    long-lived agent). Never called per-request.
    """
    try:
        import lancedb  # type: ignore
    except ImportError as e:
        raise AgentBootstrapError(
            "lancedb is not installed. Install with `pip install hcag[rag]`."
        ) from e

    index_path = Path(cfg.index.path)
    if not index_path.is_dir():
        raise AgentBootstrapError(
            f"rag index directory not found: {index_path}. "
            "Point --rag-index at a folder produced by the `rag` CLI."
        )

    db = lancedb.connect(str(index_path))
    name_set = _list_table_names(db)
    if not name_set:
        raise AgentBootstrapError(
            f"no tables at {index_path}. Run `rag --kb <path> --index {index_path}` first."
        )
    if cfg.index.table not in name_set:
        raise AgentBootstrapError(
            f"table '{cfg.index.table}' not found in {index_path}. "
            f"Available: {sorted(name_set)}"
        )

    tbl = db.open_table(cfg.index.table)

    try:
        row_count = tbl.count_rows()
    except Exception:
        row_count = 0
    if row_count == 0:
        raise AgentBootstrapError(
            f"table '{cfg.index.table}' is empty. Run `rag --kb <path> --index {index_path}` first."
        )

    # Sanity-check embed model against the indexer's stamp (§9.3.1).
    indexed_model = _read_indexed_embed_model(tbl) or ""
    if indexed_model and indexed_model != cfg.embedding.model:
        if cfg.allow_embed_mismatch:
            if logger is not None:
                logger.warn(
                    "rag_agent.embed_mismatch.allowed",
                    indexed=indexed_model,
                    configured=cfg.embedding.model,
                )
        else:
            raise AgentBootstrapError(
                f"embedding model mismatch: index was built with '{indexed_model}' but "
                f"[embedding].model is '{cfg.embedding.model}'. Fix rag_agent.toml, "
                "re-run `rag --recreate`, or set allow_embed_mismatch = true (usually wrong)."
            )

    embedder = Embedder(cfg.embedding)
    system_prompt = _load_system_prompt(cfg.system_prompt_path)

    if logger is not None:
        logger.info(
            "rag_agent.bootstrap",
            index_path=str(index_path),
            table=cfg.index.table,
            rows=row_count,
            indexed_embed_model=indexed_model,
            configured_embed_model=cfg.embedding.model,
            gen_model=cfg.llm.model,
            top_k=cfg.retrieval.top_k,
            max_context_tokens=cfg.retrieval.max_context_tokens,
        )

    return RagAgentDeps(
        cfg=cfg,
        tbl=tbl,
        embedder=embedder,
        system_prompt=system_prompt,
        indexed_embed_model=indexed_model or cfg.embedding.model,
        logger=logger,
    )


# --- Retrieval + fusion + assembly ------------------------------------------


def _hit_to_chunk(row: dict[str, Any], vector_rank: int | None, fts_rank: int | None) -> RetrievedChunk:
    return RetrievedChunk(
        id=str(row.get("id", "")),
        kb_path=str(row.get("kb_path", "")),
        chunk_index=int(row.get("chunk_index", 0) or 0),
        source_kind=str(row.get("source_kind", "")),
        text=str(row.get("text", "")),
        headings=list(row.get("headings", []) or []),
        image_path=str(row.get("image_path", "") or ""),
        token_estimate=int(row.get("token_estimate", 0) or 0),
        vector_rank=vector_rank,
        fts_rank=fts_rank,
    )


def _rrf_fuse(
    vector_hits: list[dict[str, Any]],
    fts_hits: list[dict[str, Any]],
    k_constant: int = 60,
) -> list[RetrievedChunk]:
    """Reciprocal-rank fusion. Score(id) = sum(1 / (k + rank)) across the two lists."""
    by_id: dict[str, RetrievedChunk] = {}

    for rank, row in enumerate(vector_hits):
        cid = str(row.get("id", ""))
        if not cid:
            continue
        chunk = by_id.get(cid) or _hit_to_chunk(row, vector_rank=None, fts_rank=None)
        chunk.vector_rank = rank
        chunk.fused_score += 1.0 / (k_constant + rank + 1)
        by_id[cid] = chunk

    for rank, row in enumerate(fts_hits):
        cid = str(row.get("id", ""))
        if not cid:
            continue
        chunk = by_id.get(cid) or _hit_to_chunk(row, vector_rank=None, fts_rank=None)
        chunk.fts_rank = rank
        chunk.fused_score += 1.0 / (k_constant + rank + 1)
        by_id[cid] = chunk

    return sorted(by_id.values(), key=lambda c: c.fused_score, reverse=True)


def _linear_fuse(
    vector_hits: list[dict[str, Any]],
    fts_hits: list[dict[str, Any]],
) -> list[RetrievedChunk]:
    """Linear combo — vector 0.6 / FTS 0.4 of normalized inverse rank."""
    by_id: dict[str, RetrievedChunk] = {}
    n_v = max(len(vector_hits), 1)
    n_f = max(len(fts_hits), 1)
    for rank, row in enumerate(vector_hits):
        cid = str(row.get("id", ""))
        if not cid:
            continue
        chunk = by_id.get(cid) or _hit_to_chunk(row, vector_rank=None, fts_rank=None)
        chunk.vector_rank = rank
        chunk.fused_score += 0.6 * (1.0 - rank / n_v)
        by_id[cid] = chunk
    for rank, row in enumerate(fts_hits):
        cid = str(row.get("id", ""))
        if not cid:
            continue
        chunk = by_id.get(cid) or _hit_to_chunk(row, vector_rank=None, fts_rank=None)
        chunk.fts_rank = rank
        chunk.fused_score += 0.4 * (1.0 - rank / n_f)
        by_id[cid] = chunk
    return sorted(by_id.values(), key=lambda c: c.fused_score, reverse=True)


def _dedup_adjacent(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Merge consecutive-chunk hits from the same file so the LLM sees one span.

    Kept simple: group by kb_path, then merge chunks whose indexes differ by 1.
    Order within the returned list preserves the rank of the highest-scored
    chunk in each merged group.
    """
    by_path: dict[str, list[RetrievedChunk]] = {}
    for c in chunks:
        by_path.setdefault(c.kb_path, []).append(c)

    merged: list[RetrievedChunk] = []
    seen_ids: set[str] = set()

    for c in chunks:
        if c.id in seen_ids:
            continue
        group = sorted(by_path[c.kb_path], key=lambda x: x.chunk_index)
        # Find the maximal contiguous run around c.chunk_index that's in group.
        idx_map = {g.chunk_index: g for g in group}
        run = [c]
        # extend right
        cur = c.chunk_index + 1
        while cur in idx_map:
            run.append(idx_map[cur])
            cur += 1
        # extend left
        cur = c.chunk_index - 1
        while cur in idx_map:
            run.insert(0, idx_map[cur])
            cur -= 1
        combined_text = "\n\n".join(r.text for r in run)
        combined_tokens = sum(r.token_estimate for r in run)
        best_score = max(r.fused_score for r in run)
        merged.append(
            RetrievedChunk(
                id=run[0].id,
                kb_path=c.kb_path,
                chunk_index=run[0].chunk_index,
                source_kind=c.source_kind,
                text=combined_text,
                headings=run[0].headings,
                image_path=c.image_path,
                token_estimate=combined_tokens,
                vector_rank=c.vector_rank,
                fts_rank=c.fts_rank,
                fused_score=best_score,
            )
        )
        for r in run:
            seen_ids.add(r.id)
    return merged


def _budget_cap(chunks: list[RetrievedChunk], max_context_tokens: int, est) -> tuple[list[RetrievedChunk], list[RetrievedChunk]]:
    """Include chunks in rank order until token budget is exhausted.

    If the top hit alone exceeds the budget, keep it anyway (§9.7) — refusing
    the top signal is worse than crowding the budget.
    """
    kept: list[RetrievedChunk] = []
    dropped: list[RetrievedChunk] = []
    running = 0
    for c in chunks:
        est_tokens = c.token_estimate or est(c.text)
        if not kept:
            kept.append(c)
            running += est_tokens
            continue
        if running + est_tokens > max_context_tokens:
            dropped.append(c)
            continue
        kept.append(c)
        running += est_tokens
    return kept, dropped


def _source_order(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    return sorted(chunks, key=lambda c: (c.kb_path, c.chunk_index))


def _format_context(chunks: list[RetrievedChunk]) -> str:
    parts: list[str] = ["CONTEXT"]
    for c in chunks:
        head = " > ".join(c.headings) if c.headings else "(no heading)"
        src_line = f"[source: {c.kb_path} § {head}]"
        if c.image_path:
            src_line += f" [image: {c.image_path}]"
        parts.append(src_line + "\n" + c.text)
    return "\n\n".join(parts)


# --- Public agent -----------------------------------------------------------


@dataclass
class TurnMetrics:
    vector_hits: int = 0
    fts_hits: int = 0
    fused_hits: int = 0
    kept_chunks: int = 0
    dropped_chunks: int = 0
    context_tokens: int = 0
    generation_tokens: int = 0


class RagAgent:
    """Per-session RAG chat agent (§9.2)."""

    def __init__(
        self,
        cfg: RagAgentConfig,
        deps: RagAgentDeps | None = None,
        logger: HcagLogger | None = None,
    ) -> None:
        self.cfg = cfg
        self._deps = deps
        self._logger = logger or (deps.logger if deps else None)
        self._llm: LiteLLMAdapter | None = None
        # History carries plain user/assistant turns — retrieval context is
        # attached per-turn and does NOT bloat the stored history.
        self._history: list[tuple[str, str]] = []  # [(role, text), ...]
        self._est = make_token_estimator()
        self._turn_index = 0

    def bootstrap(self) -> None:
        """Idempotent — builds deps if not injected."""
        if self._deps is None:
            self._deps = build_deps(self.cfg, self._logger)
        if self._llm is None:
            self._llm = LiteLLMAdapter(self.cfg.llm)

    # --- Retrieval --------------------------------------------------------

    def _vector_search(self, query_vec: list[float], k: int) -> list[dict[str, Any]]:
        assert self._deps is not None
        tbl = self._deps.tbl
        try:
            result = tbl.search(query_vec).limit(k)
            arrow_tbl = result.to_arrow() if hasattr(result, "to_arrow") else result.to_list()
            if hasattr(arrow_tbl, "to_pylist"):
                return arrow_tbl.to_pylist()
            return list(arrow_tbl)
        except Exception as e:  # noqa: BLE001
            if self._logger:
                self._logger.warn("rag_agent.vector_search.failed", error=f"{type(e).__name__}: {e}")
            return []

    def _fts_search(self, query_text: str, k: int) -> list[dict[str, Any]]:
        assert self._deps is not None
        tbl = self._deps.tbl
        try:
            result = tbl.search(query_text, query_type="fts").limit(k)
            arrow_tbl = result.to_arrow() if hasattr(result, "to_arrow") else result.to_list()
            if hasattr(arrow_tbl, "to_pylist"):
                return arrow_tbl.to_pylist()
            return list(arrow_tbl)
        except Exception as e:  # noqa: BLE001
            if self._logger:
                self._logger.warn("rag_agent.fts_search.failed", error=f"{type(e).__name__}: {e}")
            return []

    def _retrieve(self, query_text: str) -> tuple[list[RetrievedChunk], TurnMetrics]:
        assert self._deps is not None
        metrics = TurnMetrics()
        cfg = self.cfg.retrieval

        # Embed the query.
        try:
            embed_result = self._deps.embedder.embed([query_text])
            query_vec = embed_result.vectors[0]
        except Exception as e:  # noqa: BLE001
            if self._logger:
                self._logger.warn("rag_agent.embed.failed", error=f"{type(e).__name__}: {e}")
            return [], metrics

        # Pull more than top_k per side so the fusion has something to work with.
        per_side = max(cfg.top_k * 2, cfg.top_k + 4)
        vector_hits = self._vector_search(query_vec, per_side)
        fts_hits = self._fts_search(query_text, per_side)
        metrics.vector_hits = len(vector_hits)
        metrics.fts_hits = len(fts_hits)

        fused = self._fuse(vector_hits, fts_hits, cfg.reranker)[: cfg.top_k]
        metrics.fused_hits = len(fused)

        if cfg.merge_adjacent:
            fused = _dedup_adjacent(fused)

        kept, dropped = _budget_cap(fused, cfg.max_context_tokens, self._est)
        metrics.kept_chunks = len(kept)
        metrics.dropped_chunks = len(dropped)

        kept = _source_order(kept)
        metrics.context_tokens = sum(c.token_estimate for c in kept)

        if self._logger:
            self._logger.debug(
                "rag_agent.retrieve",
                query_len=len(query_text),
                vector_hits=metrics.vector_hits,
                fts_hits=metrics.fts_hits,
                fused=metrics.fused_hits,
                kept=metrics.kept_chunks,
                dropped=metrics.dropped_chunks,
                context_tokens=metrics.context_tokens,
            )
        return kept, metrics

    def _fuse(
        self,
        vector_hits: list[dict[str, Any]],
        fts_hits: list[dict[str, Any]],
        reranker: RerankerKind,
    ) -> list[RetrievedChunk]:
        if reranker == "linear":
            return _linear_fuse(vector_hits, fts_hits)
        if reranker == "none":
            # No fusion — just concat vector hits then FTS hits, dedup by id.
            seen: set[str] = set()
            out: list[RetrievedChunk] = []
            for rank, row in enumerate(vector_hits):
                cid = str(row.get("id", ""))
                if cid and cid not in seen:
                    out.append(_hit_to_chunk(row, vector_rank=rank, fts_rank=None))
                    seen.add(cid)
            for rank, row in enumerate(fts_hits):
                cid = str(row.get("id", ""))
                if cid and cid not in seen:
                    out.append(_hit_to_chunk(row, vector_rank=None, fts_rank=rank))
                    seen.add(cid)
            return out
        return _rrf_fuse(vector_hits, fts_hits)

    # --- Prompt + generation ----------------------------------------------

    def _build_messages(self, user_message: str, context_block: str) -> list[Message]:
        assert self._deps is not None
        msgs: list[Message] = [Message(role="system", content=self._deps.system_prompt)]
        # Prior turns as plain user/assistant messages (no context wrapper).
        for role, text in self._history:
            msgs.append(Message(role=role, content=text))
        # Current turn: wrap the user message with the CONTEXT block.
        wrapped = f"{context_block}\n\n---\nQUESTION\n{user_message}" if context_block else user_message
        msgs.append(Message(role="user", content=wrapped))
        return msgs

    # --- Turn helpers -----------------------------------------------------

    @staticmethod
    def _context_block(kept: list[RetrievedChunk]) -> str:
        if not kept:
            return "CONTEXT\n(none — no relevant excerpts were retrieved for this question)"
        return _format_context(kept)

    @staticmethod
    def _cited_paths(kept: list[RetrievedChunk]) -> list[str]:
        """The KB paths behind the answer, once each, best-ranked first."""
        return list(dict.fromkeys(c.kb_path for c in kept))

    def _record_turn(self, user_message: str, answer: str, metrics: TurnMetrics) -> None:
        # Store plain user + assistant text so history stays cache-friendly and
        # doesn't accumulate stale CONTEXT blocks turn over turn.
        self._history.append(("user", user_message))
        self._history.append(("assistant", answer))
        if self._logger:
            self._logger.info(
                "rag_agent.turn",
                user_chars=len(user_message),
                answer_chars=len(answer),
                kept_chunks=metrics.kept_chunks,
                dropped_chunks=metrics.dropped_chunks,
                context_tokens=metrics.context_tokens,
                turns_in_history=len(self._history) // 2,
            )

    # --- Public turn ------------------------------------------------------

    def run_turn(self, user_message: str) -> str:
        self.bootstrap()
        assert self._deps is not None and self._llm is not None

        try:
            kept, metrics = self._retrieve(user_message)
        except Exception as e:  # noqa: BLE001
            reason = f"{type(e).__name__}: {e}"
            if self._logger:
                self._logger.error("rag_agent.retrieval.crash", error=reason)
            return f"[retrieval_error] {reason}"

        msgs = self._build_messages(user_message, self._context_block(kept))

        try:
            resp = self._llm.chat(msgs, tools=[])
        except Exception as e:  # noqa: BLE001
            reason = f"{type(e).__name__}: {e}"
            if self._logger:
                self._logger.error("rag_agent.generate.failed", error=reason)
            return f"[generation_error] {reason}"

        answer = resp.text or ""
        self._record_turn(user_message, answer, metrics)
        return answer

    def run_turn_stream(self, user_message: str) -> "Iterator[Event]":
        """Yield §2.14.1 events for one turn — the same vocabulary the HCAG
        runtime emits, so one client reducer renders both agents (§9.5).

        A RAG turn has no tool loop, but it does have a retrieval step worth
        watching: `tool.start`/`tool.end` report what the search kept, dropped
        and cited, which is the part of a RAG answer a reader needs in order to
        judge it — and the part that makes the §9.4 comparison legible when the
        two agents are driven from the same widget.

        `run_turn` keeps its own non-streaming path deliberately rather than
        draining this one: `evalrun` (§7.3) runs the comparison through `/chat`,
        and it should keep issuing the same non-streaming provider call it
        always has. The two share every step that decides an answer — retrieval,
        context block, message build, history — so they cannot drift on
        substance, only on transport.
        """
        self.bootstrap()
        assert self._deps is not None and self._llm is not None

        self._turn_index += 1
        stream = EventStream(turn_id=f"r_{self._turn_index}")
        yield stream.emit("assistant.start")

        yield stream.emit("tool.start", tool="retrieve", context=user_message[:512])
        try:
            kept, metrics = self._retrieve(user_message)
        except Exception as e:  # noqa: BLE001
            reason = f"{type(e).__name__}: {e}"
            if self._logger:
                self._logger.error("rag_agent.retrieval.crash", error=reason)
            yield stream.emit("error", stage="retrieval", detail=reason)
            return
        yield stream.emit(
            "tool.end",
            tool="retrieve",
            kept=metrics.kept_chunks,
            dropped=metrics.dropped_chunks,
            context_tokens=metrics.context_tokens,
            sources=self._cited_paths(kept),
        )

        msgs = self._build_messages(user_message, self._context_block(kept))
        parts: list[str] = []
        answer = ""
        try:
            for chunk in self._llm.chat_stream(msgs, tools=[]):
                if isinstance(chunk, TextDelta):
                    parts.append(chunk.text)
                    yield stream.emit("assistant.delta", text=chunk.text)
                elif isinstance(chunk, Final):
                    # The Final carries the assembled text; prefer it, since a
                    # provider may deliver an answer with no deltas at all.
                    answer = chunk.response.text or "".join(parts)
        except Exception as e:  # noqa: BLE001
            reason = f"{type(e).__name__}: {e}"
            if self._logger:
                self._logger.error("rag_agent.generate.failed", error=reason)
            # Past the first frame the 200 is committed, so the failure travels
            # in-band (§2.14.3) rather than as an HTTP error.
            yield stream.emit("error", stage="generation", detail=reason)
            return

        answer = answer or "".join(parts)
        self._record_turn(user_message, answer, metrics)
        yield stream.emit(
            "assistant.final", text=answer, sources=self._cited_paths(kept)
        )


__all__ = [
    "AgentBootstrapError",
    "RagAgent",
    "RagAgentDeps",
    "RetrievedChunk",
    "build_deps",
]
