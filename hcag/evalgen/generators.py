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
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from ..config import LLMConfig
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


_SIMPLE_PROMPT = """You are generating an FAQ-style evaluation question.

Task: Produce ONE question whose answer appears **verbatim** in the packet below (a sentence or short quoted phrase). No reasoning. No paraphrasing. The reader must be able to find the answer as a literal substring of the packet.

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<verbatim quote from the packet>"
}}

Packet content:
---
{content}
---"""


_MEDIUM_PROMPT = """You are generating a single-paragraph reasoning question.

Task: Produce ONE question whose answer requires **reasoning grounded in the paragraph below**. All supporting facts must appear in this one paragraph, but the answer must NOT be a direct quote — the reader must interpret or combine facts within the paragraph.

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<a short natural-language answer, not a quote>"
}}

Packet: {packet_id}
Paragraph:
---
{paragraph}
---"""


_COMPLEX_PROMPT = """You are generating a whole-packet reasoning question.

Task: Produce ONE question whose answer requires **significant deduction across at least three distinct concepts, each drawn from a different paragraph** shown below. The question must not be answerable from any single paragraph in isolation.

You will be given three or more paragraphs from the same packet. Your JSON must cite which paragraph each supporting concept came from, using the paragraph's index (0-based) as shown.

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<a synthesized answer combining all cited concepts>",
  "cited_paragraph_indices": [<int>, <int>, <int>, ...]
}}

Packet: {packet_id}
Paragraphs:
{paragraphs}"""


_HARD1_PROMPT = """You are generating a cross-packet reasoning question.

Task: Produce ONE question whose answer requires **two packets** to answer correctly, drawing on **at least three distinct paragraphs spread across those two packets**. Neither packet alone is sufficient.

Your JSON must cite which packet each supporting paragraph came from (by packet id).

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<a synthesized answer combining facts from both packets>",
  "cited_packet_ids": ["<packet_id_1>", "<packet_id_2>"]
}}

Packet A ({packet_a_id}) — paragraphs:
{paragraphs_a}

Packet B ({packet_b_id}) — paragraphs:
{paragraphs_b}"""


_HARD2_PROMPT = """You are generating a multimodal question.

Task: Produce ONE question whose answer **requires reading the attached image** together with the packet markdown. The key fact of the answer must be **visually present in the image** (a label on a diagram, a value in a chart, a state in a state-machine figure, a component in a screenshot) and NOT stated in the surrounding markdown alone. The question must not be answerable from the markdown by itself.

Return a single JSON object, no prose, no code fences:
{{
  "question": "<the question>",
  "expected_answer": "<a short answer whose key fact is in the image>",
  "image_reference": "<what in the image supports the answer, one sentence>"
}}

Packet: {packet_id}
Packet markdown:
---
{content}
---"""


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


def gen_simple(
    cfg: LLMConfig,
    packet: PacketRecord,
    rng: random.Random,
    max_content_chars: int = 20000,
) -> GeneratedItem:
    content = _trim(packet.body, max_content_chars)
    prompt = _SIMPLE_PROMPT.format(content=content)
    raw = _complete(cfg, prompt)
    question, answer = _parse_question_answer(raw)
    # Validation: the answer must appear verbatim in the packet (allowing
    # whitespace normalization).
    normalized_body = re.sub(r"\s+", " ", packet.body).lower()
    normalized_answer = re.sub(r"\s+", " ", answer).lower().strip('"').strip("'")
    if normalized_answer and normalized_answer not in normalized_body:
        # Try a looser check: at least 60% of answer's non-trivial tokens present
        tokens = [t for t in re.findall(r"\w+", normalized_answer) if len(t) > 3]
        if not tokens or sum(1 for t in tokens if t in normalized_body) / len(tokens) < 0.6:
            raise GenerationError("simple answer not found verbatim in packet")
    return GeneratedItem(
        kind="simple",
        question=question,
        expected_answer=answer,
        source_packet_ids=[packet.id],
    )


def gen_medium(
    cfg: LLMConfig,
    packet: PacketRecord,
    rng: random.Random,
    max_paragraph_chars: int = 6000,
) -> GeneratedItem:
    idx = rng.randrange(len(packet.paragraphs))
    paragraph = _trim(packet.paragraphs[idx], max_paragraph_chars)
    prompt = _MEDIUM_PROMPT.format(packet_id=packet.id, paragraph=paragraph)
    raw = _complete(cfg, prompt)
    question, answer = _parse_question_answer(raw)
    return GeneratedItem(
        kind="medium",
        question=question,
        expected_answer=answer,
        source_packet_ids=[packet.id],
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
    prompt = _COMPLEX_PROMPT.format(packet_id=packet.id, paragraphs=formatted)
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
    prompt = _HARD1_PROMPT.format(
        packet_a_id=packet.id,
        packet_b_id=other.id,
        paragraphs_a=_format_indexed_paragraphs(packet.paragraphs, a_indices),
        paragraphs_b=_format_indexed_paragraphs(other.paragraphs, b_indices),
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
    text_prompt = _HARD2_PROMPT.format(packet_id=packet.id, content=content)
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
    )
