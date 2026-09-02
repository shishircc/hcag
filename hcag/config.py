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

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class LangfuseConfig(BaseModel):
    """Direct Langfuse trace destination (§2.11.1).

    A shorthand, not a second pipeline: it materializes the same OTLP exporter
    the `otel.*` keys build by hand, deriving the endpoint, the pinned
    http/protobuf protocol, and the Basic auth header from a key pair. The
    `langfuse` SDK is deliberately not a dependency.
    """

    # `extra="forbid"` is the enforcement for "credentials never live in the
    # config file": writing `public_key = "pk-..."` here is a validation error,
    # not a silently ignored field, so a secret cannot be committed by accident.
    model_config = ConfigDict(extra="forbid")

    host: str = "https://cloud.langfuse.com"
    """Base URL only — HCAG appends the OTLP path. Set for EU/US regional
    hosts or a self-hosted instance."""

    public_key_env: str = "LANGFUSE_PUBLIC_KEY"
    secret_key_env: str = "LANGFUSE_SECRET_KEY"


class ObservabilityConfig(BaseModel):
    log: LogConfig = Field(default_factory=LogConfig)
    otel: OTELConfig = Field(default_factory=OTELConfig)

    capture_content: bool = True
    """Export prompt and completion text on `gen_ai.chat` spans (§2.11.2).

    On by default because a trace without input and output answers almost none
    of the questions §2.11.4 asks of it. Turn it off when KB content or user
    questions must not leave the process — span structure, model, and token
    counts still export, so latency and cost stay observable.
    """

    max_content_chars: int = 250_000
    """Cap on a single exported prompt/completion payload.

    Sized so a real HCAG prompt — catalog plus a loaded active set — fits
    whole, because the question a trace has to answer is "did the model
    actually have the right information?", and a payload cut short cannot
    answer it. When a payload does exceed this, whole messages are shed from
    the middle (oldest first) rather than characters from the end, so the
    catalog and the recently-loaded packets both survive (§2.11.2).
    """

    max_message_chars: int = 25_000
    """Cap on any single message inside a payload.

    Keeps one very large packet from crowding every other message out of the
    trace. Truncation is always marked with the original length — a silently
    shortened message looks complete, so a reader concludes the prompt lacked
    something it contained.
    """

    langfuse: LangfuseConfig | None = None
    """Present only when the operator configured `[observability.langfuse]`.

    Presence *is* the switch — there is no `enabled` flag to leave on by
    accident. Absent (the default) means nothing is exported to Langfuse.
    """

    @model_validator(mode="after")
    def _one_destination(self) -> "ObservabilityConfig":
        # Silently preferring one would send traces somewhere the operator did
        # not intend, which is worse than refusing to start (§2.11.1). Fan-out
        # to two backends is a collector's job, not this process's.
        if self.langfuse is not None and self.otel.endpoint:
            raise ValueError(
                "observability: both otel.endpoint and [observability.langfuse] are "
                f"configured (otel.endpoint={self.otel.endpoint!r}). Pick one. To send "
                "to more than one backend, point otel.endpoint at a collector and let "
                "it fan out."
            )
        return self


class LLMConfig(BaseModel):
    """Provider-neutral LLM settings. Materialized into a LiteLLM model string."""

    provider: Literal["anthropic", "bedrock", "openai", "ollama"] = "anthropic"
    model: str = "claude-3-5-haiku-20241022"
    api_key_env: str = "ANTHROPIC_API_KEY"
    endpoint: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0

    preflight: bool = True
    """Probe the LLM before `hcag preprocess` walks the tree (§3.4.9).

    Build-time only; the runtime agent ignores it. Turn it off for offline runs
    where every LLM call is stubbed — it does not weaken the mid-run policy.
    """

    max_retries: int = 2
    """Retries per LLM call, with exponential backoff, before the build aborts."""

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


class CompiledConfig(BaseModel):
    """`[compiled]` — how the generated `compiled.md` artifacts are identified."""

    root_id: str | None = None
    """ID for the root folder when a downstream consumer needs a non-empty one.

    `None` means "not configured"; `CliConfig` resolves it to `""`, which is
    the documented default for the root (§3.4.5).
    """


