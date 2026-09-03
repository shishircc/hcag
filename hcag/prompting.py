"""Prompt loading — names in code, text in files (D11, §2.15).

No prompt text appears in a `.py` module. Code names a prompt; a Markdown file
supplies it, so the person who knows what the model should be told can change
it without editing Python, opening a review, or waiting for a release.

Substitution is stdlib `string.Template` (`$name` / `${name}`) — see
`render` for why, and for what that means when a prompt mentions money.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from string import Template

#: Default location of the operator's overrides, relative to the working dir.
DEFAULT_PROMPTS_DIR = "./prompts"

#: Prompts shipped with the package. Still data — Markdown, diffable,
#: overridable — the point of D11 is that no prompt is a string literal in a
#: module, not that the bytes live outside the wheel.
PACKAGED_PROMPTS_DIR = Path(__file__).parent / "prompts"

#: Everything outside this is stripped from a name segment (§2.15.1).
_ALLOWED = re.compile(r"[^a-z0-9_-]")


class PromptError(RuntimeError):
    """A prompt could not be loaded or is malformed. Always fatal at startup."""


@dataclass(frozen=True)
class PromptSpec:
    """One registered prompt and the variables it must use."""

    name: str
    required: frozenset[str] = frozenset()
    description: str = ""


def name_to_relpath(name: str) -> Path:
    """Resolve a dotted prompt name to a relative file path (§2.15.1).

    `agent.system` -> `agent/system.md`. Every character outside `[a-z0-9_-]`
    is **stripped**, not escaped and not rejected.

    Stripping is a security decision rather than a cosmetic one: a name may one
    day come from configuration or a per-tenant override, and anything that
    reaches a path is a traversal primitive. The characters that make traversal
    possible cannot appear in a resolved segment at all, which is verifiable by
    reading this function. It is lossy — distinct names can collide — and
    `validate_registry` turns that collision into a startup error rather than
    letting two prompts silently become one.
    """
    segments = []
    for raw in name.lower().split("."):
        seg = _ALLOWED.sub("", raw)
        if seg:
            segments.append(seg)
    if not segments:
        raise PromptError(f"prompt name {name!r} is empty after sanitizing")
    return Path(*segments[:-1]) / f"{segments[-1]}.md"


class PromptLibrary:
    """Loads every registered prompt once, at startup (§2.15.4).

    Read once and held, not re-read per turn: the system prompt is the head of
    the cached prefix (§2.12), so a mid-session change would invalidate the
    cache for every live conversation *and* leave a session whose early turns
    ran under different instructions than its later ones. Editing a prompt
    takes effect on restart, and that is the contract.
    """

    def __init__(
        self,
        specs: list[PromptSpec],
        prompts_dir: str | Path | None = None,
        extra_vars: dict[str, str] | None = None,
    ) -> None:
        self.dir = Path(prompts_dir or DEFAULT_PROMPTS_DIR)
        self.specs = {s.name: s for s in specs}
        validate_registry(specs)
        # Available to any prompt, required by none (§2.15.3). `today` is
        # resolved HERE, once, rather than read from the clock per render: a
        # timestamp would change every turn and destroy prompt caching, and a
        # conversation must be governed by one prompt from first turn to last.
        self.vars = {"today": date.today().isoformat(), "packets": "", **(extra_vars or {})}
        self._text: dict[str, str] = {}
        for spec in specs:
            self._text[spec.name] = self._load(spec)

    # ---- loading ---------------------------------------------------------

    def _candidates(self, name: str) -> list[Path]:
        rel = name_to_relpath(name)
        # Operator overrides win, per prompt. A directory that had to be
        # complete would turn a one-line change into a fork.
        return [self.dir / rel, PACKAGED_PROMPTS_DIR / rel]

    def _load(self, spec: PromptSpec) -> str:
        paths = self._candidates(spec.name)
        for path in paths:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                raise PromptError(f"prompt {spec.name!r} at {path} is unreadable: {e}") from e
            if not text.strip():
                # Almost always a truncated edit, and the failure it causes
                # downstream is silent.
                raise PromptError(f"prompt {spec.name!r} at {path} is empty")
            self._check_template(spec, text, path)
            return text
        searched = " and ".join(str(p) for p in paths)
        raise PromptError(
            f"prompt {spec.name!r} not found; searched {searched}. "
            "Prompts are files, not defaults baked into the code (§2.15)."
        )

    @staticmethod
    def _check_template(spec: PromptSpec, text: str, path: Path) -> None:
        template = Template(text)
        if not template.is_valid():
            # A KB about salary thresholds will have authors writing $11,800.
            # Catch it here, naming the file, rather than on the first turn
            # that renders this prompt.
            raise PromptError(
                f"prompt {spec.name!r} at {path} has an invalid $ placeholder. "
                "Write a literal dollar sign as `$$` (e.g. `$$11,800`)."
            )
        used = set(template.get_identifiers())
        missing = set(spec.required) - used
        if missing:
            raise PromptError(
                f"prompt {spec.name!r} at {path} is missing required "
                f"variable(s): {', '.join('$' + m for m in sorted(missing))}"
            )

    # ---- rendering -------------------------------------------------------

    def get(self, name: str, **values: str) -> str:
        """Render a prompt.

        `string.Template`, not `str.format`: prompts are Markdown full of
        braces — JSON examples, code fences — and under `.format` every one is
        a substitution site. `Template` reserves only `$`.

        Strict `substitute`, not `safe_substitute`: an unsupplied variable is a
        bug worth surfacing, and silently leaving `$catalog` as literal text in
        the model's context is exactly the failure this design exists to make
        loud.
        """
        text = self._text.get(name)
        if text is None:
            raise PromptError(f"prompt {name!r} is not registered")
        try:
            return Template(text).substitute({**self.vars, **values})
        except KeyError as e:
            raise PromptError(
                f"prompt {name!r} uses ${e.args[0]}, which was not supplied"
            ) from e

    def __contains__(self, name: str) -> bool:
        return name in self._text


def validate_registry(specs: list[PromptSpec]) -> None:
    """Reject two names that resolve to one file (§2.15.1).

    Sanitizing is lossy, so `a.b` and `a..b` land on the same path. Caught once
    by whoever adds the name, rather than repeatedly by whoever debugs why one
    prompt appears to have another's text.
    """
    seen: dict[Path, str] = {}
    for spec in specs:
        rel = name_to_relpath(spec.name)
        if rel in seen and seen[rel] != spec.name:
            raise PromptError(
                f"prompt names {seen[rel]!r} and {spec.name!r} both resolve to "
                f"{rel} — they differ only in characters that are stripped"
            )
        seen[rel] = spec.name


#: Every prompt the system can load (§2.15.5).
#:
#: Declared in one place so collisions, missing files and missing variables are
#: all caught at startup rather than when a rarely-taken code path first runs.
#:
#: Tool *descriptions* are here because they are model-facing text that decides
#: behaviour — the reload discipline of §2.7.1 is enforced by the wording of
#: `tool.check_and_load_kb`. Tool *schemas* stay in code: the contract is code,
#: the persuasion is data. Operator-facing log and error strings are not
#: prompts — the rule is "text the model reads".
REGISTRY: list[PromptSpec] = [
    PromptSpec("agent.system", frozenset({"catalog"}), "runtime system prompt (§2.7)"),
    PromptSpec(
        "agent.catalog_delimiter",
        frozenset({"catalog"}),
        "INDEX ONLY block wrapping the injected catalog (D3b)",
    ),
    PromptSpec("tool.get_catalog", description="get_catalog description (§1.10)"),
    PromptSpec(
        "tool.check_and_load_kb", description="check_and_load_kb description (§2.7.1)"
    ),
    PromptSpec(
        "memory.redundant_note",
        frozenset({"requested"}),
        "in-band note on a redundant call (§2.3.3)",
    ),
    PromptSpec("voice.system", frozenset({"catalog"}), "voice system prompt (§5.8)"),
    PromptSpec(
        "preprocess.folder_metadata",
        frozenset({"sections", "scope"}),
        "build-time folder summary (§3.4.4)",
    ),
    PromptSpec("preprocess.scope_own", description="leaf/mixed scoping clause (§3.4.4)"),
    PromptSpec("preprocess.scope_branch", description="node scoping clause (§3.4.4)"),
    PromptSpec(
        "evalgen.answer_rules",
        description="completeness standard shared by every question kind (§6.4)",
    ),
    PromptSpec(
        "evalgen.simple",
        frozenset({"content", "answer_rules"}),
        "FAQ-style question (§6.4.1)",
    ),
    PromptSpec(
        "evalgen.medium",
        frozenset({"packet_id", "paragraph", "answer_rules"}),
        "single-paragraph reasoning question (§6.4.2)",
    ),
    PromptSpec(
        "evalgen.complex",
        frozenset({"packet_id", "paragraphs", "answer_rules"}),
        "whole-packet reasoning question (§6.4.3)",
    ),
    PromptSpec(
        "evalgen.hard1",
        frozenset({"packet_a_id", "packet_b_id", "paragraphs_a", "paragraphs_b", "answer_rules"}),
        "cross-packet question (§6.4.4)",
    ),
    PromptSpec(
        "evalgen.hard2",
        frozenset({"packet_id", "content", "answer_rules"}),
        "multimodal question (§6.4.5)",
    ),
    PromptSpec(
        "eval.classify",
        frozenset({"question", "reply"}),
        "answer / clarify / refusal classifier (§7.4.2)",
    ),
    PromptSpec(
        "eval.clarify",
        frozenset({"question", "expected_answer", "transcript", "last_reply"}),
        "clarifier playing the user role (§7.4.2)",
    ),
    PromptSpec(
        "eval.score",
        frozenset({"question", "expected_answer", "actual_answer", "transcript"}),
        "LLM-judge rubric (§7.5)",
    ),
]


def load_prompts(
    prompts_dir: str | Path | None = None, **extra_vars: str
) -> PromptLibrary:
    """Load the whole registry. Raises `PromptError` — fatal at startup."""
    return PromptLibrary(REGISTRY, prompts_dir=prompts_dir, extra_vars=extra_vars or None)
