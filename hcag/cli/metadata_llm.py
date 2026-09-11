"""LLM-driven folder metadata generation (§3.4.4).

A folder with content of its own gets one summary record — a title and a
`long_description` — from **that content and nothing else**. Not its children's
descriptions, not its parent's, not its siblings'. A folder with no content of
its own is not summarized at all: no call is made, no description is written,
and it gets no catalog row (D3a).

That input rule is the whole point of this module, and it replaced two failures
that were not incidental. Feeding a parent its children's descriptions made
summarization *iterated*, so a branch about "SAML assertion mapping,
certificate rotation, and IdP metadata exchange" arrived at the root as
"authentication settings" — the descriptions nearest the root, which an agent
reads first, were the most degraded in the file. And asking for a description of
a pure taxonomy node, which has no text at all, produced confident specifics
about documents nobody had read, in the system prompt, where they read exactly
like source material.

Uses LiteLLM directly (provider-neutral); never imports vendor SDKs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from ..config import LLMConfig
from ..prompting import PromptLibrary, load_prompts


@dataclass
class FolderMetadata:
    title: str
    long_description: str


# --- Failure taxonomy (§3.4.9) ---------------------------------------------


class MetadataLLMError(RuntimeError):
    """Base for every LLM failure the build can raise."""


class LLMUnavailableError(MetadataLLMError):
    """The LLM cannot serve this build at all.

    Bad credentials, an unknown model, an unreachable endpoint, an exhausted
    quota. Retrying will not help and neither will continuing the walk — every
    remaining folder needs the same call.
    """


class MetadataGenerationError(MetadataLLMError):
    """One folder's summary could not be produced after retries.

    Folder-specific rather than systemic: an unparseable reply, a content
    filter. The build still aborts by default — not because the failure spreads
    (it no longer can), but because a folder with no description is one the
    agent routes past, and one such folder in a KB of two hundred is invisible
    in every aggregate (§3.4.9).
    """


#: Transient — worth retrying with backoff before giving up.
_RETRYABLE = frozenset({
    "APIConnectionError",
    "APIConnectionTimeout",
    "APIError",
    "InternalServerError",
    "OverloadedError",
    "RateLimitError",
    "ServiceUnavailableError",
    "Timeout",
})

#: Systemic — the LLM is not usable for this build; retrying wastes time.
_UNAVAILABLE = frozenset({
    "AuthenticationError",
    "BudgetExceededError",
    "InvalidRequestError",
    "NotFoundError",
    "PermissionDeniedError",
})

#: Providers whose credentials come from `api_key_env`. Bedrock uses the AWS
#: credential chain and local servers need no key, so neither is checked here.
_KEY_REQUIRED_PROVIDERS = frozenset({"anthropic", "openai"})


def classify(exc: BaseException) -> str:
    """Return ``"retryable"``, ``"unavailable"``, or ``"item"`` for ``exc``.

    Matches on exception class names rather than importing LiteLLM's exception
    types, so classification works without the import and does not break when
    LiteLLM reorganizes its hierarchy.
    """
    if isinstance(exc, LLMUnavailableError):
        return "unavailable"
    names = {c.__name__ for c in type(exc).__mro__}
    if names & _UNAVAILABLE:
        return "unavailable"
    if names & _RETRYABLE:
        return "retryable"
    return "item"


def describe_failure(cfg: LLMConfig, exc: BaseException) -> str:
    """One line naming *which* thing is misconfigured, plus the resolved
    settings an operator needs to fix it (§3.4.9)."""
    # Our own errors already read as an explanation; prefixing the class name
    # onto them just adds noise to the operator-facing line.
    detail = str(exc) if isinstance(exc, MetadataLLMError) else f"{type(exc).__name__}: {exc}"
    where = f"provider={cfg.provider} model={cfg.litellm_model()!r}"
    if cfg.endpoint:
        where += f" endpoint={cfg.endpoint!r}"
    if cfg.provider in _KEY_REQUIRED_PROVIDERS:
        where += f" api_key_env={cfg.api_key_env!r}"
    hint = ""
    if cfg.provider in ("ollama", "llamacpp") and classify(exc) == "retryable":
        hint = " — local providers need a server running at `endpoint`"
    return f"{detail} ({where}){hint}"


def check_credentials(cfg: LLMConfig) -> None:
    """Raise before any network call if the configured key env var is empty."""
    if cfg.provider not in _KEY_REQUIRED_PROVIDERS:
        return
    if not os.environ.get(cfg.api_key_env, "").strip():
        raise LLMUnavailableError(
            f"environment variable {cfg.api_key_env!r} is unset or empty; "
            f"the {cfg.provider} provider reads its API key from the environment, "
            "never from a config file"
        )


def _prompts(cfg: LLMConfig) -> PromptLibrary:
    """Load the build-time prompts (D11, §2.15).

    Cached per process: `preprocess` calls this once per folder and the files
    do not change during a run.
    """
    global _LIB
    if _LIB is None:
        _LIB = load_prompts(getattr(cfg, "prompts_dir", None))
    return _LIB


_LIB: PromptLibrary | None = None


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _complete(cfg: LLMConfig, prompt: str) -> str:
    check_credentials(cfg)

    import litellm

    resp = litellm.completion(
        model=cfg.litellm_model(),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=cfg.max_tokens,
        temperature=0.0,
        **({"api_base": cfg.endpoint} if cfg.endpoint else {}),
    )
    return resp.choices[0].message.content or ""


def generate_folder_metadata(
    cfg: LLMConfig,
    *,
    own_content: str,
    max_content_chars: int = 20000,
) -> FolderMetadata:
    """Summarize one folder for its catalog row, from its own content alone.

    ``own_content`` is the concatenated source markdown at this level. It is
    the only input, and there is no parameter for anything else — a signature
    that cannot express "and here are the children" is the cheapest place to
    enforce the rule §3.4.4 exists to state.

    Callers do not invoke this for a folder without content: such a folder has
    nothing to describe and gets no row (D3a).
    """
    trimmed = own_content[:max_content_chars]
    if not trimmed.strip():
        raise MetadataGenerationError(
            "generate_folder_metadata called with no content; a folder with "
            "nothing of its own is not summarized (D3a)"
        )
    sections = "=== OWN CONTENT ===\n" + trimmed.strip()
    raw = _complete(
        cfg, _prompts(cfg).get("preprocess.folder_metadata", sections=sections)
    )
    data = _extract_json(raw)
    return FolderMetadata(
        title=str(data.get("title", "Untitled")).strip(),
        long_description=str(data.get("long_description", "")).strip(),
    )
