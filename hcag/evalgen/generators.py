"""Per-kind LLM question/expected-answer generation (§6.4, §6.6).

Each generator picks a source (packet / paragraphs / image) per the kind's
rules, calls the configured LLM with a fixed per-kind prompt, and returns a
`GeneratedItem`. Validation lives here — items that don't meet the kind's
constraints are rejected and the caller retries per `max_retries_per_item`.
"""

from __future__ import annotations

import base64
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal

import time

from ..config import LLMConfig
from ..logger import HcagLogger
from ..cli.metadata_llm import (
    LLMUnavailableError,
    check_credentials,
    classify,
    describe_failure,
)
from ..prompting import load_prompts
from .kb_scan import PacketRecord, taxonomy_prefix


Kind = Literal["simple", "medium", "complex", "hard-1", "hard-2"]

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


@dataclass
class GeneratedItem:
    kind: Kind
    question: str
    expected_answer: str
    source_packet_ids: list[str]
    #: Origin URLs the question was grounded in — packets first, in the order
    #: used, then images (§6.7.1). Empty entries are dropped rather than
    #: substituted: a `source` column that sometimes holds a local path is
    #: worse than one that is sometimes blank.
    source_urls: list[str] = field(default_factory=list)


class GenerationError(Exception):
    """Raised when the LLM output cannot be validated for its kind."""


# --- LLM plumbing ---------------------------------------------------------


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise



_LIB = None


def _rules(cfg) -> str:
    """The completeness standard every kind's answer must meet (§6.4).

    One file rather than five copies: the reference answer's quality bar is a
    single editorial decision, and duplicating it across kinds is how four of
    them silently drift from the fifth.
    """
    return _prompts(cfg).get("evalgen.answer_rules")


def _prompts(cfg):
    """Load evalgen's prompts once per process (D11, §2.15).

    Question wording is exactly the kind of thing a subject-matter expert
    should be able to tune — what counts as a "hard" question for work-pass
    rules is a domain judgement, not a code change.
    """
    global _LIB
    if _LIB is None:
        _LIB = load_prompts(getattr(cfg, "prompts_dir", None))
    return _LIB


def _complete(cfg: LLMConfig, content: list[dict[str, Any]] | str) -> str:
    import litellm

    if isinstance(content, str):
        messages = [{"role": "user", "content": content}]
    else:
        messages = [{"role": "user", "content": content}]
    resp = litellm.completion(
        model=cfg.litellm_model(),
        messages=messages,
        max_tokens=cfg.max_tokens,
        temperature=cfg.temperature,
        **({"api_base": cfg.endpoint} if cfg.endpoint else {}),
    )
    return resp.choices[0].message.content or ""


def _image_block(path) -> dict[str, Any]:
    ext = PurePosixPath(str(path)).suffix.lower()
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    data = path.read_bytes() if hasattr(path, "read_bytes") else open(path, "rb").read()
    b64 = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _parse_question_answer(raw: str) -> tuple[str, str]:
    data = _extract_json(raw)
    question = str(data.get("question", "")).strip()
    answer = str(data.get("expected_answer", "")).strip()
    if not question or not answer:
        raise GenerationError(f"missing question or expected_answer in LLM output: {raw!r}")
    return question, answer


# --- Prompt templates -----------------------------------------------------







# --- Selection helpers ----------------------------------------------------


def _choose_paragraphs(paragraphs: list[str], n: int, rng: random.Random) -> list[int]:
    """Pick `n` distinct paragraph indices; if fewer available, return all."""
    if len(paragraphs) <= n:
        return list(range(len(paragraphs)))
    return sorted(rng.sample(range(len(paragraphs)), n))


def _format_indexed_paragraphs(paragraphs: list[str], indices: list[int]) -> str:
    parts: list[str] = []
    for i in indices:
        parts.append(f"[paragraph {i}]\n{paragraphs[i]}")
    return "\n\n---\n\n".join(parts)


