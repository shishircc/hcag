"""`hcag-voice` CLI entry point (§5.9).

Two subcommands:

- `serve`   — long-lived LiveKit worker (see `worker.serve`).
- `dry-run` — bootstrap + preload + warm-up without joining a room; prints
              a resolved-config summary. Useful in CI to catch a bad
              initial-packet ID before deploy.

Every CLI flag overrides the corresponding `voice.toml` field with
precedence CLI > env > config > default (see `config.apply_cli_overrides`).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from ..logger import build_logger
from .adapters import describe
from .config import VoiceAgentConfig, apply_cli_overrides, load_voice_config


app = typer.Typer(add_completion=False, no_args_is_help=True, help="HCAG voice agent (LiveKit).")


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [s.strip() for s in value.split(",") if s.strip()]


def _load(
    config: Path,
    kb_root: str | None,
    initial_packets: str | None,
    stt_provider: str | None,
    stt_model: str | None,
    tts_provider: str | None,
    tts_model: str | None,
    tts_voice: str | None,
    livekit_url: str | None,
    no_warmup: bool,
    log_file: str | None,
    log_level: str | None,
) -> VoiceAgentConfig:
    if not config.is_file():
        typer.echo(f"Config file not found: {config}", err=True)
        raise typer.Exit(code=2)
    cfg = load_voice_config(config)
    return apply_cli_overrides(
        cfg,
        kb_root=kb_root,
        initial_packets=_split_csv(initial_packets),
        stt_provider=stt_provider,
        stt_model=stt_model,
        tts_provider=tts_provider,
        tts_model=tts_model,
        tts_voice=tts_voice,
        livekit_url=livekit_url,
        no_warmup=no_warmup,
        log_file=log_file,
        log_level=log_level,
    )


_CONFIG_OPT = typer.Option(Path("./voice.toml"), "--config", "-c", help="Voice config (voice.toml).")
_KB_ROOT_OPT = typer.Option(None, "--kb-root", help="Override kb_root.")
_INITIAL_OPT = typer.Option(None, "--initial-packets", help="Comma-separated packet IDs to preload.")
_STT_PROVIDER_OPT = typer.Option(None, "--stt-provider", help="STT provider (deepgram|elevenlabs).")
_STT_MODEL_OPT = typer.Option(None, "--stt-model", help="STT model ID.")
_TTS_PROVIDER_OPT = typer.Option(None, "--tts-provider", help="TTS provider (elevenlabs|deepgram).")
_TTS_MODEL_OPT = typer.Option(None, "--tts-model", help="TTS model ID.")
_TTS_VOICE_OPT = typer.Option(None, "--tts-voice", help="TTS voice ID (ElevenLabs).")
_LIVEKIT_URL_OPT = typer.Option(None, "--livekit-url", help="LiveKit server URL.")
_NO_WARMUP_OPT = typer.Option(False, "--no-warmup", help="Skip the prompt-cache warm-up call (§5.4.2).")
_LOG_FILE_OPT = typer.Option(None, "--log-file", help="Log file path.")
_LOG_LEVEL_OPT = typer.Option(None, "--log-level", help="Log level (DEBUG|INFO|WARN|ERROR).")


@app.command()
def serve(
    config: Path = _CONFIG_OPT,
    kb_root: str = _KB_ROOT_OPT,
    initial_packets: str = _INITIAL_OPT,
    stt_provider: str = _STT_PROVIDER_OPT,
    stt_model: str = _STT_MODEL_OPT,
    tts_provider: str = _TTS_PROVIDER_OPT,
    tts_model: str = _TTS_MODEL_OPT,
    tts_voice: str = _TTS_VOICE_OPT,
    livekit_url: str = _LIVEKIT_URL_OPT,
    no_warmup: bool = _NO_WARMUP_OPT,
    log_file: str = _LOG_FILE_OPT,
    log_level: str = _LOG_LEVEL_OPT,
) -> None:
    """Run the LiveKit voice worker (long-lived)."""
    cfg = _load(
        config, kb_root, initial_packets, stt_provider, stt_model,
        tts_provider, tts_model, tts_voice, livekit_url, no_warmup,
        log_file, log_level,
    )
    from .worker import serve as run_serve
    asyncio.run(run_serve(cfg))


@app.command("dry-run")
def dry_run(
    config: Path = _CONFIG_OPT,
    kb_root: str = _KB_ROOT_OPT,
    initial_packets: str = _INITIAL_OPT,
    stt_provider: str = _STT_PROVIDER_OPT,
    stt_model: str = _STT_MODEL_OPT,
    tts_provider: str = _TTS_PROVIDER_OPT,
    tts_model: str = _TTS_MODEL_OPT,
    tts_voice: str = _TTS_VOICE_OPT,
    livekit_url: str = _LIVEKIT_URL_OPT,
    no_warmup: bool = _NO_WARMUP_OPT,
    log_file: str = _LOG_FILE_OPT,
    log_level: str = _LOG_LEVEL_OPT,
) -> None:
    """Bootstrap + preload + warm-up without joining a room. Prints the resolved plan."""
    cfg = _load(
        config, kb_root, initial_packets, stt_provider, stt_model,
        tts_provider, tts_model, tts_voice, livekit_url, no_warmup,
        log_file, log_level,
    )
    logger = build_logger(cfg.observability.log, name="hcag.voice.dry_run")

    from .worker import prepare_runtime
    try:
        runtime, preload, warmup = prepare_runtime(cfg, logger)
    except RuntimeError as e:
        typer.echo(f"dry-run failed: {e}", err=True)
        raise typer.Exit(code=1)

    summary = {
        "kb_root": cfg.kb_root,
        "max_active_tokens": cfg.max_active_tokens,
        "initial_packet_ids": cfg.initial_packet_ids,
        "llm": {"provider": cfg.llm.provider, "model": cfg.llm.model},
        "stt": describe(cfg.stt),
        "tts": describe(cfg.tts),
        "warmup": {
            "enabled": cfg.warmup.enabled,
            "ran": warmup.ran,
            "elapsed_ms": warmup.elapsed_ms,
            "prompt_tokens": warmup.prompt_tokens,
            "cache_write_tokens": warmup.cache_write_tokens,
        },
        "preload": {
            "loaded_ids": preload.loaded_ids,
            "skipped_unknown": preload.skipped_unknown,
            "tokens_used": preload.tokens_used,
            "elapsed_ms": preload.elapsed_ms,
        },
    }
    typer.echo(json.dumps(summary, indent=2, default=str))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
