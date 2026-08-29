"""Voice agent — LiveKit-backed real-time interface to HCAG (Part 5).

Public surface:

- `VoiceAgentConfig` — the config model loaded from `voice.toml`.
- `VoiceSession` — the per-room orchestrator that wraps `AgentRuntime`.
- `preload_initial_packets` / `warmup_prompt_cache` — the two startup phases
  called by the worker before opening the room to input (§5.4).
- Transcription message dataclasses and JSON encoders (§5.7).

The LiveKit worker entry point lives in `hcag.voice.worker` and is invoked
via the `hcag-voice` CLI (`hcag.voice.main`).
"""

from .config import (
    LiveKitConfig,
    STTConfig,
    TTSConfig,
    VoiceAgentConfig,
    WarmupConfig,
    load_voice_config,
)
from .startup import (
    PreloadResult,
    WarmupResult,
    preload_initial_packets,
    warmup_prompt_cache,
)
from .transcription import (
    TranscriptionMessage,
    TranscriptionPublisher,
    make_message,
)

__all__ = [
    "LiveKitConfig",
    "STTConfig",
    "TTSConfig",
    "VoiceAgentConfig",
    "WarmupConfig",
    "load_voice_config",
    "PreloadResult",
    "WarmupResult",
    "preload_initial_packets",
    "warmup_prompt_cache",
    "TranscriptionMessage",
    "TranscriptionPublisher",
    "make_message",
]
