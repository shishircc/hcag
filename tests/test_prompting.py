"""Prompts are data, loaded by name (D11, §2.15)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hcag.prompting import (
    PACKAGED_PROMPTS_DIR,
    PromptError,
    PromptLibrary,
    PromptSpec,
    REGISTRY,
    load_prompts,
    name_to_relpath,
    validate_registry,
)


# --- Name to filename (§2.15.1) -------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("agent.system", "agent/system.md"),
        ("Agent.System", "agent/system.md"),          # lowercased
        ("tool.check_and_load_kb", "tool/check_and_load_kb.md"),
        ("preprocess.scope_own", "preprocess/scope_own.md"),
    ],
)
def test_names_resolve_to_paths(name: str, expected: str) -> None:
    assert name_to_relpath(name) == Path(expected)


@pytest.mark.parametrize(
    "attack",
    ["../../etc/passwd", "..", "agent/../../../secrets", "~/.ssh/id_rsa", "a/../b"],
)
def test_a_name_can_never_escape_the_prompts_directory(attack: str) -> None:
    """Stripping is a security decision: the characters that make traversal
    possible cannot appear in a resolved segment at all."""
    try:
        rel = name_to_relpath(attack)
    except PromptError:
        return  # rejected outright is also fine
    assert ".." not in rel.parts
    assert not rel.is_absolute()
    # Still lands inside the directory once joined.
    assert (Path("/prompts") / rel).resolve().is_relative_to(Path("/prompts"))


def test_a_name_that_is_all_punctuation_is_an_error() -> None:
    """Better than a file called `.md`."""
    with pytest.raises(PromptError, match="empty after sanitizing"):
        name_to_relpath("!!!")


def test_colliding_names_are_a_startup_error() -> None:
    """Stripping is lossy, so `a.b` and `a..b` are the same file. Caught once
    by whoever adds the name, not repeatedly by whoever debugs the behaviour."""
    with pytest.raises(PromptError, match="both resolve to"):
        validate_registry([PromptSpec("a.b"), PromptSpec("a..b")])


# --- Resolution and failure (§2.15.2) -------------------------------------


def _spec(**kw) -> list[PromptSpec]:
    return [PromptSpec("t.one", **kw)]


def _write(tmp_path: Path, rel: str, text: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_operator_file_overrides_the_packaged_default(tmp_path: Path) -> None:
    _write(tmp_path, "agent/system.md", "MINE $catalog")
    lib = load_prompts(tmp_path)

    assert lib.get("agent.system", catalog="C") == "MINE C"
    # Overriding is per prompt: everything else still comes from the package.
    assert "MOST TURNS NEED NO CALL" in lib.get("tool.check_and_load_kb")


def test_packaged_defaults_load_with_no_operator_directory() -> None:
    """'No hard-coded prompts' must not mean 'unusable on install'."""
    lib = load_prompts("/nonexistent-prompts-dir")
    assert lib.get("agent.system", catalog="X")
    assert (PACKAGED_PROMPTS_DIR / "agent" / "system.md").is_file()


def test_a_missing_prompt_is_a_startup_error(tmp_path: Path) -> None:
    with pytest.raises(PromptError, match="not found"):
        PromptLibrary(_spec(), prompts_dir=tmp_path)


def test_an_empty_prompt_file_is_a_startup_error(tmp_path: Path) -> None:
    """Almost always a truncated edit, and the failure it causes is silent."""
    _write(tmp_path, "t/one.md", "   \n\n")
    with pytest.raises(PromptError, match="is empty"):
        PromptLibrary(_spec(), prompts_dir=tmp_path)


# --- Templates (§2.15.3) --------------------------------------------------


def test_unescaped_dollar_fails_at_startup_naming_the_file(tmp_path: Path) -> None:
    """A KB about salary thresholds will have authors writing $11,800. Catch it
    at load, not on the first turn that renders this prompt."""
    _write(tmp_path, "t/one.md", "The qualifying salary is $11,800 at 45.")
    with pytest.raises(PromptError, match=r"invalid \$ placeholder"):
        PromptLibrary(_spec(), prompts_dir=tmp_path)


def test_escaped_dollar_renders_literally(tmp_path: Path) -> None:
    _write(tmp_path, "t/one.md", "The qualifying salary is $$11,800 at 45.")
    lib = PromptLibrary(_spec(), prompts_dir=tmp_path)
    assert lib.get("t.one") == "The qualifying salary is $11,800 at 45."


def test_a_missing_required_variable_is_a_startup_error(tmp_path: Path) -> None:
    """An SME who deletes $catalog would otherwise get an agent that starts
    cleanly, answers fluently, and has no knowledge base."""
    _write(tmp_path, "t/one.md", "no variables here")
    with pytest.raises(PromptError, match=r"missing required variable\(s\): \$catalog"):
        PromptLibrary(_spec(required=frozenset({"catalog"})), prompts_dir=tmp_path)


def test_braces_are_ordinary_text(tmp_path: Path) -> None:
    """The reason for Template over str.format: prompts are full of JSON."""
    _write(tmp_path, "t/one.md", 'Emit {"kind": "assistant.delta", "text": "hi"} and $catalog')
    lib = PromptLibrary(_spec(required=frozenset({"catalog"})), prompts_dir=tmp_path)
    assert '{"kind": "assistant.delta", "text": "hi"}' in lib.get("t.one", catalog="C")


def test_an_unsupplied_variable_raises_rather_than_leaking(tmp_path: Path) -> None:
    """Strict substitute, not safe_substitute: literal `$catalog` reaching the
    model's context is exactly the failure this is meant to make loud."""
    _write(tmp_path, "t/one.md", "uses $mystery")
    lib = PromptLibrary(_spec(), prompts_dir=tmp_path)
    with pytest.raises(PromptError, match=r"uses \$mystery"):
        lib.get("t.one")


