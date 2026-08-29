"""Per-row multi-turn conversation loop (§7.4).

Runs one CSV row through the chatbot under test, driving clarifications with
the LLM judge when needed, and returns a structured ``RowExchange`` that the
runner turns into a completed CSV row + JSON transcript.

The loop is intentionally synchronous and stateless (aside from the backend
session). ``concurrency`` is applied at the row level, one Python thread per
row — every LiteLLM/httpx call is blocking I/O so threads amortize well.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .backend import BackendClient, ChatSession, ChatTurn
from .config import EvalConfig
from .csv_io import EvalRow
from .llm_calls import (
    ClassifyResult,
    classify_response,
    generate_clarification,
    load_prompt,
)


@dataclass
class TranscriptTurn:
    role: str            # "user" | "bot"
    text: str
    source: str = "user"  # "user" (original question), "clarifier", "bot", "bot_final"
    elapsed_ms: float = 0.0
    http_status: int = 0


@dataclass
class RowExchange:
    row: EvalRow
    session_id: str
    turns: list[TranscriptTurn] = field(default_factory=list)
    turn_count: int = 0
    actual_answer: str = ""
    terminated_by: str = ""     # "answer" | "refusal" | "max_turns_exceeded" | "backend_error" | "backend_timeout" | "classifier_error"
    total_chat_ms: float = 0.0

    def transcript_text(self) -> str:
        """Render the transcript as a plain-text block for the judge prompt."""
        lines = []
        for t in self.turns:
            tag = t.role.upper()
            if t.source == "clarifier":
                tag = "USER (clarifier)"
            elif t.source == "user":
                tag = "USER"
            lines.append(f"{tag}: {t.text}")
        return "\n\n".join(lines)


def _mk_session_id(row_id: str, shared: str | None) -> str:
    if shared is not None:
        return shared
    # per-question: keep the row id in the session id so backend logs are cross-referenceable
    return f"eval-{row_id}-{uuid.uuid4().hex[:8]}"


def run_row(
    row: EvalRow,
    cfg: EvalConfig,
    *,
    shared_session_id: str | None = None,
) -> RowExchange:
    """Drive one row's conversation to completion.

    ``shared_session_id`` is set when ``run.session_scope == "per-run"`` — the
    caller allocates one session id up front and threads it through every row.
    """
    classify_prompt = load_prompt(cfg.classifier.prompt_path, "classify.md")
    clarify_prompt = load_prompt(cfg.judge.prompts.clarify, "clarify.md")

    session_id = _mk_session_id(row.question_id, shared_session_id)
    backend = BackendClient(
        url=cfg.backend.url,
        chat_path=cfg.backend.chat_path,
        request_timeout=cfg.backend.request_timeout,
        retries=cfg.backend.retries,
    )
    session = ChatSession(session_id=session_id)
    exchange = RowExchange(row=row, session_id=session_id)

    # Turn 1 is always the original question.
    next_user_text = row.question
    next_user_source = "user"

    for turn_idx in range(1, cfg.loop.max_turns + 1):
        exchange.turn_count = turn_idx
        exchange.turns.append(
            TranscriptTurn(role="user", text=next_user_text, source=next_user_source)
        )

        resp = backend.chat(session, next_user_text)
        exchange.total_chat_ms += resp.elapsed_ms

        if not resp.ok():
            # Hard backend failure — capture per §7.4.3 and terminate.
            code = "backend_timeout" if "timeout" in resp.error.lower() else "backend_error"
            exchange.actual_answer = f"[{code}] {resp.error}"
            exchange.terminated_by = code
            return exchange

        exchange.turns.append(
            TranscriptTurn(
                role="bot",
                text=resp.text,
                source="bot",
                elapsed_ms=resp.elapsed_ms,
                http_status=resp.http_status,
            )
        )

        cls = classify_response(
            llm=cfg.classifier.llm,
            prompt_template=classify_prompt,
            question=row.question,
            reply=resp.text,
        )

        if cls.category == "answer":
            exchange.actual_answer = resp.text
            exchange.terminated_by = "answer"
            _mark_final(exchange)
            return exchange
        if cls.category == "refusal":
            exchange.actual_answer = resp.text
            exchange.terminated_by = "refusal"
            _mark_final(exchange)
            return exchange

        # clarify — need another user turn (unless we're already at the limit).
        if turn_idx >= cfg.loop.max_turns:
            exchange.actual_answer = f"[max_turns_exceeded] last_response={resp.text!r}"
            exchange.terminated_by = "max_turns_exceeded"
            return exchange

        clarification = generate_clarification(
            llm=cfg.judge.llm,
            prompt_template=clarify_prompt,
            question=row.question,
            expected_answer=row.expected_answer,
            transcript=exchange.transcript_text(),
            last_reply=resp.text,
        )
        if clarification.error and not clarification.text:
            exchange.actual_answer = (
                f"[clarifier_failed] {clarification.error} | last_response={resp.text!r}"
            )
            exchange.terminated_by = "classifier_error"
            return exchange

        next_user_text = clarification.text
        next_user_source = "clarifier"

    # Should be unreachable — the for-loop's own guard returns first.
    return exchange


def _mark_final(exchange: RowExchange) -> None:
    """Tag the last bot turn as the final answer, for the report."""
    for t in reversed(exchange.turns):
        if t.role == "bot":
            t.source = "bot_final"
            break


# Convenience for tests + the promptfoo provider.
def build_session_history_from_turns(turns: list[TranscriptTurn]) -> list[ChatTurn]:
    return [ChatTurn(role=t.role, text=t.text) for t in turns if t.role in ("user", "bot")]
