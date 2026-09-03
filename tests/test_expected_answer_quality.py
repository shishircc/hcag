"""Expected answers must be complete, not extractable (§6.4)."""

from __future__ import annotations

import pytest

from hcag.evalgen.generators import GenerationError, _check_grounded
from hcag.prompting import load_prompts

BODY = (
    "The EP qualifying salary starts at $5,600 for candidates aged 23 or below in all "
    "sectors except financial services, and rises progressively with age to $10,700 at "
    "age 45 and above. For financial services it starts at $6,200 and rises to $11,800. "
    "From 1 January 2027 these thresholds increase. Candidates must also pass COMPASS "
    "unless exempted, scoring at least 40 points across six criteria."
)


# --- The completeness standard --------------------------------------------


def test_every_kind_carries_the_completeness_rules() -> None:
    """One file, five kinds: duplicating the quality bar is how four of them
    silently drift from the fifth."""
    lib = load_prompts()
    rules = lib.get("evalgen.answer_rules")
    rendered = {
        "evalgen.simple": lib.get("evalgen.simple", content="C", answer_rules=rules),
        "evalgen.medium": lib.get(
            "evalgen.medium", packet_id="p", paragraph="P", answer_rules=rules
        ),
        "evalgen.complex": lib.get(
            "evalgen.complex", packet_id="p", paragraphs="P", answer_rules=rules
        ),
        "evalgen.hard1": lib.get(
            "evalgen.hard1", packet_a_id="a", packet_b_id="b",
            paragraphs_a="A", paragraphs_b="B", answer_rules=rules,
        ),
        "evalgen.hard2": lib.get(
            "evalgen.hard2", packet_id="p", content="C", answer_rules=rules
        ),
    }
    for name, text in rendered.items():
        assert "COMPLETENESS" in text, name


def test_the_rules_forbid_a_bare_conditional_figure() -> None:
    """The failure that motivated this: a minimum presented as the answer when
    it is really the floor of an age- and sector-dependent scale."""
    rules = load_prompts().get("evalgen.answer_rules")
    assert "wrong answer, not a short\none" in rules
    assert "floor of a scale" in rules
    assert "Length follows completeness" in rules
    # Comprehensive must not become an invitation to invent.
    assert "Grounded, never invented" in rules


def test_no_kind_still_asks_for_a_short_or_verbatim_answer() -> None:
    lib = load_prompts()
    for name in ("simple", "medium", "complex", "hard1", "hard2"):
        text = lib.get(f"evalgen.{name}", **{
            k: "X" for k in load_prompts().specs[f"evalgen.{name}"].required
        })
        assert "verbatim" not in text.lower(), name
        assert "short answer" not in text.lower(), name


def test_simple_is_about_the_question_not_the_answer() -> None:
    """'No reasoning' describes how the reader gets there, not how much they get."""
    text = load_prompts().get("evalgen.simple", content="C", answer_rules="R")
    assert "requires no reasoning" in text
    assert "describes the QUESTION, not the ANSWER" in text


# --- Grounding replaces extraction ----------------------------------------


def test_a_comprehensive_answer_is_accepted() -> None:
    """The old verbatim test rejected exactly this: an answer assembled from
    several places and phrased to join them appears nowhere literally."""
    answer = (
        "The minimum depends on age and sector. Outside financial services it starts at "
        "$5,600 for candidates aged 23 or below and rises progressively to $10,700 at age "
        "45 and above; financial services starts at $6,200 and rises to $11,800."
    )
    assert answer not in BODY  # not extractable…
    _check_grounded(answer, BODY)  # …but grounded


def test_a_short_answer_is_still_accepted() -> None:
    """Completeness is enforced by the prompt; the validator only guards
    invention, so it must not start rejecting brevity."""
    _check_grounded("Candidates need at least $5,600 a month.", BODY)


def test_an_invented_figure_is_rejected() -> None:
    """Numbers are the facts a wrong expected answer does the most damage with,
    so this check is exact."""
    with pytest.raises(GenerationError, match="numbers absent"):
        _check_grounded("The minimum qualifying salary is $9,999 per month.", BODY)


def test_an_off_topic_answer_is_rejected() -> None:
    with pytest.raises(GenerationError, match="not grounded"):
        _check_grounded(
            "Refunds settle within five business days once approval completes.", BODY
        )


def test_punctuation_around_a_figure_is_not_a_mismatch() -> None:
    """`45.` in the packet and `45,` in the answer are the same number."""
    _check_grounded("It rises to $10,700 at age 45, which is the top of the scale.", BODY)


def test_a_very_short_answer_is_not_judged_on_word_overlap() -> None:
    """Three tokens is noise, not evidence — the numbers check carries it."""
    _check_grounded("$5,600.", BODY)


def test_an_empty_answer_is_rejected() -> None:
    with pytest.raises(GenerationError, match="empty"):
        _check_grounded("   ", BODY)
