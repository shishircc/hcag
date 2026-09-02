"""AgentRuntime — the orchestrator (§2.9, §2.10.1)."""

from __future__ import annotations

import json
from typing import Any, Iterator

from ..config import AgentConfig
from ..logger import HcagLogger, build_logger
from ..memory import FileSystemMemoryModule, LocalFsStorage, TokenBudget
from ..memory.module import MemoryModule
from ..models import CheckAndLoadRequest, Delta
from ..tracing import build_tracer, json_payload, messages_payload, set_attrs, truncate_middle
from .events import Event, EventStream
from .llm import (
    LLM,
    Final,
    LiteLLMAdapter,
    Message,
    TextDelta,
    ToolCall,
    packet_to_content_blocks,
    stream_or_buffer,
)


def _messages_for_trace(history: list[Message], max_message_chars: int) -> list[dict[str, Any]]:
    """Render conversation history for a trace payload.

    Two things are deliberately not shipped: base64 image data, which would be
    megabytes per span, and the exact byte-for-byte tool results, which are
    already visible as the packet ids on the tool span. Each is replaced by a
    short marker so the shape of the conversation still reads correctly.
    """
    out: list[dict[str, Any]] = []
    for m in history:
        content = m.content
        if isinstance(content, list):
            parts = []
            for block in content:
                if block.get("type") == "image_url":
                    parts.append("[image]")
                else:
                    parts.append(block.get("text", ""))
            content = "\n".join(parts)
        if isinstance(content, str):
            # Middle-out: for a packet the identifying head and the trailing
            # detail are both load-bearing (§2.11.2).
            content = truncate_middle(content, max_message_chars)
        entry: dict[str, Any] = {"role": m.role, "content": content}
        if m.tool_calls:
            entry["tool_calls"] = [
                {"name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls
            ]
        out.append(entry)
    return out


def _response_for_trace(response) -> dict[str, Any]:
    return {
        "content": response.text,
        "tool_calls": [
            {"name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls
        ],
    }


TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "get_catalog",
            "description": (
                "Return the full HCAG knowledge catalog. You should not need this: the "
                "catalog in your system prompt already indexes every folder in the KB "
                "at every depth. Provided only for re-inspection."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_and_load_kb",
            "description": (
                "Loads a packet's actual content. The catalog in your system prompt "
                "is an index: it tells you WHICH packet to load and is never itself an "
                "answer, so a question the catalog appears to describe is a question "
                "you must load a packet to answer. That said, MOST TURNS NEED NO CALL: "
                "it acquires knowledge you do not have; it is not an acknowledgement of "
                "a turn, not a refresh, and not a way to confirm what is loaded. Before "
                "calling, check in order: "
                "(1) can you answer from the CONTENT of packets already loaded? Then "
                "answer — do not call. (2) Is the material inside a packet already in this "
                "conversation? Then re-read it — do not call; a loaded packet never "
                "needs re-requesting. (3) Only if the catalog names an entry that "
                "covers the gap AND that entry is absent from your active set, call "
                "with exactly those ids. Requesting ids that are already active is an "
                "error: it loads nothing, costs a round trip, and disturbs eviction "
                "order. Request the deepest matching ids straight from the catalog — "
                "you never need to load a parent folder on the way to a child, and one "
                "call can carry ids from several branches. Pass the ids you need in "
                "`requested_packet_ids` and the ids you believe are currently active in "
                "`active_packet_ids`. Returns delta (loaded + evicted)."
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
        session_id: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.session_id = session_id
        self.logger = logger or build_logger(cfg.observability.log, name="hcag.runtime")
        # Pass the whole observability block so either trace-destination form
        # is honored (§2.11.1); a missing Langfuse key raises here, at startup.
        self.tracer = build_tracer(cfg.observability, logger=self.logger)
        if memory is None:
            storage = LocalFsStorage(cfg.kb_root)
            memory = FileSystemMemoryModule(
                storage=storage,
                budget=TokenBudget(cfg.max_active_tokens),
                logger=self.logger,
                tracer=self.tracer,
                strip_subtopics_on_load=cfg.catalog.strip_subtopics_on_load,
            )
        self.memory = memory
        self.llm = llm or LiteLLMAdapter(cfg.llm)
        self._system_prompt: str | None = None
        self._history: list[Message] = []
        self._turn_index = 0
        # Reload discipline counters (§2.7.1). `redundant_rate` is the number
        # that says whether the discipline is holding; healthy is at or near 0.
        self._reload_calls = 0
        self._redundant_reloads = 0
        # Mirrors the delta's authoritative `active_after` so `assistant.final`
        # can report what the turn ended up holding (§2.14.1).
        self._active_ids: list[str] = []

    # ---- Bootstrap ------------------------------------------------------

    def bootstrap(self) -> None:
        catalog = self.memory.get_catalog()
        # The delimiter states the catalog's role again, right where the model
        # reads it: an index whose descriptions route, never evidence that
        # answers. A block of plausible prose is otherwise easy to mistake for
        # source material.
        self._system_prompt = (
            f"{self.cfg.system_prompt_prefix}\n\n"
            "--- KNOWLEDGE CATALOG (INDEX ONLY — every folder, all depths) ---\n"
            "The entries below are routing metadata. Use them to choose packet "
            "ids to load. Do NOT answer any question from the text below; "
            "answers come only from the ## Content of packets you have loaded.\n\n"
            f"{catalog.raw_markdown}\n"
            "--- END CATALOG (nothing above is a source) ---"
        )
        self._history = [Message(role="system", content=self._system_prompt)]
        self.logger.info(
            "agent.bootstrap",
            catalog_entries=len(catalog.entries),
            catalog_max_depth=max((e.depth for e in catalog.entries), default=0),
            catalog_chars=len(catalog.raw_markdown),
        )

    # ---- Turn loop ------------------------------------------------------

    def run_turn(self, user_message: str, max_tool_iters: int = 6) -> str:
        """The finished answer.

        Drains `run_turn_stream` rather than duplicating the loop (§2.14.2):
        one turn implementation, so the synchronous and streaming paths cannot
        grow different behaviour around tool loops, eviction, or history.
        """
        answer = ""
        for event in self.run_turn_stream(user_message, max_tool_iters=max_tool_iters):
            if event.kind == "assistant.final":
                answer = event.data.get("text", "")
            elif event.kind == "error":
                raise RuntimeError(event.data.get("detail", "turn failed"))
        return answer

    def run_turn_stream(
        self, user_message: str, max_tool_iters: int = 6
    ) -> "Iterator[Event]":
        """Yield §2.14.1 events as the turn happens — the primitive."""
        if self._system_prompt is None:
            self.bootstrap()

        self._turn_index += 1
        self.logger.info("turn.start", turn=self._turn_index, user_chars=len(user_message))

        stream = EventStream(turn_id=f"t_{self._turn_index}")
        self._history.append(Message(role="user", content=user_message))

        # One root span per turn, so a trace is a conversation turn rather than
        # a loose pile of LLM calls (§2.11.2).
        with self.tracer.start_as_current_span("conversation.turn") as turn_span:
            set_attrs(turn_span, self._turn_attrs(user_message))
            yield stream.emit("assistant.start")
            answer = ""
            try:
                for event in self._run_tool_loop(stream, max_tool_iters):
                    if event.kind == "assistant.final":
                        answer = event.data.get("text", "")
                    yield event
            except Exception as e:  # noqa: BLE001
                # Past the first event the HTTP status is already sent, so a
                # failure has to travel in-band (§2.14.3).
                self.logger.error(
                    "turn.failed", turn=self._turn_index, error=f"{type(e).__name__}: {e}"
                )
                yield stream.emit("error", detail=f"{type(e).__name__}: {e}")
                return
            set_attrs(
                turn_span,
                {
                    "hcag.turn.reload_calls": self._reload_calls,
                    "hcag.turn.redundant_reloads": self._redundant_reloads,
                    **self._content_attrs(inp=user_message, out=answer),
                },
            )

    def _run_tool_loop(self, stream: EventStream, max_tool_iters: int) -> "Iterator[Event]":
        for _ in range(max_tool_iters):
            response = None
            for chunk in self._chat_stream():
                if isinstance(chunk, TextDelta):
                    yield stream.emit("assistant.delta", text=chunk.text)
                elif isinstance(chunk, Final):
                    response = chunk.response
            if response is None:
                raise RuntimeError("LLM stream closed without a final response")

            self._history.append(
                Message(role="assistant", content=response.text or None, tool_calls=response.tool_calls)
            )

            if not response.tool_calls:
                self.logger.info(
                    "turn.end",
                    turn=self._turn_index,
                    output_chars=len(response.text),
                    reload_calls=self._reload_calls,
                    redundant_reloads=self._redundant_reloads,
                    redundant_rate=round(self._redundant_reloads / self._turn_index, 3),
                )
                yield stream.emit(
                    "assistant.final", text=response.text, active_after=list(self._active_ids)
                )
                return

            for call in response.tool_calls:
                yield from self._handle_tool_call(call, stream)

        self.logger.warn("turn.tool_loop_exhausted", turn=self._turn_index)
        tail = self._history[-1].content if isinstance(self._history[-1].content, str) else ""
        yield stream.emit("assistant.final", text=tail, active_after=list(self._active_ids))

    # ---- Tracing --------------------------------------------------------

    def _turn_attrs(self, user_message: str) -> dict[str, Any]:
        return {
            "hcag.turn.index": self._turn_index,
            "hcag.user.message.chars": len(user_message),
            # Langfuse groups traces sharing a session id into one conversation.
            "langfuse.session.id": self.session_id,
            "session.id": self.session_id,
        }

    def _content_attrs(self, inp: Any = None, out: Any = None) -> dict[str, Any]:
        """Input/output payloads, when content capture is enabled (§2.11.2)."""
        obs = self.cfg.observability
        if not obs.capture_content:
            return {}
        attrs: dict[str, Any] = {}
        if inp is not None:
            attrs["langfuse.observation.input"] = json_payload(inp, obs.max_content_chars)
        if out is not None:
            attrs["langfuse.observation.output"] = json_payload(out, obs.max_content_chars)
        return attrs

    def _prompt_attr(self) -> dict[str, Any]:
        """The conversation as it was sent, for a `gen_ai.chat` span.

        Uses the message-aware serializer so an oversized prompt sheds whole
        old messages instead of having its tail — the loaded packets — cut off
        by a character budget (§2.11.2).
        """
        obs = self.cfg.observability
        if not obs.capture_content:
            return {}
        rendered = _messages_for_trace(self._history, obs.max_message_chars)
        return {
            "langfuse.observation.input": messages_payload(rendered, obs.max_content_chars),
            "gen_ai.request.messages": len(self._history),
        }

    def _chat_stream(self):
        """One LLM call as a stream, with the span populated on both sides.

        Request attributes go on before the call so a failed call still carries
        the model and parameters; usage and output go on after the stream
        closes. `stream_or_buffer` means a non-streaming binding still arrives
        here as a stream (§2.14).
        """
        llm_cfg = self.cfg.llm
        with self.tracer.start_as_current_span("gen_ai.chat") as span:
            set_attrs(
                span,
                {
                    # Marks this as a generation in Langfuse; inert elsewhere.
                    "langfuse.observation.type": "generation",
                    "gen_ai.operation.name": "chat",
                    "gen_ai.system": llm_cfg.provider,
                    "gen_ai.request.model": llm_cfg.litellm_model(),
                    "gen_ai.request.max_tokens": llm_cfg.max_tokens,
                    "gen_ai.request.temperature": llm_cfg.temperature,
                    "langfuse.session.id": self.session_id,
                    **self._prompt_attr(),
                },
            )
            response = None
            for chunk in stream_or_buffer(self.llm, self._history, TOOL_DEFS):
                if isinstance(chunk, Final):
                    response = chunk.response
                yield chunk
            if response is None:
                return
            set_attrs(
                span,
                {
                    "gen_ai.response.model": getattr(response, "model", "") or None,
                    **{
                        f"gen_ai.usage.{k}": v
                        for k, v in (getattr(response, "usage", None) or {}).items()
                    },
                    "gen_ai.response.tool_calls": len(response.tool_calls),
                    **self._content_attrs(out=_response_for_trace(response)),
                },
            )

    # ---- Tool dispatch --------------------------------------------------

    def _handle_tool_call(self, call: ToolCall, stream: EventStream) -> "Iterator[Event]":
        """Dispatch one tool call, always inside a span.

        The span is opened here, around the dispatch, rather than inside each
        branch. Instrumenting only the branches we care about means a
        `get_catalog` call — or a hallucinated tool name — produces no span at
        all, and the trace shows a turn that appears to have done nothing
        between two LLM calls. Every tool call leaves a trace, or the trace
        cannot be trusted to show what the turn did.
        """
        args = call.arguments or {}
        # Which packets a turn chose is the most interesting thing about it, so
        # the client learns before the load, not after (§2.14.1).
        yield stream.emit(
            "tool.start",
            tool=call.name,
            requested=list(args.get("requested_packet_ids", []) or []),
            context=str(args.get("context", ""))[:512],
        )
        with self.tracer.start_as_current_span(f"tool.{call.name}") as span:
            set_attrs(
                span,
                {
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": call.name,
                    "langfuse.session.id": self.session_id,
                    **self._content_attrs(inp=args),
                },
            )
            outcome = self._dispatch_tool_call(call, span)
        yield stream.emit("tool.end", tool=call.name, **outcome)

    def _dispatch_tool_call(self, call: ToolCall, span: Any) -> dict[str, Any]:
        if call.name == "get_catalog":
            catalog = self.memory.get_catalog()
            # §2.12 item 6: the injected catalog is complete, so a mid-session
            # read is wasted context. Surface it rather than serving it silently.
            self.logger.warn(
                "get_catalog.mid_session",
                turn=self._turn_index,
                reason="catalog is already complete in the system prompt",
            )
            set_attrs(
                span,
                {
                    "hcag.tool.unnecessary": True,
                    "hcag.catalog.entries": len(catalog.entries),
                    **self._content_attrs(out=catalog.raw_markdown),
                },
            )
            self._append_tool_result(call.id, [{"type": "text", "text": catalog.raw_markdown}])
            return {"entries": len(catalog.entries), "unnecessary": True}

        if call.name == "check_and_load_kb":
            args = call.arguments or {}
            req = CheckAndLoadRequest(
                context=str(args.get("context", "")),
                requested_packet_ids=list(args.get("requested_packet_ids", []) or []),
                active_packet_ids=list(args.get("active_packet_ids", []) or []),
            )
            set_attrs(
                span,
                {
                    "hcag.tool.requested_ids": ",".join(req.requested_packet_ids),
                    "hcag.tool.active_ids_in": ",".join(req.active_packet_ids),
                    "hcag.tool.context": req.context[:512],
                },
            )
            delta = self.memory.check_and_load_kb(req)
            set_attrs(
                span,
                {
                    "hcag.tool.loaded_ids": ",".join(p.id for p in delta.loaded),
                    "hcag.tool.evicted_ids": ",".join(delta.evicted),
                    "hcag.tool.active_ids_after": ",".join(delta.active_after),
                    "hcag.tool.redundant": delta.redundant,
                    "hcag.tool.errors": len(delta.errors),
                    **self._content_attrs(
                        out={
                            "loaded": [p.id for p in delta.loaded],
                            "evicted": delta.evicted,
                            "active_after": delta.active_after,
                            "redundant": delta.redundant,
                            "note": delta.note,
                            "errors": [
                                {"id": e.packet_id, "reason": e.reason}
                                for e in delta.errors
                            ],
                        }
                    ),
                },
            )
            self._reload_calls += 1
            if delta.redundant:
                self._redundant_reloads += 1
            self._active_ids = list(delta.active_after)
            self._append_tool_result(call.id, self._serialize_delta(delta))
            return {
                "loaded": [p.id for p in delta.loaded],
                "evicted": list(delta.evicted),
                "active_after": list(delta.active_after),
                "redundant": delta.redundant,
                "errors": len(delta.errors),
            }

        # A name we do not serve. Traced and logged rather than silently
        # answered with an error string the model may or may not act on.
        # NB: `name` is a reserved LogRecord field — passing it as an extra
        # raises inside logging and would crash the turn.
        self.logger.warn("tool.unknown", turn=self._turn_index, tool_name=call.name)
        message = f"Unknown tool: {call.name}"
        set_attrs(span, {"hcag.tool.unknown": True, **self._content_attrs(out=message)})
        try:
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, message))
        except Exception:  # noqa: BLE001 — telemetry must not fail a turn
            pass
        self._append_tool_result(call.id, [{"type": "text", "text": message}])
        return {"unknown": True}

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
        if delta.note:
            # The model reads this in-conversation, which corrects a reflex call
            # for the rest of the session in a way a prompt rule alone does not
            # (§2.7.1, enforcement layer 3).
            blocks.append({"type": "text", "text": "NOTE: " + delta.note})
        for packet in delta.loaded:
            blocks.extend(packet_to_content_blocks(packet))
        return blocks
