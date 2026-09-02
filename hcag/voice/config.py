"""Voice agent configuration models (§5.8).

`VoiceAgentConfig` layers voice-specific sections on top of the base
`AgentConfig` used by the text runtime — the shared blocks (`llm`, `log`,
OTEL) reuse the same schemas so nothing forks between runtimes.

Precedence for provider/model selection is enforced elsewhere (§5.9):
CLI flag > env var > config file > adapter default. This module only holds
the config-file layer.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..config import CatalogConfig, LLMConfig, LogConfig, ObservabilityConfig, OTELConfig


STTProvider = Literal["deepgram", "elevenlabs"]
TTSProvider = Literal["elevenlabs", "deepgram"]


class LiveKitConfig(BaseModel):
    url: str = ""
    api_key_env: str = "LIVEKIT_API_KEY"
    api_secret_env: str = "LIVEKIT_API_SECRET"
    room_prefix: str = "hcag-"

    def resolved_api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

    def resolved_api_secret(self) -> str | None:
        return os.environ.get(self.api_secret_env)


class STTConfig(BaseModel):
    provider: STTProvider = "deepgram"
    model: str = "nova-2-general"
    api_key_env: str = "DEEPGRAM_API_KEY"
    language: str = "en-US"
    endpoint: str = ""

    def resolved_api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


class TTSConfig(BaseModel):
    provider: TTSProvider = "elevenlabs"
    model: str = "eleven_turbo_v2_5"
    voice_id: str = ""
    api_key_env: str = "ELEVENLABS_API_KEY"
    endpoint: str = ""

    def resolved_api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


class WarmupConfig(BaseModel):
    enabled: bool = True
    prompt: str = "Ready. Await user turn."


class VoiceAgentConfig(BaseModel):
    """Full voice-agent config (`voice.toml`).

    Extends the same fields as `AgentConfig` and adds voice-specific blocks.
    """

    kb_root: str
    max_active_tokens: int = 32000
    prompts_dir: str = "./prompts"
    """Prompt overrides for the voice agent (D11, §2.15).

    The voice system prompt is `voice.system`; it differs from the text agent's
    in asking for spoken prose, which is a wording decision and therefore a
    file rather than a constant.
    """
    initial_packet_ids: list[str] = Field(default_factory=list)

    llm: LLMConfig = Field(default_factory=LLMConfig)
    catalog: CatalogConfig = Field(default_factory=CatalogConfig)
    observability: ObservabilityConfig = Field(
        default_factory=lambda: ObservabilityConfig(
            log=LogConfig(file_path="./hcag-voice.log"),
            otel=OTELConfig(service_name="hcag-voice"),
        )
    )

    livekit: LiveKitConfig = Field(default_factory=LiveKitConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    warmup: WarmupConfig = Field(default_factory=WarmupConfig)

    @model_validator(mode="after")
    def _dedup_preloads(self) -> "VoiceAgentConfig":
        """Drop duplicate initial packet IDs while preserving order."""
        seen: set[str] = set()
        deduped: list[str] = []
        for pid in self.initial_packet_ids:
            if pid not in seen:
                seen.add(pid)
                deduped.append(pid)
        self.initial_packet_ids = deduped
        return self


def load_voice_config(path: Path | str) -> VoiceAgentConfig:
    path = Path(path)
    with path.open("rb") as f:
        data = tomllib.load(f)
    return VoiceAgentConfig.model_validate(data)


def apply_cli_overrides(
    cfg: VoiceAgentConfig,
    *,
    kb_root: str | None = None,
    initial_packets: list[str] | None = None,
    stt_provider: str | None = None,
    stt_model: str | None = None,
    tts_provider: str | None = None,
    tts_model: str | None = None,
    tts_voice: str | None = None,
    livekit_url: str | None = None,
    no_warmup: bool = False,
    log_file: str | None = None,
    log_level: str | None = None,
) -> VoiceAgentConfig:
    """Apply CLI flag overrides on top of a loaded config (§5.9 precedence).

    Returns a NEW config instance; the input is left untouched.
    """
    patch: dict = {}
    if kb_root is not None:
        patch["kb_root"] = kb_root
    if initial_packets is not None:
        patch["initial_packet_ids"] = initial_packets

    if any(v is not None for v in (stt_provider, stt_model)):
        stt = cfg.stt.model_dump()
        if stt_provider is not None:
            stt["provider"] = stt_provider
        if stt_model is not None:
            stt["model"] = stt_model
        patch["stt"] = STTConfig(**stt)

    if any(v is not None for v in (tts_provider, tts_model, tts_voice)):
        tts = cfg.tts.model_dump()
        if tts_provider is not None:
            tts["provider"] = tts_provider
        if tts_model is not None:
            tts["model"] = tts_model
        if tts_voice is not None:
            tts["voice_id"] = tts_voice
        patch["tts"] = TTSConfig(**tts)

    if livekit_url is not None:
        lk = cfg.livekit.model_dump()
        lk["url"] = livekit_url
        patch["livekit"] = LiveKitConfig(**lk)

    if no_warmup:
        warm = cfg.warmup.model_dump()
        warm["enabled"] = False
        patch["warmup"] = WarmupConfig(**warm)

    if log_file is not None or log_level is not None:
        obs = cfg.observability.model_dump()
        log = obs.get("log", {})
        if log_file is not None:
            log["file_path"] = log_file
        if log_level is not None:
            log["level"] = log_level.upper()
        obs["log"] = log
        patch["observability"] = ObservabilityConfig(**obs)

    return cfg.model_copy(update=patch)
