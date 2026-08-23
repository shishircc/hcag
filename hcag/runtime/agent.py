"""AgentRuntime — the orchestrator (§2.9, §2.10.1)."""

from __future__ import annotations

import json
from typing import Any

from ..config import AgentConfig
from ..logger import HcagLogger, build_logger
from ..memory import FileSystemMemoryModule, LocalFsStorage, TokenBudget
from ..memory.module import MemoryModule
from ..models import CheckAndLoadRequest, Delta
from ..tracing import build_tracer
from .llm import LLM, LiteLLMAdapter, Message, ToolCall, packet_to_content_blocks


TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "get_catalog",
            "description": (
                "Return the full HCAG knowledge catalog. Usually not needed since the "
                "catalog is already in the system prompt; use only for re-inspection."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_and_load_kb",
            "description": (
                "Load additional knowledge packets from the KB. Call this ONLY when the "
                "currently-loaded packets are insufficient to answer. Pass the packet "
                "IDs you need in `requested_packet_ids` and the IDs you believe are "
                "currently active in `active_packet_ids`. Returns delta (loaded + evicted)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "context": {
                        "type": "string",
                        "description": "One-line description of what you need. Used for observability.",
                    },
                    "requested_packet_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Packet IDs to add to the active set.",
                    },
                    "active_packet_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Packet IDs you currently believe are active (LRU order, most recent last).",
                    },
                },
                "required": ["context", "requested_packet_ids", "active_packet_ids"],
            },
        },
    },
]


class AgentRuntime:
    """Owns the conversation loop; delegates KB access to the memory module."""

    def __init__(
        self,
        cfg: AgentConfig,
        memory: MemoryModule | None = None,
        llm: LLM | None = None,
        logger: HcagLogger | None = None,
    ) -> None:
        self.cfg = cfg
        self.logger = logger or build_logger(cfg.observability.log, name="hcag.runtime")
        self.tracer = build_tracer(cfg.observability.otel)
        if memory is None:
            storage = LocalFsStorage(cfg.kb_root)
            memory = FileSystemMemoryModule(
                storage=storage,
                budget=TokenBudget(cfg.max_active_tokens),
                logger=self.logger,
                tracer=self.tracer,
            )
        self.memory = memory
        self.llm = llm or LiteLLMAdapter(cfg.llm)
        self._system_prompt: str | None = None
        self._history: list[Message] = []
        self._turn_index = 0

    # ---- Bootstrap ------------------------------------------------------

    def bootstrap(self) -> None:
        catalog = self.memory.get_catalog()
        self._system_prompt = (
            f"{self.cfg.system_prompt_prefix}\n\n"
            f"--- KNOWLEDGE CATALOG ---\n{catalog.raw_markdown}\n--- END CATALOG ---"
        )
        self._history = [Message(role="system", content=self._system_prompt)]
        self.logger.info("agent.bootstrap", catalog_entries=len(catalog.entries))

    # ---- Turn loop ------------------------------------------------------

    def run_turn(self, user_message: str, max_tool_iters: int = 6) -> str:
        if self._system_prompt is None:
            self.bootstrap()

        self._turn_index += 1
        self.logger.info("turn.start", turn=self._turn_index, user_chars=len(user_message))

        self._history.append(Message(role="user", content=user_message))

        for _ in range(max_tool_iters):
            with self.tracer.start_as_current_span("gen_ai.chat"):
                response = self.llm.chat(self._history, tools=TOOL_DEFS)

            self._history.append(
                Message(role="assistant", content=response.text or None, tool_calls=response.tool_calls)
            )

            if not response.tool_calls:
                self.logger.info("turn.end", turn=self._turn_index, output_chars=len(response.text))
                return response.text

            for call in response.tool_calls:
                self._handle_tool_call(call)

        self.logger.warn("turn.tool_loop_exhausted", turn=self._turn_index)
        return self._history[-1].content if isinstance(self._history[-1].content, str) else ""

    # ---- Tool dispatch --------------------------------------------------

    def _handle_tool_call(self, call: ToolCall) -> None:
        if call.name == "get_catalog":
            catalog = self.memory.get_catalog()
            self._append_tool_result(call.id, [{"type": "text", "text": catalog.raw_markdown}])
            return

        if call.name == "check_and_load_kb":
            args = call.arguments or {}
            req = CheckAndLoadRequest(
                context=str(args.get("context", "")),
                requested_packet_ids=list(args.get("requested_packet_ids", []) or []),
                active_packet_ids=list(args.get("active_packet_ids", []) or []),
            )
            with self.tracer.start_as_current_span("tool.check_and_load_kb"):
                delta = self.memory.check_and_load_kb(req)
            self._append_tool_result(call.id, self._serialize_delta(delta))
            return

        self._append_tool_result(
            call.id,
            [{"type": "text", "text": f"Unknown tool: {call.name}"}],
        )

    def _append_tool_result(self, tool_call_id: str, blocks: list[dict[str, Any]]) -> None:
        # LiteLLM's tool-result role accepts a string or content-block list depending on
        # the provider; we send text for text-only results and blocks when images are present.
        has_images = any(b.get("type") == "image_url" for b in blocks)
        content: Any
        if has_images:
            content = blocks
        else:
            content = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        self._history.append(Message(role="tool", tool_call_id=tool_call_id, content=content))

    @staticmethod
    def _serialize_delta(delta: Delta) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        meta = {
            "loaded_ids": [p.id for p in delta.loaded],
            "evicted": delta.evicted,
            "active_after": delta.active_after,
            "errors": [{"id": e.packet_id, "reason": e.reason} for e in delta.errors],
        }
        blocks.append({"type": "text", "text": "DELTA-METADATA: " + json.dumps(meta)})
        for packet in delta.loaded:
            blocks.extend(packet_to_content_blocks(packet))
        return blocks