class CatalogConfig(BaseModel):
    """Controls the `## Sub-topics` subtree roll-up (D3a, §3.4.4, §3.6).

    The first four knobs are build-time (read by `hcag preprocess`);
    `strip_subtopics_on_load` is runtime (read by the memory module).
    """

    max_depth: int = 0
    """Cap roll-up to this many levels below each folder. 0 = unlimited."""

    long_depth: int = 1
    """Include `long` on entries at this depth or shallower. 0 = never."""

    include_tree: bool = True
    """Emit the compact `#### Tree` outline at the top of the section."""

    warn_tokens: int = 40000
    """WARN at build time if the ROOT catalog exceeds this (§3.4.8)."""

    strip_subtopics_on_load: bool = True
    """Elide `## Sub-topics` when serving a non-root packet (§2.6)."""


class AgentConfig(BaseModel):
    """Runtime agent configuration."""

    kb_root: str
    max_active_tokens: int = 32000
    llm: LLMConfig = Field(default_factory=LLMConfig)
    catalog: CatalogConfig = Field(default_factory=CatalogConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    system_prompt_prefix: str = (
        "You are an HCAG agent grounded in a hierarchical knowledge base.\n\n"
        "GROUNDING -- THE MOST IMPORTANT RULE. The catalog below is an INDEX, "
        "not a source. Its titles and descriptions exist to tell you WHICH "
        "packet to load; they are one-line summaries written by a build tool "
        "and they are not evidence about anything. NEVER answer from the "
        "catalog. Every factual claim you make must come from the ## Content "
        "of a packet you have actually loaded into this conversation. If the "
        "catalog names a packet that looks like it covers the question, that "
        "means you must LOAD it -- not that you may answer from its "
        "description. If no loaded packet supports an answer, say you do not "
        "have that information and load the packet that would; do not fill the "
        "gap from the catalog, from the folder names, or from your own prior "
        "knowledge.\n\n"
        "NAVIGATION. The catalog indexes EVERY folder in the KB at every "
        "depth, so you never need to walk the tree: find the entries that "
        "cover the question and request them by id directly, however deep they "
        "are. Choose entries by what their OWN content covers, not by how deep "
        "they sit. kind: leaf holds documents and nothing below it. kind: "
        "mixed holds its own documents AND has children -- its content is NOT "
        "repeated in those children, so a deeper entry never supersedes it; "
        "when a mixed topic and one of its specialised children both look "
        "relevant, the parent usually carries the governing rule and the child "
        "the detail. kind: node is a waypoint with no content of its own; go "
        "to its descendants instead. Beware an entry whose description names "
        "your exact keywords but is a narrow sub-document: check whether the "
        "broader topic it sits under defines the rule you actually need.\n\n"
        "WHEN TO LOAD. check_and_load_kb acquires knowledge you do not have; "
        "it is not an acknowledgement of a turn, not a refresh, and not a way "
        "to confirm what is loaded. On each turn decide in this order: (1) is "
        "the question already answerable from the ## Content of packets loaded "
        "in this conversation? Then answer from that content, without calling. "
        "(2) Is the material inside a packet already loaded? Then re-read it, "
        "without calling -- a loaded packet never needs re-requesting. (3) "
        "Otherwise the answer is not in your context: call once with the ids "
        "of the catalog entries that cover the gap (one call may carry ids "
        "from several branches). Never call to refresh, to make sure, or on a "
        "conversational turn such as a follow-up, clarification, or "
        "thank-you. Requesting ids that are already active loads nothing and "
        "is an error.\n\n"
        "Pass currently-known active IDs and requested IDs; trust active_after "
        "as authoritative. Do not call get_catalog -- the catalog below is "
        "already complete. Never assume you can read the KB directly. "
        "Answer in Markdown: use tables, lists, and headings when the source "
        "content does, since the chat UI renders them."
    )


class CliConfig(BaseModel):
    """CLI build tool configuration (hcag.toml at KB root)."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    tokenizer: TokenizerConfig = Field(default_factory=TokenizerConfig)
    catalog: CatalogConfig = Field(default_factory=CatalogConfig)
    compiled: CompiledConfig = Field(default_factory=CompiledConfig)
    log: LogConfig = Field(default_factory=lambda: LogConfig(file_path="./hcag-build.log"))

    root_id: str = ""
    """Resolved root folder ID — read this, not `compiled.root_id`.

    `[compiled] root_id` (§3.6) is the documented home for this setting. A
    top-level `root_id` is also accepted, since earlier configs were written
    that way; `[compiled]` wins when both are present.
    """

    @model_validator(mode="after")
    def _resolve_root_id(self) -> "CliConfig":
        if self.compiled.root_id is not None:
            self.root_id = self.compiled.root_id
        else:
            self.compiled.root_id = self.root_id
        return self


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
