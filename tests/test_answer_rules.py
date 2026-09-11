"""Prompt rules that were added after a specific wrong answer.

Two families, both pinned to the wording that fixed them:

- **Aboutness, not coverage** (§3.4.4) — a folder described by topics it only
  mentions, so a question routes to the folder that defers the rule instead of
  the one that holds it.
- **Verdict questions** (§2.7) — a reply that ruled "does not qualify" and then
  conceded a case-by-case provision the asker plausibly met.

Each rule here exists because a version without it shipped and was wrong. The
assertions are on the exact phrasing for that reason: a paraphrase that loses
the distinction loses the fix.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from hcag.cli import metadata_llm
from hcag.config import LLMConfig

_REPLY = json.dumps({"title": "T", "long_description": "l"})


def _prompt_for(**kwargs) -> str:
    seen: list[str] = []

    def _fake(cfg, prompt):  # noqa: ARG001
        seen.append(prompt)
        return _REPLY

    with patch.object(metadata_llm, "_complete", side_effect=_fake):
        metadata_llm.generate_folder_metadata(LLMConfig(), **kwargs)
    return seen[0]


# --- Fix 1: the ranking rule no longer inverts on a `mixed` ancestor -------


def test_prompt_ranks_by_own_content_not_by_depth() -> None:
    """`mixed` holds content its children do not, so a deeper entry never
    supersedes it. The old 'prefer the most specific entry' rule was true for a
    `node` ancestor and false for a `mixed` one."""
    from hcag.prompting import load_prompts

    prompt = load_prompts().get("agent.system", catalog="")

    assert "Prefer the most specific entries" not in prompt
    assert "not by how deep they sit" in prompt
    assert "neither depth wins by default" in prompt
    # Superseded by the situation rule: a specialised child carries the
    # governing version where the person named a situation.
    assert "the folder for THAT SITUATION governs" in prompt
    # And warns about the exact trap: a narrow child that matches keywords.
    # (Both directions of it now — see test_keyword_matching_is_warned_about
    # _in_both_directions, which is why this asserts the claim not the phrasing.)
    assert "may be a sub-document" in prompt
    assert "the broader topic above it may define the rule you actually need" in prompt
    # Depth decides nothing on its own; what the person is doing decides.
    assert "neither depth wins by default" in prompt
    # A folder holding nothing is absent rather than listed-and-empty (D3a).
    assert "has no row at all" in prompt


def test_cross_references_are_excluded_from_the_summary() -> None:
    """A description is read by something choosing ONE folder to open, so a topic
    it names is a promise that opening this folder answers questions about it.

    The case this comes from: `compass-c1-salary-benchmarks` is 47KB of COMPASS
    sector benchmark tables, and contains two cross-reference bullets — "candidates
    who do not meet the EP qualifying salary will not be eligible" and the $22,500
    COMPASS exemption. Both link to the *parent* `eligibility` folder, which is where
    the qualifying-salary tables actually live. The summarizer named them, accurately,
    and the resulting entry read "...with rules on EP qualifying salary and
    exemptions" — so a question about qualifying salary routed to the child that
    defers it instead of the parent that answers it.
    """
    prompt = _prompt_for(own_content="# C1\nBenchmarks.")

    # The distinction the summarizer has to make.
    assert "ABOUT" in prompt
    assert "merely references, defers, or links elsewhere for" in prompt

    # The test it applies to decide.
    assert "find the answer here — or only a pointer somewhere else?" in prompt

    # Cross-references to a parent are called out as the costly case, because the
    # referenced topic is precisely the one that should have routed elsewhere.
    assert "parent or sibling" in prompt

    # Proportion: a one-line caveat must not read like a co-equal subject.
    assert "single sentence or bullet" in prompt

    # A reference worth keeping is phrased as direction, not possession.
    assert "pointer it is" in prompt


def test_titles_lead_with_the_searchable_subject() -> None:
    """`eligibility` holds the qualifying-salary tables but was titled "Employment
    Pass Eligibility & COMPASS Framework" — neither "salary" nor "renewal" — while
    a sibling's title said "Salary Benchmarks by Sector". Titles carry lexical
    signal, so the one that reads like the answer wins."""
    prompt = _prompt_for(own_content="# E\nThreshold is $$11,800.")
    assert "the words a reader would search for" in prompt


# --- Verdict questions (§2.7) ----------------------------------------------


def test_the_verdict_rule_triggers_on_the_answer_not_the_question() -> None:
    """The rule was headed "can I" questions and so never fired on "does the
    company qualify as established?" — the same question about an employer."""
    from hcag.prompting import load_prompts

    prompt = load_prompts().get("agent.system", catalog="")

    assert "VERDICT QUESTIONS" in prompt
    assert '"CAN I" QUESTIONS' not in prompt
    # Named by what the reply will be, and by every subject it can rule on.
    assert "if you are about to say met or not met, this section governs" in prompt
    for subject in ("company", "salary", "document", "date"):
        assert subject in prompt.split("VERDICT QUESTIONS")[1][:600], subject


