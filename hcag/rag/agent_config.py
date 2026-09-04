"""Configuration for the RAG chat agent (§9.6).

Distinct from ``RagConfig`` (which drives the indexer CLI, Part 8) because
the agent needs *retrieval-time* settings — top_k, reranker, context budget,
generation LLM — that have no meaning at index-build time.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..config import LLMConfig, LogConfig
from .config import EmbeddingConfig


RerankerKind = Literal["rrf", "linear", "none"]


class RagAgentIndexConfig(BaseModel):
    """Which LanceDB table to query. Must match what `rag` wrote (§8.7 [index])."""

    path: str = "./local_lancedb"
    table: str = "kb"


class RagAgentRetrievalConfig(BaseModel):
    top_k: int = Field(default=8, ge=1)
    reranker: RerankerKind = "rrf"
    max_context_tokens: int = Field(default=6000, ge=64)
    merge_adjacent: bool = True

    aliases: dict[str, str] = Field(default_factory=dict)
    """Query-time synonym expansion: ``{name users type: name the corpus uses}``.

    Neither retrieval leg can bridge a name the corpus never spells: BM25
    cannot match a token that is absent, and a short query built on a coined
    compound gives the embedder little to work with. The mapping is *data*
    about a particular KB, so it is empty by default and lives in
    ``rag_agent.toml`` — the mechanism is general, the vocabulary is not.

    Keys match on word boundaries, case-insensitively; the value is appended to
    the retrieval query, never substituted, so the user's own wording keeps its
    weight. The QUESTION the generator sees is untouched.
    """


class RagAgentConfig(BaseModel):
    """Top-level ``rag_agent.toml`` schema (§9.6)."""

    index: RagAgentIndexConfig = Field(default_factory=RagAgentIndexConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    llm: LLMConfig = Field(
        default_factory=lambda: LLMConfig(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            api_key_env="ANTHROPIC_API_KEY",
            max_tokens=1024,
        )
    )
    retrieval: RagAgentRetrievalConfig = Field(default_factory=RagAgentRetrievalConfig)
    system_prompt_path: str = ""  # empty => packaged default
    allow_embed_mismatch: bool = False  # escape hatch (§9.8); off by default
    log: LogConfig = Field(default_factory=lambda: LogConfig(file_path="./rag-agent.log"))


def load_rag_agent_config(path: Path | str) -> RagAgentConfig:
    p = Path(path)
    if not p.is_file():
        return RagAgentConfig()
    with p.open("rb") as f:
        return RagAgentConfig.model_validate(tomllib.load(f))


def apply_cli_overrides(
    cfg: RagAgentConfig,
    *,
    index_path: str | None = None,
    table: str | None = None,
    log_file: str | None = None,
    log_level: str | None = None,
) -> RagAgentConfig:
    patch: dict = {}
    if index_path is not None or table is not None:
        idx = cfg.index.model_dump()
        if index_path is not None:
            idx["path"] = index_path
        if table is not None:
            idx["table"] = table
        patch["index"] = RagAgentIndexConfig(**idx)

    if log_file is not None or log_level is not None:
        log = cfg.log.model_dump()
        if log_file is not None:
            log["file_path"] = log_file
        if log_level is not None:
            log["level"] = log_level.upper()
        patch["log"] = LogConfig(**log)

    return cfg.model_copy(update=patch)