def _pair_packet(
    primary: PacketRecord,
    all_packets: list[PacketRecord],
    bias: str,
    rng: random.Random,
) -> PacketRecord | None:
    """Choose a second packet for hard-1. Prefers taxonomy siblings when bias=='taxonomy'."""
    others = [p for p in all_packets if p.id != primary.id and len(p.paragraphs) >= 1]
    if not others:
        return None
    if bias == "taxonomy":
        parent = taxonomy_prefix(primary.id)
        if parent:
            siblings = [p for p in others if taxonomy_prefix(p.id) == parent]
            if siblings:
                return rng.choice(siblings)
    return rng.choice(others)


# --- Content trimming ----------------------------------------------------


def _trim(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[...truncated...]"


# --- Per-kind generators --------------------------------------------------



#: Frequent words that carry no evidence, excluded from the grounding check so
#: connective prose in a complete answer cannot dilute it.
_STOPWORDS = frozenset(
    """that this then than with from into over under also more most must need
    when where which while what your their there these those they them will would
    should could been being have has had are was were the and for not but any all
    each per may can does depends applies apply based only same such other""".split()
)


def _check_grounded(answer: str, body: str) -> None:
    """Reject an expected answer whose facts are not in the packet.

    This replaced a verbatim-substring test. That test was not a grounding
    check at all — it was an *extraction* check, and it is what forced answers
    to be short: the only text guaranteed to appear literally in a packet is a
    fragment of it. A complete answer to a conditional question is assembled
    from several places and phrased to join them, so it can be perfectly
    grounded and appear nowhere verbatim (§6.4.1).

    What matters is that the answer invents nothing:

    - **Every number must come from the packet.** Figures are the facts an
      expected answer is most likely to get wrong and the ones a wrong answer
      does the most damage with, so this is exact and unforgiving.
    - **Most distinctive words must come from the packet**, ignoring
      connectives. A comprehensive answer reuses the source's terminology; one
      that does not is describing something else.
    """
    normalized_body = re.sub(r"\s+", " ", body).lower()
    normalized_answer = re.sub(r"\s+", " ", answer).lower()
    if not normalized_answer.strip():
        raise GenerationError("empty expected answer")

    # Trailing punctuation is part of the sentence, not the figure: "45." in
    # the packet and "45," in the answer are the same number.
    def _numbers(text: str) -> list[str]:
        return [n.rstrip(".,") for n in re.findall(r"\d[\d,.]*", text)]

    body_numbers = set(_numbers(normalized_body))
    invented = [n for n in _numbers(normalized_answer) if n not in body_numbers]
    if invented:
        raise GenerationError(
            f"expected answer cites numbers absent from the packet: {invented[:3]}"
        )

    # Calibrated against a real packet: a comprehensive conditional answer
    # scores 0.85, a short one 1.00, and an off-topic hallucination 0.50. The
    # floor on word count keeps a three-word answer from being judged on a
    # ratio that is mostly noise.
    words = [
        w for w in re.findall(r"[a-z]{5,}", normalized_answer) if w not in _STOPWORDS
    ]
    if len(words) >= 6:
        present = sum(1 for w in words if w in normalized_body)
        if present / len(words) < 0.6:
            raise GenerationError("expected answer is not grounded in the packet")


def gen_simple(
    cfg: LLMConfig,
    packet: PacketRecord,
    rng: random.Random,
    max_content_chars: int = 20000,
) -> GeneratedItem:
    content = _trim(packet.body, max_content_chars)
    prompt = _prompts(cfg).get("evalgen.simple", content=content, answer_rules=_rules(cfg))
    raw = _complete(cfg, prompt)
    question, answer = _parse_question_answer(raw)
    _check_grounded(answer, packet.body)
    return GeneratedItem(
        kind="simple",
        question=question,
        expected_answer=answer,
        source_packet_ids=[packet.id],
        source_urls=[u for u in [packet.url()] if u],
    )


def gen_medium(
    cfg: LLMConfig,
    packet: PacketRecord,
    rng: random.Random,
    max_paragraph_chars: int = 6000,
) -> GeneratedItem:
    idx = rng.randrange(len(packet.paragraphs))
    paragraph = _trim(packet.paragraphs[idx], max_paragraph_chars)
    prompt = _prompts(cfg).get(
        "evalgen.medium", packet_id=packet.id, paragraph=paragraph,
        answer_rules=_rules(cfg),
    )
    raw = _complete(cfg, prompt)
    question, answer = _parse_question_answer(raw)
    return GeneratedItem(
        kind="medium",
        question=question,
        expected_answer=answer,
        source_packet_ids=[packet.id],
        source_urls=[u for u in [packet.url()] if u],
    )


def gen_complex(
    cfg: LLMConfig,
    packet: PacketRecord,
    rng: random.Random,
) -> GeneratedItem:
    if len(packet.paragraphs) < 3:
        raise GenerationError(f"packet {packet.id} has <3 paragraphs")
    indices = _choose_paragraphs(packet.paragraphs, 3, rng)
    formatted = _format_indexed_paragraphs(packet.paragraphs, indices)
    prompt = _prompts(cfg).get(
        "evalgen.complex", packet_id=packet.id, paragraphs=formatted,
        answer_rules=_rules(cfg),
    )
    raw = _complete(cfg, prompt)
    data = _extract_json(raw)
    question = str(data.get("question", "")).strip()
    answer = str(data.get("expected_answer", "")).strip()
    cited = data.get("cited_paragraph_indices", [])
    if not question or not answer:
        raise GenerationError(f"missing question/expected_answer: {raw!r}")
    if not isinstance(cited, list) or len({int(i) for i in cited if isinstance(i, int)}) < 3:
        raise GenerationError(f"complex must cite >=3 distinct paragraphs, got {cited!r}")
    return GeneratedItem(
        kind="complex",
        question=question,
        expected_answer=answer,
        source_packet_ids=[packet.id],
        source_urls=[u for u in [packet.url()] if u],
    )


def gen_hard1(
    cfg: LLMConfig,
    packet: PacketRecord,
    all_packets: list[PacketRecord],
    bias: str,
    rng: random.Random,
) -> GeneratedItem:
    other = _pair_packet(packet, all_packets, bias, rng)
    if other is None:
        raise GenerationError("no second packet available for hard-1")

    # Ensure at least 3 paragraphs total across the two packets.
    total_available = len(packet.paragraphs) + len(other.paragraphs)
    if total_available < 3:
        raise GenerationError(f"pair {packet.id}+{other.id} has <3 paragraphs total")

    # Prefer 2 from primary + 1 from other, but fall back if primary too short.
    a_take = min(2, len(packet.paragraphs))
    b_take = min(3 - a_take, len(other.paragraphs))
    if a_take + b_take < 3:
        a_take = min(3 - b_take, len(packet.paragraphs))

    a_indices = _choose_paragraphs(packet.paragraphs, a_take, rng)
    b_indices = _choose_paragraphs(other.paragraphs, b_take, rng)
    prompt = _prompts(cfg).get(
        "evalgen.hard1",
        packet_a_id=packet.id,
        packet_b_id=other.id,
        paragraphs_a=_format_indexed_paragraphs(packet.paragraphs, a_indices),
        paragraphs_b=_format_indexed_paragraphs(other.paragraphs, b_indices),
        answer_rules=_rules(cfg),
    )
    raw = _complete(cfg, prompt)
    data = _extract_json(raw)
    question = str(data.get("question", "")).strip()
    answer = str(data.get("expected_answer", "")).strip()
    cited = data.get("cited_packet_ids", [])
    if not question or not answer:
        raise GenerationError(f"missing question/expected_answer: {raw!r}")
    cited_set = {str(c) for c in cited if isinstance(c, str)}
    if not {packet.id, other.id}.issubset(cited_set):
        raise GenerationError(f"hard-1 must cite both packets, got {cited!r}")
    return GeneratedItem(
        kind="hard-1",
        question=question,
        expected_answer=answer,
        source_packet_ids=[packet.id, other.id],
        # Packet A then packet B, matching the question's structure.
        source_urls=[u for u in [packet.url(), other.url()] if u],
    )


def gen_hard2(
    cfg: LLMConfig,
    packet: PacketRecord,
    rng: random.Random,
    max_content_chars: int = 15000,
) -> GeneratedItem:
    if not packet.assets:
        raise GenerationError(f"packet {packet.id} has no image assets")
    image_path = rng.choice(packet.assets)
    content = _trim(packet.body, max_content_chars)
    text_prompt = _prompts(cfg).get(
        "evalgen.hard2", packet_id=packet.id, content=content,
        answer_rules=_rules(cfg),
    )
    message_content = [
        {"type": "text", "text": text_prompt},
        _image_block(image_path),
    ]
    raw = _complete(cfg, message_content)
    data = _extract_json(raw)
    question = str(data.get("question", "")).strip()
    answer = str(data.get("expected_answer", "")).strip()
    image_ref = str(data.get("image_reference", "")).strip()
    if not question or not answer:
        raise GenerationError(f"missing question/expected_answer: {raw!r}")
    if not image_ref:
        raise GenerationError("hard-2 must state what the image contributes")
    return GeneratedItem(
        kind="hard-2",
        question=question,
        expected_answer=answer,
        source_packet_ids=[packet.id],
        # Packet first, then the image — for hard-2 the image *is* the
        # grounding, so which one was used is the difference between a
        # reviewable row and a mystery (§6.7.1).
        source_urls=[u for u in [packet.url(), packet.image_url(image_path)] if u],
    )


# --- Preflight (§6.2.2, mirroring §3.4.9) ---------------------------------


_PREFLIGHT_PROMPT = (
    "Reply with ONE compact JSON object and nothing else: "
    '{"title": "ok", "short_description": "ok", "long_description": "ok"}'
)


def preflight(cfg: LLMConfig, logger: HcagLogger, max_retries: int | None = None) -> None:
    """Prove the LLM works before generating anything.

    `evalgen` makes one call per question and writes the CSV at the end, so a
    bad key discovered on question 40 of 50 costs the whole run and every token
    spent on the 39 that succeeded. The probe is a real generation-shaped
    request against the configured model — env-var resolution, provider
    dispatch, model-id validity, auth, and whether the reply parses as the JSON
    the generators expect.

    Raises `LLMUnavailableError`. Nothing has been written when it fires.
    """
    check_credentials(cfg)
    attempts = max(0, cfg.max_retries if max_retries is None else max_retries) + 1
    last: BaseException | None = None
    started = time.monotonic()

    for attempt in range(attempts):
        try:
            _extract_json(_complete(cfg, _PREFLIGHT_PROMPT))
            logger.info(
                "evalgen.preflight.ok",
                provider=cfg.provider,
                model=cfg.litellm_model(),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return
        except Exception as e:  # noqa: BLE001
            last = e
            kind = classify(e)
            if kind == "unavailable":
                raise LLMUnavailableError(describe_failure(cfg, e)) from e
            if attempt + 1 < attempts:
                logger.warn(
                    "evalgen.preflight.retry",
                    attempt=attempt + 1,
                    of=attempts,
                    classification=kind,
                    error=f"{type(e).__name__}: {e}",
                )
                continue

    # A reply that will not parse is a model too small to follow the output
    # contract — far cheaper to learn now than on question 40.
    raise LLMUnavailableError(f"preflight reply was not usable — {describe_failure(cfg, last)}")
