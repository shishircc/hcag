"""LLM-driven folder metadata generation (§3.4.4).

Every folder — leaf, taxonomy node, mixed, or root — needs one summary record
(title, short_description, long_description) that its parent renders as an
entry in its ``## Sub-topics`` section. The same prompt handles all three
folder kinds: leaves are summarized from their own content, taxonomy nodes
from their children's descriptions, and mixed folders from both.

**A parent is fed its children's `long_description`s, never their
`short_description`s** (§3.4.4). Summarization here is iterated — the root's
description is a summary of summaries — so anything dropped at one level can
never be recovered at the next. Feeding one-line labels upward makes each
parent summarize labels rather than content, and by the root a branch about
"SAML assertion mapping, certificate rotation, and IdP metadata exchange" has
flattened into "authentication settings". The parent's summarizer does the
compressing; it should not compound a compression that already happened.

Uses LiteLLM directly (provider-neutral); never imports vendor SDKs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from ..config import LLMConfig


@dataclass
class FolderMetadata:
    title: str
    short_description: str
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
    filter. The build still aborts by default, because a placeholder summary
    would silently degrade every ancestor above it (§3.4.4).
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
            "not from hcag.toml"
        )


_PROMPT = """You will summarize one folder of a hierarchical knowledge base so
a catalog can route to it.

{scope}

Emit ONE compact JSON object with exactly these fields (no prose, no code fences):
  "title": short human-readable title (<=60 chars)
  "short_description": ONE line, no line breaks, <=180 chars
  "long_description": 2-4 sentences describing scope, key concepts, and when
                      this folder is relevant

{sections}"""


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


#: How the summary must be scoped, by folder kind (§3.4.4).
#:
#: A folder with its own content must be described by THAT content. Describing
#: what its children hold makes its catalog entry match questions its own
#: `## Content` cannot answer — and since the roll-up (D3a) already gives every
#: descendant its own entry in the same catalog, advertising their contents
#: buys nothing and costs precision. Observed: an "eligibility" folder whose
#: description absorbed a child's "sector-specific salary benchmark tables"
#: drew the agent to the child and away from the rule it needed.
_SCOPE_OWN = """Describe what THIS folder's own content says — the text under
OWN CONTENT below. Child topics are listed only as context so you can tell what
kind of branch this is; do NOT describe their contents or borrow their
specifics. Every descendant has its own catalog entry, so this entry only has
to make THIS folder findable. If the folder's own content states a rule,
threshold, or definition, say so explicitly — that is what callers route on."""

_SCOPE_BRANCH = """This folder has no content of its own: it is a waypoint. Its
child topics are all there is to describe, so summarize ACROSS them — the
result must characterize the whole branch, not just its first or largest
child."""


def _compose_sections(
    own_content: str,
    children_longs: list[tuple[str, str]],
    max_child_chars: int,
) -> str:
    parts: list[str] = []
    if own_content.strip():
        parts.append("=== OWN CONTENT ===\n" + own_content.strip())
    if children_longs:
        blocks = []
        for cid, long in children_longs:
            text = " ".join((long or "").split())[:max_child_chars]
            blocks.append(f"- `{cid}`\n  {text}" if text else f"- `{cid}`\n  (no description)")
        parts.append("=== CHILD TOPICS ===\n" + "\n".join(blocks))
    if not parts:
        parts.append("(empty folder — infer a placeholder summary from its identifier)")
    return "\n\n".join(parts)


def generate_folder_metadata(
    cfg: LLMConfig,
    *,
    own_content: str = "",
    children_longs: list[tuple[str, str]] | None = None,
    max_content_chars: int = 20000,
    max_child_chars: int = 1200,
    kind: str = "",
) -> FolderMetadata:
    """Summarize one folder for its parent's catalog entry.

    ``own_content`` is the concatenated source markdown at this level (empty
    for pure taxonomy nodes). ``children_longs`` is a list of
    ``(id, long_description)`` tuples for the **immediate** children (empty
    for pure leaves) — the long form, per the module docstring and §3.4.4.

    Long inputs are trimmed rather than dropped: ``max_child_chars`` caps each
    child individually so that a wide folder still sees *every* child. Dropping
    children to fit a budget would hide whole branches from the summary, which
    is the failure this design is trying to avoid in the first place.
    """
    trimmed = own_content[:max_content_chars]
    sections = _compose_sections(
        trimmed, list(children_longs or []), max_child_chars=max_child_chars
    )
    # A `node` has nothing but its children to describe; anything else must be
    # described by its own content (§3.4.4).
    has_own = bool(trimmed.strip()) if not kind else kind in ("leaf", "mixed")
    scope = _SCOPE_OWN if has_own else _SCOPE_BRANCH
    raw = _complete(cfg, _PROMPT.format(sections=sections, scope=scope))
    data = _extract_json(raw)
    return FolderMetadata(
        title=str(data.get("title", "Untitled")).strip(),
        short_description=str(data.get("short_description", "")).replace("\n", " ").strip(),
        long_description=str(data.get("long_description", "")).strip(),
    )
