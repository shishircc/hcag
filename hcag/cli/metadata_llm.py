"""LLM-driven metadata generation for packets and nodes.

Uses LiteLLM directly (provider-neutral); never imports vendor SDKs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..config import LLMConfig


@dataclass
class PacketMetadata:
    title: str
    short_description: str
    long_description: str


@dataclass
class NodeMetadata:
    node_title: str
    node_short_description: str


_PACKET_PROMPT = """You will be given a merged markdown document that forms a single knowledge packet.
Emit ONE compact JSON object with exactly these fields (no prose, no code fences):
  "title": short human-readable title (<=60 chars)
  "short_description": ONE line, no line breaks, <=180 chars
  "long_description": 2-4 sentences describing scope, key concepts, and when this packet is relevant

Content:
---
{content}
---"""


_NODE_PROMPT = """You will be given a list of short descriptions of child items in a knowledge taxonomy node.
Emit ONE compact JSON object with exactly these fields (no prose, no code fences):
  "node_title": short title summarizing the taxonomy branch (<=60 chars)
  "node_short_description": ONE line describing this branch (<=180 chars)

Children:
{children}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Strip code fences if the model added them despite instructions
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find first {...} block
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


def generate_packet_metadata(cfg: LLMConfig, content: str, max_chars: int = 20000) -> PacketMetadata:
    trimmed = content[:max_chars]
    raw = _complete(cfg, _PACKET_PROMPT.format(content=trimmed))
    data = _extract_json(raw)
    return PacketMetadata(
        title=str(data.get("title", "Untitled")).strip(),
        short_description=str(data.get("short_description", "")).replace("\n", " ").strip(),
        long_description=str(data.get("long_description", "")).strip(),
    )


def generate_node_metadata(cfg: LLMConfig, children_shorts: list[tuple[str, str]]) -> NodeMetadata:
    body = "\n".join(f"- {name}: {short}" for name, short in children_shorts)
    raw = _complete(cfg, _NODE_PROMPT.format(children=body))
    data = _extract_json(raw)
    return NodeMetadata(
        node_title=str(data.get("node_title", "Untitled")).strip(),
        node_short_description=str(data.get("node_short_description", "")).replace("\n", " ").strip(),
    )
