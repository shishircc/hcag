"""FastAPI application factory for the hcag web backend."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..config import AgentConfig, load_agent_config
from ..runtime.agent import AgentRuntime


class HistoryTurn(BaseModel):
    role: str
    text: str = ""


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str
    history: list[HistoryTurn] = Field(default_factory=list)


class ChatReply(BaseModel):
    text: str
    session_id: str


class TokenRequest(BaseModel):
    identity: str = Field(..., min_length=1)
    room: str | None = None


class TokenReply(BaseModel):
    url: str
    token: str
    room: str


class _SessionEntry:
    __slots__ = ("runtime", "touched")

    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime
        self.touched = time.monotonic()


def _load_config(agent_toml: Path | None) -> AgentConfig:
    if agent_toml is not None and agent_toml.is_file():
        return load_agent_config(agent_toml)
    # Fall back to a minimal in-memory config so the server can boot even
    # without a KB — every /chat call will still fail loudly, but /livekit/token
    # remains usable and startup is diagnosable.
    kb = os.environ.get("HCAG_KB_ROOT", "./kb")
    return AgentConfig(kb_root=kb)


def _resolve_livekit(agent_toml: Path | None) -> tuple[str, str, str, str]:
    """Return (url, api_key, api_secret, room_prefix) for LiveKit token minting.

    Priority: environment > voice.toml > empty. We deliberately DO NOT reuse
    AgentConfig here because AgentConfig doesn't model livekit; voice.toml does.
    """
    url = os.environ.get("LIVEKIT_URL", "")
    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")
    room_prefix = os.environ.get("LIVEKIT_ROOM_PREFIX", "hcag-")

    # Optional: pull url + prefix (but not keys — keys stay in env) from voice.toml
    # if a path is provided alongside agent.toml.
    voice_toml_env = os.environ.get("HCAG_VOICE_CONFIG")
    if voice_toml_env:
        voice_toml = Path(voice_toml_env)
        if voice_toml.is_file():
            try:
                from ..voice.config import load_voice_config

                vc = load_voice_config(voice_toml)
                url = url or vc.livekit.url
                room_prefix = room_prefix or vc.livekit.room_prefix
                api_key = api_key or (vc.livekit.resolved_api_key() or "")
                api_secret = api_secret or (vc.livekit.resolved_api_secret() or "")
            except Exception:
                # Non-fatal — /livekit/token will report a clearer error later.
                pass

    return url, api_key, api_secret, room_prefix


def _mint_livekit_token(url: str, api_key: str, api_secret: str, room: str, identity: str) -> str:
    """Mint a LiveKit access token. Isolated so the import cost is paid on demand."""
    try:
        from livekit.api import AccessToken, VideoGrants  # type: ignore
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=(
                "livekit-api is not installed. Install with `pip install hcag[web]` "
                "(which pulls livekit-api)."
            ),
        ) from e

    if not (url and api_key and api_secret):
        raise HTTPException(
            status_code=500,
            detail=(
                "LiveKit is not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY and "
                "LIVEKIT_API_SECRET, or point HCAG_VOICE_CONFIG at a voice.toml."
            ),
        )

    grants = VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
    at = (
        AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(grants)
    )
    return at.to_jwt()


def create_app(*, agent_toml: Path | None = None, cors_origins: list[str] | None = None) -> FastAPI:
    cfg = _load_config(agent_toml)
    sessions: dict[str, _SessionEntry] = {}
    lock = threading.Lock()

    app = FastAPI(title="hcag-server", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    def get_runtime(session_id: str) -> AgentRuntime:
        with lock:
            entry = sessions.get(session_id)
            if entry is None:
                runtime = AgentRuntime(cfg=cfg)
                runtime.bootstrap()
                entry = _SessionEntry(runtime)
                sessions[session_id] = entry
            entry.touched = time.monotonic()
            return entry.runtime

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "kb_root": cfg.kb_root, "sessions": len(sessions)}

    @app.post("/chat", response_model=ChatReply)
    def chat(req: ChatRequest) -> ChatReply:
        try:
            runtime = get_runtime(req.session_id)
            text = runtime.run_turn(req.message)
        except FileNotFoundError as e:
            raise HTTPException(
                status_code=500,
                detail=f"KB not found at {cfg.kb_root}. Point HCAG_AGENT_CONFIG at your agent.toml.",
            ) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
        return ChatReply(text=text or "", session_id=req.session_id)

    @app.post("/livekit/token", response_model=TokenReply)
    def token(req: TokenRequest) -> TokenReply:
        url, api_key, api_secret, room_prefix = _resolve_livekit(agent_toml)
        room = req.room or f"{room_prefix}{req.identity}"
        jwt = _mint_livekit_token(url, api_key, api_secret, room, req.identity)
        return TokenReply(url=url, token=jwt, room=room)

    return app
