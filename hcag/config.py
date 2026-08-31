"""Configuration models loaded from TOML files.

Two entry points:
- AgentConfig    — for the runtime agent (agent.toml)
- CliConfig      — for the `hcag` build tool (hcag.toml at KB root)

Both share ObservabilityConfig.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LogConfig(BaseModel):
    file_path: str = "./hcag.log"
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"] = "INFO"
    rotation_size_mb: int = 50
    rotation_keep: int = 5


class OTELConfig(BaseModel):
    endpoint: str | None = None
    protocol: Literal["http/protobuf", "grpc"] = "http/protobuf"
    headers: dict[str, str] = Field(default_factory=dict)
    service_name: str = "hcag-agent"


class ObservabilityConfig(BaseModel):
    log: LogConfig = Field(default_factory=LogConfig)
    otel: OTELConfig = Field(default_factory=OTELConfig)


class LLMConfig(BaseModel):
    """Provider-neutral LLM settings. Materialized into a LiteLLM model string."""

    provider: Literal["anthropic", "bedrock", "openai", "ollama"] = "anthropic"
    model: str = "claude-3-5-haiku-20241022"
    api_key_env: str = "ANTHROPIC_API_KEY"
    endpoint: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0

    def litellm_model(self) -> str:
        """Build the LiteLLM-compatible model string."""
        if self.provider == "anthropic":
            return self.model
        if self.provider == "bedrock":
            # Accept both prefixed and bare model IDs
            return self.model if self.model.startswith("bedrock/") else f"bedrock/{self.model}"
        if self.provider == "ollama":
            return self.model if self.model.startswith("ollama/") else f"ollama/{self.model}"
        if self.provider == "openai":
            return self.model if self.model.startswith("openai/") else f"openai/{self.model}"
        return self.model


class TokenizerConfig(BaseModel):
    kind: Literal["tiktoken", "rough"] = "tiktoken"
    encoding: str = "cl100k_base"


class AgentConfig(BaseModel):
    """Runtime agent configuration."""

    kb_root: str
    max_active_tokens: int = 32000
    llm: LLMConfig = Field(default_factory=LLMConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    system_prompt_prefix: str = (
        "You are an HCAG agent grounded in a hierarchical knowledge base. "
        "Consult the catalog below and use check_and_load_kb only when the "
        "currently-loaded packets are insufficient. Pass currently-known active "
        "IDs and requested IDs; trust active_after as authoritative. "
        "Never assume you can read the KB directly."
    )


class CliConfig(BaseModel):
    """CLI build tool configuration (hcag.toml at KB root)."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    tokenizer: TokenizerConfig = Field(default_factory=TokenizerConfig)
    log: LogConfig = Field(default_factory=lambda: LogConfig(file_path="./hcag-build.log"))
    root_id: str = ""  # id used for the root folder; empty string is fine and matches §3.4.5


class EvalGenGenerationConfig(BaseModel):
    max_retries_per_item: int = 2
    paragraph_min_chars: int = 120
    cross_packet_bias: Literal["taxonomy", "uniform"] = "taxonomy"


class EvalGenConfig(BaseModel):
    """`evalgen` CLI configuration (§6.8)."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    generation: EvalGenGenerationConfig = Field(default_factory=EvalGenGenerationConfig)
    log: LogConfig = Field(default_factory=lambda: LogConfig(file_path="./evalgen.log"))


def load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def load_agent_config(path: Path) -> AgentConfig:
    return AgentConfig.model_validate(load_toml(path))


def load_cli_config(path: Path) -> CliConfig:
    if not path.exists():
        return CliConfig()
    return CliConfig.model_validate(load_toml(path))


def load_evalgen_config(path: Path) -> EvalGenConfig:
    if not path.exists():
        return EvalGenConfig()
    return EvalGenConfig.model_validate(load_toml(path))
