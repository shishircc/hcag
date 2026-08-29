"""Configuration models for the `eval` CLI (§7.9)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..config import LLMConfig, LogConfig


SessionScope = Literal["per-question", "per-run"]


class BackendConfig(BaseModel):
    """The chatbot backend under test — the thing `eval` calls."""

    url: str = "http://localhost:8000"
    chat_path: str = "/chat"
    request_timeout: float = 60.0
    retries: int = 2
    session_scope: SessionScope = "per-question"


class LoopConfig(BaseModel):
    """Multi-turn loop parameters (§7.4)."""

    max_turns: int = Field(default=5, ge=1)


class ClassifierConfig(BaseModel):
    """Small classifier LLM — decides answer / clarify / refusal."""

    llm: LLMConfig = Field(
        default_factory=lambda: LLMConfig(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            api_key_env="ANTHROPIC_API_KEY",
            max_tokens=64,
        )
    )
    prompt_path: str = ""  # empty => use packaged default


class JudgePromptsConfig(BaseModel):
    score: str = ""
    clarify: str = ""
    classify: str = ""


class JudgeConfig(BaseModel):
    """LLM judge (§7.5) — also plays the clarifier role (§7.4.2)."""

    llm: LLMConfig = Field(
        default_factory=lambda: LLMConfig(
            provider="anthropic",
            model="claude-opus-4-7",
            api_key_env="ANTHROPIC_API_KEY",
            max_tokens=512,
        )
    )
    retries: int = 2
    prompts: JudgePromptsConfig = Field(default_factory=JudgePromptsConfig)


class RunConfig(BaseModel):
    concurrency: int = Field(default=4, ge=1)
    seed: int | None = None


class ReportConfig(BaseModel):
    title: str = "HCAG eval"
    baseline: str = ""


class EvalConfig(BaseModel):
    """Top-level `eval.toml` schema (§7.9)."""

    backend: BackendConfig = Field(default_factory=BackendConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    log: LogConfig = Field(default_factory=lambda: LogConfig(file_path="./eval.log"))


def load_eval_config(path: Path | str) -> EvalConfig:
    p = Path(path)
    if not p.is_file():
        return EvalConfig()
    with p.open("rb") as f:
        return EvalConfig.model_validate(tomllib.load(f))


def apply_cli_overrides(
    cfg: EvalConfig,
    *,
    backend_url: str | None = None,
    max_turns: int | None = None,
    concurrency: int | None = None,
    request_timeout: float | None = None,
    session_scope: SessionScope | None = None,
    seed: int | None = None,
    baseline: str | None = None,
    log_file: str | None = None,
    log_level: str | None = None,
) -> EvalConfig:
    """Return a NEW config with CLI-flag overrides applied on top of `cfg`."""
    patch: dict = {}

    if any(v is not None for v in (backend_url, request_timeout, session_scope)):
        b = cfg.backend.model_dump()
        if backend_url is not None:
            b["url"] = backend_url
        if request_timeout is not None:
            b["request_timeout"] = request_timeout
        if session_scope is not None:
            b["session_scope"] = session_scope
        patch["backend"] = BackendConfig(**b)

    if max_turns is not None:
        patch["loop"] = LoopConfig(max_turns=max_turns)

    if concurrency is not None or seed is not None:
        r = cfg.run.model_dump()
        if concurrency is not None:
            r["concurrency"] = concurrency
        if seed is not None:
            r["seed"] = seed
        patch["run"] = RunConfig(**r)

    if baseline is not None:
        rep = cfg.report.model_dump()
        rep["baseline"] = baseline
        patch["report"] = ReportConfig(**rep)

    if log_file is not None or log_level is not None:
        log = cfg.log.model_dump()
        if log_file is not None:
            log["file_path"] = log_file
        if log_level is not None:
            log["level"] = log_level.upper()
        patch["log"] = LogConfig(**log)

    return cfg.model_copy(update=patch)