# --- Ambient variables ----------------------------------------------------


def test_today_and_packets_are_available_to_any_prompt(tmp_path: Path) -> None:
    _write(tmp_path, "t/one.md", "date=$today packets=[$packets]")
    lib = PromptLibrary(_spec(), prompts_dir=tmp_path)
    out = lib.get("t.one")

    assert "packets=[]" in out          # empty unless the deployment warm-starts
    assert "date=20" in out


def test_today_is_fixed_at_construction_not_read_per_render(tmp_path: Path) -> None:
    """A timestamp would change every turn and destroy prompt caching; the date
    is resolved once so a conversation is governed by one prompt (§2.15.4)."""
    _write(tmp_path, "t/one.md", "$today")
    lib = PromptLibrary(_spec(), prompts_dir=tmp_path)
    assert lib.get("t.one") == lib.get("t.one") == lib.vars["today"]


def test_packets_can_be_supplied_by_the_deployment(tmp_path: Path) -> None:
    _write(tmp_path, "t/one.md", "[$packets]")
    lib = PromptLibrary(_spec(), prompts_dir=tmp_path, extra_vars={"packets": "PRELOADED"})
    assert lib.get("t.one") == "[PRELOADED]"


# --- The shipped registry -------------------------------------------------


def test_the_whole_registry_loads_and_renders() -> None:
    """Every declared prompt has a file, valid template, and required vars."""
    lib = load_prompts()
    values = {
        "catalog": "C", "requested": "a, b", "sections": "S", "scope": "SC",
        "content": "X", "packet_id": "p", "paragraph": "P", "paragraphs": "PS",
        "packet_a_id": "a", "packet_b_id": "b", "paragraphs_a": "A", "paragraphs_b": "B",
        "answer_rules": "RULES",
        "question": "Q", "reply": "R", "expected_answer": "EA",
        "actual_answer": "AA", "transcript": "T", "last_reply": "LR",
    }
    for spec in REGISTRY:
        rendered = lib.get(spec.name, **{k: values[k] for k in spec.required})
        assert rendered.strip(), spec.name


def test_no_prompt_text_remains_in_python_modules() -> None:
    """D11: code names a prompt; a file supplies it."""
    import hcag

    root = Path(hcag.__file__).parent
    needles = ["You are an HCAG", "MOST TURNS NEED NO CALL", "no packets loaded:"]
    offenders = [
        f"{path.relative_to(root)}: {needle}"
        for path in root.rglob("*.py")
        for needle in needles
        if needle in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
