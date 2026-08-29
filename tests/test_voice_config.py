"""Voice-agent config: TOML load, CLI override precedence, duplicate dedup (§5.6, §5.8)."""

from __future__ import annotations

from pathlib import Path

from hcag.voice.config import (
    STTConfig,
    TTSConfig,
    VoiceAgentConfig,
    apply_cli_overrides,
    load_voice_config,
)


SAMPLE_TOML = """
kb_root = "./my-kb"
max_active_tokens = 32000
initial_packet_ids = ["billing.refunds", "billing.invoices"]

[llm]
provider = "anthropic"
model = "claude-3-5-haiku-20241022"

[livekit]
url = "wss://demo.livekit.cloud"

[stt]
provider = "deepgram"
model = "nova-2-general"

[tts]
provider = "elevenlabs"
model = "eleven_turbo_v2_5"
voice_id = "abc123"

[warmup]
enabled = true
"""


def test_load_voice_config(tmp_path: Path) -> None:
    p = tmp_path / "voice.toml"
    p.write_text(SAMPLE_TOML, encoding="utf-8")
    cfg = load_voice_config(p)
    assert cfg.kb_root == "./my-kb"
    assert cfg.stt.provider == "deepgram"
    assert cfg.tts.voice_id == "abc123"
    assert cfg.warmup.enabled is True
    assert cfg.initial_packet_ids == ["billing.refunds", "billing.invoices"]


def test_initial_packet_ids_dedup_preserves_order() -> None:
    cfg = VoiceAgentConfig(
        kb_root="./kb",
        initial_packet_ids=["a", "b", "a", "c", "b"],
    )
    assert cfg.initial_packet_ids == ["a", "b", "c"]


def test_cli_overrides_swap_stt_provider_and_model() -> None:
    base = VoiceAgentConfig(kb_root="./kb")
    assert base.stt.provider == "deepgram"
    patched = apply_cli_overrides(
        base, stt_provider="elevenlabs", stt_model="scribe_v1"
    )
    assert patched.stt.provider == "elevenlabs"
    assert patched.stt.model == "scribe_v1"
    # original unchanged
    assert base.stt.provider == "deepgram"


def test_cli_overrides_swap_tts_and_voice() -> None:
    base = VoiceAgentConfig(kb_root="./kb")
    patched = apply_cli_overrides(
        base, tts_provider="deepgram", tts_model="aura-2-thalia-en", tts_voice="ignored"
    )
    assert patched.tts.provider == "deepgram"
    assert patched.tts.model == "aura-2-thalia-en"
    assert patched.tts.voice_id == "ignored"


def test_cli_overrides_no_warmup_and_initial_packets() -> None:
    base = VoiceAgentConfig(kb_root="./kb", initial_packet_ids=["x"])
    patched = apply_cli_overrides(
        base, initial_packets=["a", "b"], no_warmup=True
    )
    assert patched.initial_packet_ids == ["a", "b"]
    assert patched.warmup.enabled is False


def test_cli_override_log_file_and_level() -> None:
    base = VoiceAgentConfig(kb_root="./kb")
    patched = apply_cli_overrides(base, log_file="/tmp/voice.log", log_level="debug")
    assert patched.observability.log.file_path == "/tmp/voice.log"
    assert patched.observability.log.level == "DEBUG"


def test_env_var_resolution_for_api_keys(monkeypatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-secret")
    stt = STTConfig()
    assert stt.resolved_api_key() == "dg-secret"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-secret")
    tts = TTSConfig()
    assert tts.resolved_api_key() == "el-secret"
