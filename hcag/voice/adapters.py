"""STT / TTS adapter factories (§5.6).

The concrete provider libraries (`livekit-plugins-deepgram`,
`livekit-plugins-elevenlabs`) are imported *inside* the factories rather
than at module load, so:

- the voice package can be imported for tests without the plugins installed,
- and a mis-selected provider fails with a clear, actionable message rather
  than an ImportError at CLI startup.

Adapters are returned as opaque objects; the LiveKit `Agent` / `Session`
runtime consumes them via its own plugin protocol. This keeps the HCAG
layer honest about not building its own STT/TTS engines — the plugins
already implement the streaming protocol the LiveKit runtime expects.
"""

from __future__ import annotations

from typing import Any

from .config import STTConfig, TTSConfig


class VoiceProviderError(RuntimeError):
    """Raised when a provider block cannot be materialized (missing lib, no key, etc)."""


def build_stt(cfg: STTConfig) -> Any:
    """Materialize an STT adapter from `cfg`.

    Deferred imports isolate the plugin dependency to the provider actually
    selected. `endpoint` is passed through when the plugin supports a custom
    base URL (self-hosted or region-pinned deployments).
    """
    api_key = cfg.resolved_api_key()
    if not api_key:
        raise VoiceProviderError(
            f"STT provider '{cfg.provider}' requires env var {cfg.api_key_env} to be set."
        )

    if cfg.provider == "deepgram":
        try:
            from livekit.plugins import deepgram  # type: ignore
        except ImportError as e:
            raise VoiceProviderError(
                "livekit-plugins-deepgram is not installed. Install with "
                "`pip install hcag[voice]` or `pip install livekit-plugins-deepgram`."
            ) from e
        kwargs: dict[str, Any] = {"api_key": api_key, "model": cfg.model, "language": cfg.language}
        if cfg.endpoint:
            kwargs["base_url"] = cfg.endpoint
        return deepgram.STT(**kwargs)

    if cfg.provider == "elevenlabs":
        try:
            from livekit.plugins import elevenlabs  # type: ignore
        except ImportError as e:
            raise VoiceProviderError(
                "livekit-plugins-elevenlabs is not installed. Install with "
                "`pip install hcag[voice]` or `pip install livekit-plugins-elevenlabs`."
            ) from e
        # ElevenLabs' STT model kwarg name varies by version; we forward the
        # plain 'model' key and let the plugin ignore it if unsupported.
        kwargs = {"api_key": api_key, "model": cfg.model, "language_code": cfg.language}
        if cfg.endpoint:
            kwargs["base_url"] = cfg.endpoint
        return elevenlabs.STT(**{k: v for k, v in kwargs.items() if v})

    raise VoiceProviderError(f"Unsupported STT provider: {cfg.provider!r}")


def build_tts(cfg: TTSConfig) -> Any:
    """Materialize a TTS adapter from `cfg`.

    `voice_id` is required for ElevenLabs; Deepgram uses `model` alone.
    """
    api_key = cfg.resolved_api_key()
    if not api_key:
        raise VoiceProviderError(
            f"TTS provider '{cfg.provider}' requires env var {cfg.api_key_env} to be set."
        )

    if cfg.provider == "elevenlabs":
        try:
            from livekit.plugins import elevenlabs  # type: ignore
        except ImportError as e:
            raise VoiceProviderError(
                "livekit-plugins-elevenlabs is not installed."
            ) from e
        if not cfg.voice_id:
            raise VoiceProviderError("ElevenLabs TTS requires tts.voice_id (or --tts-voice).")
        kwargs = {
            "api_key": api_key,
            "model": cfg.model,
            "voice_id": cfg.voice_id,
        }
        if cfg.endpoint:
            kwargs["base_url"] = cfg.endpoint
        return elevenlabs.TTS(**kwargs)

    if cfg.provider == "deepgram":
        try:
            from livekit.plugins import deepgram  # type: ignore
        except ImportError as e:
            raise VoiceProviderError(
                "livekit-plugins-deepgram is not installed."
            ) from e
        kwargs = {"api_key": api_key, "model": cfg.model}
        if cfg.endpoint:
            kwargs["base_url"] = cfg.endpoint
        return deepgram.TTS(**kwargs)

    raise VoiceProviderError(f"Unsupported TTS provider: {cfg.provider!r}")


def describe(cfg: STTConfig | TTSConfig) -> dict[str, Any]:
    """Non-secret summary of a provider block for the `voice.startup.resolved` log line."""
    d = {
        "provider": cfg.provider,
        "model": cfg.model,
        "endpoint": cfg.endpoint or "(default)",
    }
    if isinstance(cfg, STTConfig):
        d["language"] = cfg.language
    else:
        d["voice_id"] = cfg.voice_id or "(unset)"
    return d