def test_a_route_is_any_provision_that_could_change_the_outcome() -> None:
    """The rescue in the reported reply was the same revenue test measured on a
    different basis, which a model told to "list routes" files as decoration."""
    from hcag.prompting import load_prompts

    prompt = load_prompts().get("agent.system", catalog="")

    assert "LIST EVERY WAY IN" in prompt
    assert "measured on a different basis" in prompt
    assert "combined amounts from the entire global office" in prompt
    assert "is one of these, not decoration on a settled answer" in prompt


def test_the_three_verdicts_are_named_and_the_fourth_forbidden() -> None:
    """"No, but it might be considered" evaded the flat-no ban on a
    technicality, so the permitted shapes are enumerated instead."""
    from hcag.prompting import load_prompts

    prompt = load_prompts().get("agent.system", catalog="")

    assert "MET --" in prompt
    assert "OPEN, NOT SETTLED --" in prompt
    assert "NOT MET --" in prompt
    assert "There is no fourth." in prompt
    assert "FORBIDDEN FOURTH" in prompt
    # And says what to do instead: the concession leads.
    assert "the concession is the answer, not a footnote to it" in prompt


def test_the_assessment_posture_comes_before_the_ruling() -> None:
    """It was already required "early"; the reply put it four paragraphs after
    a negative verdict, which is early enough to satisfy the old wording."""
    from hcag.prompting import load_prompts

    prompt = load_prompts().get("agent.system", catalog="")
    assert "say it plainly and FIRST, not after a ruling" in prompt


# --- Voice (§2.7) -----------------------------------------------------------


def _system() -> str:
    from hcag.prompting import load_prompts

    return load_prompts().get("agent.system", catalog="")


def test_the_voice_rule_is_a_test_not_a_list_of_phrases() -> None:
    """It shipped as six examples, every one naming an artifact, and the
    trailing "or any variant" was left carrying the rule alone."""
    prompt = _system()
    assert "NEVER NARRATE YOUR OWN WORKING" in prompt
    assert "The test is not whether a phrase names a system" in prompt
    assert "about YOU" in prompt and "about THEIR situation" in prompt


def test_the_two_families_that_were_getting_through_are_named() -> None:
    prompt = _system()
    # What you hold, or how sure you are: names no system, banned all the same.
    assert "based on the information I have" in prompt
    assert "your own state of knowledge is not a fact about their case" in prompt
    # What the reply is about to do: not covered by the old rule at all.
    assert "I need to give you both good and challenging news" in prompt
    assert "A preamble announcing the answer is not the answer" in prompt


def test_the_weaker_duplicate_is_gone() -> None:
    """Two statements of one rule that differ in strength is its own hazard:
    the weaker is satisfiable on its own terms, and it sat later in the file."""
    prompt = _system()
    assert "STYLE." not in prompt
    # Its one distinct clause survives where it belongs.
    assert "internal machinery: never mention them to the user" in prompt


# --- Reply shape (§2.7, §10.3) ---------------------------------------------


def test_the_word_limit_is_in_the_prompt_not_only_the_design() -> None:
    """It lived in DESIGN.md alone, so replies obeyed nothing written down."""
    prompt = _system()
    assert "50 to 80 words" in prompt
    assert "The limit is per reply, not an average" in prompt


