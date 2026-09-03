"""`evalrun`'s prompts are registry prompts (D11, §2.15) and its LLMs preflight (§7.3.1).

The regression these pin: the classifier and judge prompts contain a literal
JSON example (`{"score": 0 | 1 | 2 | 3, ...}`) and were rendered with
`str.format`, under which every brace is a substitution site. Both raised
`KeyError` on the first render, so no row was ever classified or scored. This
is exactly the failure §2.15 cites as the reason prompts use `string.Template`.
"""

from __future__ import annotations

import pytest

from hcag.cli.metadata_llm import LLMUnavailableError
from hcag.config import LLMConfig
from hcag.eval.config import EvalConfig, load_eval_config
from hcag.eval.llm_calls import preflight
from hcag.logger import build_logger, LogConfig
from hcag.prompting import REGISTRY, load_prompts


EVAL_PROMPTS = {"eval.classify", "eval.clarify", "eval.score"}


def _logger(tmp_path):
    return build_logger(LogConfig(file_path=str(tmp_path / "t.log")), name="hcag.eval.test")


def test_eval_prompts_are_in_the_registry() -> None:
    assert EVAL_PROMPTS <= {s.name for s in REGISTRY}


@pytest.mark.parametrize(
    "name,values",
    [
        ("eval.classify", {"question": "Q", "reply": "R"}),
        (
            "eval.score",
            {"question": "Q", "expected_answer": "E", "actual_answer": "A", "transcript": "T"},
        ),
        (
            "eval.clarify",
            {"question": "Q", "expected_answer": "E", "transcript": "T", "last_reply": "L"},
        ),
    ],
)
def test_eval_prompts_render(name: str, values: dict[str, str]) -> None:
    """The bug: these raised KeyError before rendering a single character."""
    rendered = load_prompts().get(name, **values)
    for v in values.values():
        assert v in rendered


def test_json_example_braces_survive_rendering() -> None:
    """The literal JSON the model is told to emit must reach the model intact."""
    lib = load_prompts()
    assert '{"category": "answer" | "clarify" | "refusal"}' in lib.get(
        "eval.classify", question="Q", reply="R"
    )
    assert '{"score": 0 | 1 | 2 | 3' in lib.get(
        "eval.score", question="Q", expected_answer="E", actual_answer="A", transcript="T"
    )


def test_preflight_reports_the_role_that_failed(tmp_path, monkeypatch) -> None:
    """Classifier and judge are separately configured and often separately keyed."""
    monkeypatch.delenv("HCAG_TEST_MISSING_KEY", raising=False)
    cfg = LLMConfig(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        api_key_env="HCAG_TEST_MISSING_KEY",
    )
    with pytest.raises(LLMUnavailableError) as e:
        preflight(cfg, "judge", _logger(tmp_path))
    assert "judge" in str(e.value)


def test_sample_config_still_parses() -> None:
    cfg = load_eval_config("examples/evalrun.toml")
    assert cfg.prompts_dir and cfg.judge.llm.preflight and cfg.classifier.llm.preflight
    assert cfg.log.file_path == "./evalrun.log"


def test_prompt_overrides_go_through_prompts_dir(tmp_path) -> None:
    """Per-prompt override, not a bespoke `prompt_path` per role (§2.15.2)."""
    d = tmp_path / "prompts" / "eval"
    d.mkdir(parents=True)
    (d / "score.md").write_text("MINE $question $expected_answer $actual_answer $transcript")
    lib = load_prompts(str(tmp_path / "prompts"))
    assert lib.get(
        "eval.score", question="Q", expected_answer="E", actual_answer="A", transcript="T"
    ).startswith("MINE")
    # Un-overridden siblings still come from the packaged copies.
    assert "classifier" in lib.get("eval.classify", question="Q", reply="R")


def test_default_config_names_evalrun_not_eval() -> None:
    assert EvalConfig().log.file_path == "./evalrun.log"
