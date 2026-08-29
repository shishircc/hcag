"""Thin wrappers around the shared LiteLLM adapter for the three eval LLM
roles: classifier (§7.4.2), clarifier (§7.4.2), and judge (§7.5).

All three parse structured JSON output. Malformed replies are retried up to
``retries`` times; persistent failure surfaces as a typed dataclass so the
caller can record it in ``remark`` without pretending the row succeeded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

from ..config import LLMConfig
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


# --- Prompt loading ---------------------------------------------------------


def _read_packaged(name: str) -> str:
    return resources.files("hcag.eval.prompts").joinpath(name).read_text(encoding="utf-8")


def load_prompt(path: str, default_name: str) -> str:
    """Load a prompt template. Empty ``path`` falls back to the packaged default."""
    if not path:
        return _read_packaged(default_name)
    return Path(path).read_text(encoding="utf-8")


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
    prompt_template: str,
    question: str,
    reply: str,
    retries: int = 1,
) -> ClassifyResult:
    """Classify a chatbot reply as answer / clarify / refusal."""
    adapter = LiteLLMAdapter(llm)
    prompt = prompt_template.format(question=question, reply=reply)
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
    prompt_template: str,
    question: str,
    expected_answer: str,
    transcript: str,
    last_reply: str,
    retries: int = 1,
) -> ClarifyResult:
    """Play the user role in a clarifier turn (§7.4.2)."""
    adapter = LiteLLMAdapter(llm)
    prompt = prompt_template.format(
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
    prompt_template: str,
    question: str,
    expected_answer: str,
    actual_answer: str,
    transcript: str,
    retries: int = 2,
) -> JudgeResult:
    """Judge the actual answer against the expected answer (§7.5)."""
    adapter = LiteLLMAdapter(llm)
    prompt = prompt_template.format(
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
