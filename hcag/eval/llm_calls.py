"""Thin wrappers around the shared LiteLLM adapter for the three eval LLM
roles: classifier (§7.4.2), clarifier (§7.4.2), and judge (§7.5).

The three prompts are registry entries (`eval.classify`, `eval.clarify`,
`eval.score`) loaded like every other prompt in the system (D11, §2.15), so
this module names them and never contains their text.

All three parse structured JSON output. Malformed replies are retried up to
``retries`` times; persistent failure surfaces as a typed dataclass so the
caller can record it in ``remark`` without pretending the row succeeded.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Literal

from ..cli.metadata_llm import (
    LLMUnavailableError,
    check_credentials,
    classify,
    describe_failure,
)
from ..config import LLMConfig
from ..logger import HcagLogger
from ..prompting import PromptLibrary
from ..runtime.llm import LiteLLMAdapter, Message


Category = Literal["answer", "clarify", "refusal"]


@dataclass
class ClassifyResult:
    category: Category
    error: str = ""


@dataclass
class ClarifyResult:
    text: str
    error: str = ""


@dataclass
class JudgeResult:
    score: int | None
    remark: str
    error: str = ""


# --- JSON extraction (LLMs sometimes wrap in fences despite instructions) --

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    """Best-effort JSON extraction. Returns None if nothing parseable found."""
    if not text:
        return None
    stripped = text.strip()
    for candidate in (stripped, _find_first_json_object(stripped)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _find_first_json_object(text: str) -> str | None:
    m = _JSON_BLOCK_RE.search(text)
    return m.group(0) if m else None


# --- Chat helper ------------------------------------------------------------


def _chat_text(adapter: LiteLLMAdapter, prompt: str) -> str:
    """Single-message user prompt -> assistant text (no tools)."""
    msg = Message(role="user", content=prompt)
    return adapter.chat([msg], tools=[]).text or ""


# --- Public entry points ----------------------------------------------------


def classify_response(
    llm: LLMConfig,
    prompts: PromptLibrary,
    question: str,
    reply: str,
    retries: int = 1,
) -> ClassifyResult:
    """Classify a chatbot reply as answer / clarify / refusal."""
    adapter = LiteLLMAdapter(llm)
    prompt = prompts.get("eval.classify", question=question, reply=reply)
    last_err = ""
    for _ in range(retries + 1):
        try:
            raw = _chat_text(adapter, prompt)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            continue
        data = _extract_json(raw)
        if data and data.get("category") in ("answer", "clarify", "refusal"):
            return ClassifyResult(category=data["category"])  # type: ignore[arg-type]
        last_err = f"malformed_classifier_output: {raw[:120]!r}"
    # Fall back to `answer` so the row is judged rather than dropped (§7.4.3).
    return ClassifyResult(category="answer", error=last_err)


def generate_clarification(
    llm: LLMConfig,
    prompts: PromptLibrary,
    question: str,
    expected_answer: str,
    transcript: str,
    last_reply: str,
    retries: int = 1,
) -> ClarifyResult:
    """Play the user role in a clarifier turn (§7.4.2)."""
    adapter = LiteLLMAdapter(llm)
    prompt = prompts.get(
        "eval.clarify",
        question=question,
        expected_answer=expected_answer,
        transcript=transcript,
        last_reply=last_reply,
    )
    last_err = ""
    for _ in range(retries + 1):
        try:
            raw = _chat_text(adapter, prompt).strip()
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            continue
        if raw:
            return ClarifyResult(text=raw)
        last_err = "empty_clarifier_output"
    return ClarifyResult(text="", error=last_err)


def score_answer(
    llm: LLMConfig,
    prompts: PromptLibrary,
    question: str,
    expected_answer: str,
    actual_answer: str,
    transcript: str,
    retries: int = 2,
) -> JudgeResult:
    """Judge the actual answer against the expected answer (§7.5)."""
    adapter = LiteLLMAdapter(llm)
    prompt = prompts.get(
        "eval.score",
        question=question,
        expected_answer=expected_answer,
        actual_answer=actual_answer,
        transcript=transcript,
    )
    last_err = ""
    for _ in range(retries + 1):
        try:
            raw = _chat_text(adapter, prompt)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            continue
        data = _extract_json(raw)
        if not data:
            last_err = f"malformed_judge_output: {raw[:200]!r}"
            continue
        score = data.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or score not in (0, 1, 2, 3):
            last_err = f"invalid_score: {score!r}"
            continue
        remark = str(data.get("remark") or "").strip() or "(no remark)"
        return JudgeResult(score=score, remark=remark)

    # Per §7.5 — never fabricate a score. Leave empty, put reason in remark.
    return JudgeResult(score=None, remark=f"[judge_failed] {last_err}", error=last_err)


# --- Startup preflight (§7.3.1) --------------------------------------------

_PREFLIGHT_PROMPT = (
    "Reply with ONE compact JSON object and nothing else: "
    '{"score": 3, "remark": "ok"}'
)


def preflight(cfg: LLMConfig, role: str, logger: HcagLogger) -> None:
    """Prove one of the eval LLMs works before any row is run.

    `evalrun` spends real money on the *backend* before either of its own
    models is ever called: the loop drives the chatbot through up to
    `max_turns` per row, and the judge only runs afterwards. A bad judge key
    is therefore discovered after the entire run has been paid for, and the
    output is a CSV of `[judge_failed]` remarks — a shape that looks like a
    model-quality problem rather than a missing environment variable.

    Both roles are probed, because they are separately configured and often
    separately keyed (a cheap classifier, an expensive judge).

    Raises `LLMUnavailableError`. Nothing has been written when it fires.
    """
    # Label every failure with the role, including the credential check: a
    # missing key is the common case, and "which of my two models" is the whole
    # question the operator has when the message reaches them.
    try:
        check_credentials(cfg)
    except LLMUnavailableError as e:
        raise LLMUnavailableError(f"{role}: {e}") from e

    attempts = max(0, cfg.max_retries) + 1
    last: BaseException | None = None
    started = time.monotonic()

    for attempt in range(attempts):
        try:
            _extract_json(_chat_text(LiteLLMAdapter(cfg), _PREFLIGHT_PROMPT))
            logger.info(
                "evalrun.preflight.ok",
                role=role,
                provider=cfg.provider,
                model=cfg.litellm_model(),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return
        except Exception as e:  # noqa: BLE001
            last = e
            if classify(e) == "unavailable":
                raise LLMUnavailableError(f"{role}: {describe_failure(cfg, e)}") from e
            if attempt + 1 < attempts:
                logger.warn(
                    "evalrun.preflight.retry",
                    role=role,
                    attempt=attempt + 1,
                    of=attempts,
                    error=f"{type(e).__name__}: {e}",
                )
                continue

    raise LLMUnavailableError(f"{role}: {describe_failure(cfg, last)}")
