"""Per-room voice session (§5.5).

`VoiceSession` binds together the pieces that come alive when a LiveKit
room joins:

- an `AgentRuntime` (already bootstrapped and, ideally, warm-started),
- an STT adapter that streams user audio → text events,
- a TTS adapter that streams assistant text → audio frames,
- a `TranscriptionPublisher` mirroring both sides onto `hcag.transcription`.

The session exposes callback-shaped hooks (`on_user_partial`, `on_user_final`,
`on_llm_delta`, `on_llm_final`, `cancel_current_turn`) so the LiveKit worker
can wire it into whichever event scheme the plugin runtime uses without
this module having to depend on `livekit-agents` at import time.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..logger import HcagLogger
from ..runtime.agent import AgentRuntime
from .config import VoiceAgentConfig
from .transcription import TranscriptionPublisher


@dataclass
class TurnMetrics:
    turn_id: str
    started_at: float
    first_token_at: float | None = None
    first_audio_at: float | None = None
    ended_at: float | None = None
    interrupted: bool = False
    user_final: str = ""
    assistant_final: str = ""


@dataclass
class SessionState:
    turn_index: int = 0
    open_turn: TurnMetrics | None = None
    completed_turns: list[TurnMetrics] = field(default_factory=list)


class VoiceSession:
    """State machine for a single LiveKit room.

    The class is transport-agnostic. Hooks are async because the transcription
    publisher and the underlying LiveKit publish path are async — nothing
    heavier is required of the caller.
    """

    def __init__(
        self,
        cfg: VoiceAgentConfig,
        runtime: AgentRuntime,
        publisher: TranscriptionPublisher,
        logger: HcagLogger,
        *,
        stt: Any = None,
        tts: Any = None,
    ) -> None:
        self.cfg = cfg
        self.runtime = runtime
        self.publisher = publisher
        self.logger = logger
        self.stt = stt
        self.tts = tts
        self.state = SessionState()
        self._session_id = uuid.uuid4().hex[:8]

    # ---- Lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Announce readiness to the browser (§5.4.3 final step)."""
        await self.publisher.emit("system.ready", session_id=self._session_id)
        self.logger.info("voice.session.opened", session_id=self._session_id)

    async def stop(self, reason: str = "closed") -> None:
        if self.state.open_turn is not None:
            await self.cancel_current_turn()
        self.logger.info(
            "voice.session.closed",
            session_id=self._session_id,
            reason=reason,
            turns=len(self.state.completed_turns),
        )
        if self.tts is not None and hasattr(self.tts, "close"):
            try:
                self.tts.close()
            except Exception:  # noqa: BLE001
                pass
        if self.stt is not None and hasattr(self.stt, "close"):
            try:
                self.stt.close()
            except Exception:  # noqa: BLE001
                pass

    # ---- STT callbacks --------------------------------------------------

    async def on_user_partial(self, text: str) -> None:
        turn_id = self._current_turn_id()
        await self.publisher.emit("user.partial", turn_id=turn_id, text=text)

    async def on_user_final(self, text: str) -> None:
        # A new final while a turn is still open == barge-in. Cancel first.
        if self.state.open_turn is not None:
            await self.cancel_current_turn()
        self._open_turn(user_final=text)
        turn_id = self.state.open_turn.turn_id  # type: ignore[union-attr]
        await self.publisher.emit("user.final", turn_id=turn_id, text=text)
        await self._drive_llm_turn(text)

    # ---- LLM streaming callbacks ---------------------------------------

    async def on_llm_delta(self, text: str) -> None:
        if self.state.open_turn is None:
            return
        if self.state.open_turn.first_token_at is None:
            self.state.open_turn.first_token_at = time.perf_counter()
        turn_id = self.state.open_turn.turn_id
        await self.publisher.emit("assistant.delta", turn_id=turn_id, text=text)
        if self.tts is not None and hasattr(self.tts, "push_text"):
            try:
                await _maybe_await(self.tts.push_text(text))
            except Exception as e:  # noqa: BLE001
                self.logger.warn("voice.tts.push_failed", turn_id=turn_id, error=str(e))
        if self.state.open_turn.first_audio_at is None and self.tts is not None:
            self.state.open_turn.first_audio_at = time.perf_counter()

    async def on_llm_final(self, text: str) -> None:
        if self.state.open_turn is None:
            return
        turn_id = self.state.open_turn.turn_id
        self.state.open_turn.assistant_final = text
        self.state.open_turn.ended_at = time.perf_counter()
        await self.publisher.emit("assistant.final", turn_id=turn_id, text=text)
        if self.tts is not None and hasattr(self.tts, "flush"):
            try:
                await _maybe_await(self.tts.flush())
            except Exception as e:  # noqa: BLE001
                self.logger.warn("voice.tts.flush_failed", turn_id=turn_id, error=str(e))
        self._close_turn()

    # ---- Barge-in / cancellation ---------------------------------------

    async def cancel_current_turn(self) -> None:
        if self.state.open_turn is None:
            return
        turn = self.state.open_turn
        turn.interrupted = True
        turn.ended_at = time.perf_counter()
        await self.publisher.emit("assistant.interrupted", turn_id=turn.turn_id)
        if self.tts is not None and hasattr(self.tts, "cancel"):
            try:
                await _maybe_await(self.tts.cancel())
            except Exception as e:  # noqa: BLE001
                self.logger.warn("voice.tts.cancel_failed", turn_id=turn.turn_id, error=str(e))
        self._close_turn()

    # ---- Internal -------------------------------------------------------

    def _current_turn_id(self) -> str:
        if self.state.open_turn is not None:
            return self.state.open_turn.turn_id
        # Partials that arrive before any final live on a provisional turn id.
        return f"t_{self._session_id}_{self.state.turn_index + 1}_partial"

    def _open_turn(self, *, user_final: str) -> None:
        self.state.turn_index += 1
        turn = TurnMetrics(
            turn_id=f"t_{self._session_id}_{self.state.turn_index}",
            started_at=time.perf_counter(),
            user_final=user_final,
        )
        self.state.open_turn = turn

    def _close_turn(self) -> None:
        turn = self.state.open_turn
        if turn is None:
            return
        self.state.open_turn = None
        self.state.completed_turns.append(turn)
        self.logger.info(
            "voice.turn.completed",
            turn_id=turn.turn_id,
            interrupted=turn.interrupted,
            user_final_chars=len(turn.user_final),
            assistant_final_chars=len(turn.assistant_final),
            first_token_ms=_ms(turn.started_at, turn.first_token_at),
            first_audio_ms=_ms(turn.started_at, turn.first_audio_at),
            total_ms=_ms(turn.started_at, turn.ended_at),
        )

    async def _drive_llm_turn(self, user_text: str) -> None:
        """Run the AgentRuntime turn.

        The base `AgentRuntime.run_turn` is synchronous and returns the final
        assistant string. We surface it as a single `assistant.final` (there
        is no per-token stream on the base runtime). A streaming variant
        can subclass this class and override this method to invoke a
        streaming LLM path — the transcription/TTS wiring above is already
        delta-based.
        """
        try:
            reply = self.runtime.run_turn(user_text)
        except Exception as e:  # noqa: BLE001
            turn_id = self.state.open_turn.turn_id if self.state.open_turn else "unknown"
            self.logger.error("voice.turn.failed", turn_id=turn_id, error=str(e))
            await self.publisher.emit("system.error", turn_id=turn_id, reason=str(e))
            self._close_turn()
            return
        await self.on_llm_final(reply)


async def _maybe_await(value: Any) -> Any:
    """Await if the return value is awaitable; otherwise pass through.

    STT/TTS plugin surfaces vary — some methods are sync, some return
    coroutines. This shim lets us call them uniformly.
    """
    if hasattr(value, "__await__"):
        return await value
    return value


def _ms(start: float, end: float | None) -> int | None:
    if end is None:
        return None
    return int((end - start) * 1000)
