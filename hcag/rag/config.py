"""Configuration models for the `rag` CLI (§8.7)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import LogConfig


class EmbeddingConfig(BaseModel):
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    api_key_env: str = "OPENAI_API_KEY"
    endpoint: str = ""
    batch_size: int = Field(default=32, ge=1)
    dimension: int | None = None  # optional pin; validated against first response


class ImageConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    api_key_env: str = "ANTHROPIC_API_KEY"
    prompt_path: str = ""  # empty => packaged default
    max_retries: int = 2
    max_output_tokens: int = 400
    temperature: float = 0.0


class ChunkingConfig(BaseModel):
    target_tokens: int = Field(default=500, ge=32)
    overlap_tokens: int = Field(default=60, ge=0)
    respect_headings: bool = True


class IndexConfig(BaseModel):
    table: str = "kb"
    include_images: bool = True


class RagConfig(BaseModel):
    """Top-level `rag.toml` schema (§8.7)."""

    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    log: LogConfig = Field(default_factory=lambda: LogConfig(file_path="./rag.log"))


def load_rag_config(path: Path | str) -> RagConfig:
    p = Path(path)
    if not p.is_file():
        return RagConfig()
    with p.open("rb") as f:
        return RagConfig.model_validate(tomllib.load(f))


def apply_cli_overrides(
    cfg: RagConfig,
    *,
    table: str | None = None,
    include_images: bool | None = None,
    log_file: str | None = None,
    log_level: str | None = None,
) -> RagConfig:
    patch: dict = {}
    if table is not None or include_images is not None:
        idx = cfg.index.model_dump()
        if table is not None:
            idx["table"] = table
        if include_images is not None:
            idx["include_images"] = include_images
        patch["index"] = IndexConfig(**idx)

    if log_file is not None or log_level is not None:
        log = cfg.log.model_dump()
        if log_file is not None:
            log["file_path"] = log_file
        if log_level is not None:
            log["level"] = log_level.upper()
        patch["log"] = LogConfig(**log)

    return cfg.model_copy(update=patch)