def test_the_format_rule_bans_the_document_shape_not_markdown() -> None:
    """§10.3 renders assistant messages as Markdown and argues the pipeline
    preserves structure to that end. Both rules hold: a short list is fine, a
    page is not."""
    prompt = _system()
    assert "rendered as Markdown" in prompt
    assert "use them where they genuinely help" in prompt
    assert "no headings" in prompt and "no tables" in prompt
    assert "dumps every criterion at once" in prompt


# --- The load budget (§2.7.1) ----------------------------------------------


def test_the_budget_is_round_trips_not_packets() -> None:
    """It shipped as "ONE CALL PER TURN ... a turn gets one load", which reads
    as a one-packet budget. A reader of the design misread it that way."""
    prompt = _system()
    assert "ONE ROUND TRIP" in prompt
    assert "ONE CALL PER TURN" not in prompt
    assert "The limit is on round trips, NOT on packets" in prompt
    assert "one call carries as many ids as the question needs" in prompt


def test_recovery_is_licensed_by_loaded_content() -> None:
    """The rule that forbids dribbling also forbade the call you make because
    the content you loaded turned out not to hold the rule."""
    prompt = _system()
    assert "IF WHAT YOU LOADED DOES NOT HOLD THE ANSWER, LOAD AGAIN" in prompt
    assert "Not answering short" in prompt
    # And the licence is evidence, never a feeling, or reflex-calling reopens.
    assert "licenses the second call is the loaded content itself" in prompt
    assert "never a feeling that more might help" in prompt


def test_the_tool_description_agrees_with_the_system_prompt() -> None:
    """Both state the rule; two statements at different strengths is the
    failure mode this suite keeps finding."""
    from hcag.prompting import load_prompts

    tool = load_prompts().get("tool.check_and_load_kb")
    assert "ONE ROUND TRIP, EVERY ID" in tool
    assert "not on packets" in tool
    assert "call again with the id that does" in tool


def test_reasoning_is_licensed_and_silent_in_one_place() -> None:
    """The licence shipped alone, forty-five lines below "plan silently", and
    the looser statement won: a reply arrived with its deliberation on screen."""
    prompt = _system()
    reasoning = prompt.split("REASONING.")[1]
    assert "Reason freely" in reasoning
    assert "reason SILENTLY" in reasoning
    assert "the reply begins at the answer" in reasoning
    assert "no deliberation above a separator" in reasoning


# --- Hub pages (§3.4.4) -----------------------------------------------------


def test_the_summarizer_is_told_what_to_do_with_a_hub_page() -> None:
    """A page whose every sentence is a pointer has no passing reference to
    exclude, and a faithful summary of it competes with all nine children."""
    from hcag.prompting import load_prompts

    prompt = load_prompts().get("preprocess.folder_metadata", sections="X")
    assert "WHEN THE WHOLE DOCUMENT IS POINTERS" in prompt
    assert "mostly headings and links to other pages" in prompt
    assert "never as\nsubjects this folder covers" in prompt
    assert "A hub decides nothing" in prompt


# --- Choosing ids (§2.7.1) -------------------------------------------------


def test_a_description_is_not_treated_as_a_list_of_contents() -> None:
    """Four sentences cannot enumerate a 17 kB packet, so absence from a
    description is not absence from the packet."""
    prompt = _system()
    assert "A DESCRIPTION IS NOT A LIST OF CONTENTS" in prompt
    assert "never rule a folder out because its description does not happen to mention" in prompt
    assert "which folder OWNS that subject" in prompt


def test_keyword_matching_is_warned_about_in_both_directions() -> None:
    """Only the narrow-row direction was documented. A hub matches on mention,
    not on possession."""
    prompt = _system()
    assert "may be a sub-document" in prompt
    assert "lists every topic beneath it and decides none of them" in prompt
    assert "it matches because it mentions, not because it holds" in prompt


def test_ids_are_chosen_by_situation_then_by_subject() -> None:
    """The reported miss needed the renewal folder and the card-replacement
    folder; it loaded the hub that mentions both and decides neither."""
    prompt = _system()
    assert "WORK OUT THE IDS IN TWO STEPS" in prompt
    assert "WHAT SITUATION IS THIS PERSON IN?" in prompt
    assert "WHAT ARE THEY ACTUALLY ASKING WITHIN IT?" in prompt
    assert "you want both in the one call" in prompt
    # And the case where they are one folder: the situation-specific rule wins.
    assert "the situation folder holds the version that applies" in prompt
