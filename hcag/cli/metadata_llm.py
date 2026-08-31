"""LLM-driven folder metadata generation (§3.4.4).

Every folder — leaf, taxonomy node, mixed, or root — needs one summary record
(title, short_description, long_description) that its parent renders as an
entry in its ``## Sub-topics`` section. The same prompt handles all three
folder kinds: leaves are summarized from their own content, taxonomy nodes
from their children's shorts, and mixed folders from both.

Uses LiteLLM directly (provider-neutral); never imports vendor SDKs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..config import LLMConfig


@dataclass
class FolderMetadata:
    title: str
    short_description: str
    long_description: str


_PROMPT = """You will summarize one folder of a hierarchical knowledge base so
its parent's catalog can describe it.

The folder may include its own content, a list of child topics, or both.
Emit ONE compact JSON object with exactly these fields (no prose, no code fences):
  "title": short human-readable title (<=60 chars)
  "short_description": ONE line, no line breaks, <=180 chars
  "long_description": 2-4 sentences describing scope, key concepts, and when
                      this folder is relevant

{sections}"""


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


def _complete(cfg: LLMConfig, prompt: str) -> str:
    import litellm

    resp = litellm.completion(
        model=cfg.litellm_model(),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=cfg.max_tokens,
        temperature=0.0,
        **({"api_base": cfg.endpoint} if cfg.endpoint else {}),
    )
    return resp.choices[0].message.content or ""


def _compose_sections(own_content: str, children_shorts: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    if own_content.strip():
        parts.append("=== OWN CONTENT ===\n" + own_content.strip())
    if children_shorts:
        listing = "\n".join(f"- {cid}: {short}" for cid, short in children_shorts)
        parts.append("=== CHILD TOPICS ===\n" + listing)
    if not parts:
        parts.append("(empty folder — infer a placeholder summary from its identifier)")
    return "\n\n".join(parts)


def generate_folder_metadata(
    cfg: LLMConfig,
    *,
    own_content: str = "",
    children_shorts: list[tuple[str, str]] | None = None,
    max_content_chars: int = 20000,
) -> FolderMetadata:
    """Summarize one folder for its parent's catalog entry.

    ``own_content`` is the concatenated source markdown at this level (empty
    for pure taxonomy nodes). ``children_shorts`` is a list of ``(id, short)``
    tuples for the immediate children (empty for pure leaves).
    """
    trimmed = own_content[:max_content_chars]
    sections = _compose_sections(trimmed, list(children_shorts or []))
    raw = _complete(cfg, _PROMPT.format(sections=sections))
    data = _extract_json(raw)
    return FolderMetadata(
        title=str(data.get("title", "Untitled")).strip(),
        short_description=str(data.get("short_description", "")).replace("\n", " ").strip(),
        long_description=str(data.get("long_description", "")).strip(),
    )
