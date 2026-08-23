"""LLM abstraction — thin adapter over LiteLLM.

Design constraint (§2.13.2, §2.13.8): the implementation must NOT import
`anthropic`, `openai`, or `boto3` at the LLM call site. LiteLLM handles all
provider dispatch and tool-format normalization.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import LLMConfig
from ..models import ImageBlock, Packet, TextBlock


# --- Wire types -----------------------------------------------------------


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    tool_call_id: str
    content_blocks: list[dict[str, Any]]


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | list[dict[str, Any]] | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    raw: Any = None


# --- Protocol -------------------------------------------------------------


class LLM(Protocol):
    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse: ...


# --- LiteLLM adapter ------------------------------------------------------


def _msg_to_openai_dict(m: Message) -> dict[str, Any]:
    """Translate to the OpenAI-style dict that LiteLLM normalizes across providers."""
    if m.role == "tool":
        # LiteLLM expects a role="tool" message with `tool_call_id` and text content.
        # We flatten multimodal blocks into a single content payload; LiteLLM
        # routes images through provider-appropriate channels.
        return {
            "role": "tool",
            "tool_call_id": m.tool_call_id,
            "content": m.content if isinstance(m.content, list) else (m.content or ""),
        }
    payload: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": _dumps_args(tc.arguments),
                },
            }
            for tc in m.tool_calls
        ]
    return payload


def _dumps_args(args: dict[str, Any]) -> str:
    import json

    return json.dumps(args, ensure_ascii=False)


def _loads_args(raw: str | dict[str, Any]) -> dict[str, Any]:
    import json

    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


class LiteLLMAdapter:
    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg
        # Ensure the configured credential env var is present at construction time
        # for providers that need one (Bedrock uses the AWS chain, not this).
        if cfg.provider == "anthropic" and not os.environ.get(cfg.api_key_env):
            # Don't hard-fail — LiteLLM will raise a clear error on first call.
            pass

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        import litellm

        payload = {
            "model": self.cfg.litellm_model(),
            "messages": [_msg_to_openai_dict(m) for m in messages],
            "max_tokens": self.cfg.max_tokens,
            "temperature": self.cfg.temperature,
        }
        if tools:
            payload["tools"] = tools
        if self.cfg.endpoint:
            payload["api_base"] = self.cfg.endpoint

        raw = litellm.completion(**payload)

        choice = raw.choices[0]
        msg = choice.message
        text = getattr(msg, "content", None) or ""
        tool_calls_raw = getattr(msg, "tool_calls", None) or []
        tool_calls: list[ToolCall] = []
        for tc in tool_calls_raw:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", "") if fn else ""
            args = getattr(fn, "arguments", "") if fn else ""
            tool_calls.append(
                ToolCall(
                    id=getattr(tc, "id", "") or "",
                    name=name,
                    arguments=_loads_args(args),
                )
            )
        return LLMResponse(text=text, tool_calls=tool_calls, raw=raw)


# --- Content-block helpers for tool results ------------------------------


def packet_to_content_blocks(packet: Packet) -> list[dict[str, Any]]:
    """Serialize a Packet into OpenAI/Anthropic-compatible content blocks."""
    out: list[dict[str, Any]] = []
    for block in packet.content:
        if isinstance(block, TextBlock):
            out.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageBlock):
            b64 = base64.b64encode(block.data).decode("ascii")
            out.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{block.mime_type};base64,{b64}"},
                }
            )
    return out
