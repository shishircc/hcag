"""FastAPI application factory for the hcag web backend.

Serves ``POST /chat`` for one of two agent implementations (§9.5):

- ``agent_type="hcag"`` — the taxonomy-navigating HCAG ``AgentRuntime`` (§2).
- ``agent_type="rag"``  — the flat-RAG ``RagAgent`` competing baseline (§9).

Both agents implement ``run_turn(str) -> str`` so the HTTP route is agent-agnostic.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


AgentType = Literal["hcag", "rag"]


# --- Wire types -------------------------------------------------------------


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


class _AgentLike(Protocol):
    def run_turn(self, user_message: str) -> str: ...


class _SessionEntry:
    __slots__ = ("agent", "touched")

    def __init__(self, agent: _AgentLike) -> None:
        self.agent = agent
        self.touched = time.monotonic()


# --- HCAG agent bootstrap ---------------------------------------------------


def _load_hcag_config(agent_toml: Path | None):
    from ..config import AgentConfig, load_agent_config

    if agent_toml is not None and agent_toml.is_file():
        return load_agent_config(agent_toml)
    kb = os.environ.get("HCAG_KB_ROOT", "./kb")
    return AgentConfig(kb_root=kb)


def _make_hcag_factory(agent_toml: Path | None):
    """Return (health_info, session_factory) for --agent hcag."""
    from ..runtime.agent import AgentRuntime

    cfg = _load_hcag_config(agent_toml)

    def _session() -> _AgentLike:
        runtime = AgentRuntime(cfg=cfg)
        runtime.bootstrap()
        return runtime

    info = {"agent": "hcag", "kb_root": cfg.kb_root}
    return info, _session


# --- RAG agent bootstrap ----------------------------------------------------


def _load_rag_agent_config(rag_toml: Path | None):
    from ..rag.agent_config import RagAgentConfig, load_rag_agent_config

    if rag_toml is not None and rag_toml.is_file():
        return load_rag_agent_config(rag_toml)
    return RagAgentConfig()


def _make_rag_factory(rag_toml: Path | None, rag_index: Path | None):
    """Return (health_info, session_factory) for --agent rag.

    Bootstraps ``RagAgentDeps`` ONCE at server startup so each session shares
    the LanceDB connection + embedder + system prompt (§9.5 startup semantics).
    Startup failure raises ``AgentBootstrapError`` which the caller maps to a
    process-fatal error.
    """
    from ..rag.agent import RagAgent, build_deps

    cfg = _load_rag_agent_config(rag_toml)
    if rag_index is not None:
        cfg = cfg.model_copy(update={"index": cfg.index.model_copy(update={"path": str(rag_index)})})

    deps = build_deps(cfg)

    def _session() -> _AgentLike:
        return RagAgent(cfg=cfg, deps=deps)

    info = {
        "agent": "rag",
        "index_path": cfg.index.path,
        "table": cfg.index.table,
        "embed_model": cfg.embedding.model,
        "gen_model": cfg.llm.model,
    }
    return info, _session


# --- LiveKit token minting (unchanged from prior version) -------------------


def _resolve_livekit() -> tuple[str, str, str, str]:
    url = os.environ.get("LIVEKIT_URL", "")
    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")
    room_prefix = os.environ.get("LIVEKIT_ROOM_PREFIX", "hcag-")

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
                pass

    return url, api_key, api_secret, room_prefix


def _mint_livekit_token(url: str, api_key: str, api_secret: str, room: str, identity: str) -> str:
    try:
        from livekit.api import AccessToken, VideoGrants  # type: ignore
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail="livekit-api is not installed. Install with `pip install hcag[web]`.",
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


# --- Factory ---------------------------------------------------------------


def create_app(
    *,
    agent_type: AgentType = "hcag",
    agent_toml: Path | None = None,
    rag_index: Path | None = None,
    rag_config: Path | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    if agent_type == "hcag":
        agent_info, session_factory = _make_hcag_factory(agent_toml)
    elif agent_type == "rag":
        agent_info, session_factory = _make_rag_factory(rag_config, rag_index)
    else:
        raise ValueError(f"unknown agent_type: {agent_type!r}. Expected 'hcag' or 'rag'.")

    sessions: dict[str, _SessionEntry] = {}
    lock = threading.Lock()

    app = FastAPI(title=f"hcag-server ({agent_type})", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    def get_agent(session_id: str) -> _AgentLike:
        with lock:
            entry = sessions.get(session_id)
            if entry is None:
                entry = _SessionEntry(session_factory())
                sessions[session_id] = entry
            entry.touched = time.monotonic()
            return entry.agent

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "sessions": len(sessions), **agent_info}

    @app.post("/chat", response_model=ChatReply)
    def chat(req: ChatRequest) -> ChatReply:
        try:
            agent = get_agent(req.session_id)
            text = agent.run_turn(req.message)
        except FileNotFoundError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
        return ChatReply(text=text or "", session_id=req.session_id)

    @app.post("/livekit/token", response_model=TokenReply)
    def token(req: TokenRequest) -> TokenReply:
        url, api_key, api_secret, room_prefix = _resolve_livekit()
        room = req.room or f"{room_prefix}{req.identity}"
        jwt = _mint_livekit_token(url, api_key, api_secret, room, req.identity)
        return TokenReply(url=url, token=jwt, room=room)

    return app
