"""Standalone Python module invoked by promptfoo per test (§7.6).

promptfoo spawns a worker per test case; each worker imports this module and
calls ``call_api(prompt, options, context)``. Everything the provider needs
from `evalrun.toml` is passed via ``HCAG_EVAL_CONFIG_JSON`` (a file path written
by the runner), so this file has no dependency on the CLI parsing layer.

Contract with promptfoo (v0.6+ Python provider protocol):

    def call_api(prompt: str, options: dict, context: dict) -> dict:
        # context["vars"] carries the row-specific test vars.
        return { "output": "<actual_answer>", "metadata": { ... } }
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

# NOTE: promptfoo loads this file via `file://<abs>` — outside its package
# context. That's why the imports are absolute: relative imports would raise
# `ImportError: attempted relative import with no known parent package`.
from hcag.eval.config import EvalConfig
from hcag.eval.csv_io import EvalRow
from hcag.eval.llm_calls import score_answer
from hcag.eval.progress import emit
from hcag.prompting import load_prompts
from hcag.eval.loop import RowExchange, run_row


_cfg_lock = threading.Lock()
_cached_cfg: EvalConfig | None = None
_cached_shared_session: str | None = None


def _load_cfg() -> EvalConfig:
    global _cached_cfg
    with _cfg_lock:
        if _cached_cfg is not None:
            return _cached_cfg
        path = os.environ.get("HCAG_EVAL_CONFIG_JSON", "")
        if not path or not os.path.isfile(path):
            raise RuntimeError(
                "HCAG_EVAL_CONFIG_JSON is not set or points at a missing file. "
                "The `eval` runner writes it before invoking promptfoo."
            )
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cached_cfg = EvalConfig.model_validate(data)
        # Optional shared session id for per-run scope.
        global _cached_shared_session
        _cached_shared_session = os.environ.get("HCAG_EVAL_SHARED_SESSION_ID") or None
        return _cached_cfg


def _row_from_context(context: dict[str, Any]) -> EvalRow:
    v = context.get("vars", {}) or {}
    return EvalRow(
        question_id=str(v.get("question_id", "")),
        kind=str(v.get("kind", "")),
        question=str(v.get("question", "")),
        expected_answer=str(v.get("expected_answer", "")),
    )


def call_api(prompt: str, options: dict, context: dict) -> dict:  # noqa: ARG001
    """Entry point promptfoo calls once per test row.

    Runs the multi-turn conversation loop, then scores the final answer with
    the LLM judge. Returns the actual_answer as ``output`` and the score /
    remark / transcript metadata for the runner + report to consume.
    """
    cfg = _load_cfg()
    row = _row_from_context(context)

    if not row.question:
        emit({"event": "row.done", "question_id": row.question_id,
              "kind": row.kind, "score": None, "error": "missing_question_var"})
        return {
            "output": "",
            "metadata": {
                "error": "missing_question_var",
                "score": None,
                "remark": "[eval_error] test row is missing the `question` var",
            },
        }

    scope = cfg.backend.session_scope
    shared = _cached_shared_session if scope == "per-run" else None

    started = time.monotonic()
    exchange: RowExchange = run_row(row, cfg, shared_session_id=shared)

    judge = score_answer(
        llm=cfg.judge.llm,
        prompts=load_prompts(cfg.prompts_dir),
        question=row.question,
        expected_answer=row.expected_answer,
        actual_answer=exchange.actual_answer,
        transcript=exchange.transcript_text(),
        retries=cfg.judge.retries,
    )

    # Report before returning: this is the only moment the parent can learn
    # that a row finished, and promptfoo does not surface per-test completion
    # until the whole run is done (§7.11.1).
    emit(
        {
            "event": "row.done",
            "question_id": row.question_id,
            "kind": row.kind,
            "score": judge.score,
            "turns": exchange.turn_count,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    )

    return {
        "output": exchange.actual_answer,
        "metadata": {
            "question_id": row.question_id,
            "kind": row.kind,
            "session_id": exchange.session_id,
            "turn_count": exchange.turn_count,
            "terminated_by": exchange.terminated_by,
            "score": judge.score,
            "remark": judge.remark,
            "judge_error": judge.error,
            "transcript": [
                {
                    "role": t.role,
                    "text": t.text,
                    "source": t.source,
                    "elapsed_ms": t.elapsed_ms,
                    "http_status": t.http_status,
                }
                for t in exchange.turns
            ],
            "total_chat_ms": exchange.total_chat_ms,
        },
    }
