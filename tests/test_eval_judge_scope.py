"""The judge scores the whole exchange, not the closing fragment (§7.5).

The agent answers in 50-80 word parts across turns (§2.7). Judging only the
reply the classifier called an "answer" measures its pacing rather than its
content: a complete answer delivered in four parts would be scored on its
fourth.
"""

from __future__ import annotations

from hcag.eval.csv_io import EvalRow
from hcag.eval.loop import RowExchange, TranscriptTurn


def _row() -> EvalRow:
    return EvalRow(
        question_id="q-1",
        kind="simple",
        question="How do I apply?",
        expected_answer="Consent, then form, then fee.",
        source="",
        actual_answer="",
        score=None,
        remark="",
    )


def _exchange(*bot_texts: str, terminated_by: str = "answer") -> RowExchange:
    ex = RowExchange(row=_row(), session_id="s")
    for i, text in enumerate(bot_texts):
        ex.turns.append(TranscriptTurn(role="user", text=f"user turn {i}", source="user"))
        ex.turns.append(TranscriptTurn(role="bot", text=text, source="bot"))
    ex.actual_answer = bot_texts[-1] if bot_texts else ""
    ex.terminated_by = terminated_by
    return ex


def test_a_multi_part_answer_is_judged_whole() -> None:
    ex = _exchange("First get consent.", "Then submit the form.", "The fee is $105.")

    text = ex.answer_text("conversation")

    assert "First get consent." in text
    assert "Then submit the form." in text
    assert "The fee is $105." in text
    assert "[part 1 of 3]" in text and "[part 3 of 3]" in text


def test_final_scope_keeps_the_old_single_reply_behaviour() -> None:
    """Any benchmark recorded before this option was measured this way."""
    ex = _exchange("First get consent.", "Then submit the form.", "The fee is $105.")

    assert ex.answer_text("final") == "The fee is $105."


def test_a_single_reply_is_not_dressed_up_with_part_labels() -> None:
    ex = _exchange("Everything you need, in one go.")

    assert ex.answer_text("conversation") == "Everything you need, in one go."


def test_a_hard_failure_still_reaches_the_judge_verbatim() -> None:
    """The rubric scores these 0 by matching the sentinel, so it must survive
    both scopes -- and the sentinel lives outside the bot turns."""
    ex = RowExchange(row=_row(), session_id="s")
    ex.turns.append(TranscriptTurn(role="user", text="How do I apply?", source="user"))
    ex.actual_answer = "[backend_timeout] read timed out"
    ex.terminated_by = "backend_timeout"

    assert ex.answer_text("conversation") == "[backend_timeout] read timed out"
    assert ex.answer_text("final") == "[backend_timeout] read timed out"


def test_max_turns_exceeded_is_not_reassembled_into_a_passing_answer() -> None:
    ex = _exchange("Part one.", "Part two.", terminated_by="max_turns_exceeded")
    ex.actual_answer = "[max_turns_exceeded] last_response='Part two.'"

    assert ex.answer_text("conversation").startswith("[max_turns_exceeded]")


def test_a_refusal_is_judged_on_the_whole_exchange_too() -> None:
    ex = _exchange("Here is the salary bar.", "I can't help with that.",
                   terminated_by="refusal")

    text = ex.answer_text("conversation")
    assert "Here is the salary bar." in text and "I can't help with that." in text


def test_conversation_is_the_default_scope() -> None:
    from hcag.eval.config import EvalConfig, JudgeConfig

    assert JudgeConfig().scope == "conversation"
    assert EvalConfig(input_path="a.csv", out_path="b.csv", report_path="c.html").judge.scope == (
        "conversation"
    )


def test_the_judge_prompt_says_to_score_the_whole_exchange() -> None:
    from pathlib import Path

    raw = Path("hcag/prompts/eval/score.md").read_text(encoding="utf-8")
    prompt = " ".join(raw.split())

    assert "Score the chatbot's answer AS A WHOLE" in prompt
    assert "[part N of M]" in prompt
    # Chatbot-driven turns are its job; user-driven steering is the penalty.
    assert "Do not deduct for it" in prompt
    assert "The USER having to steer" in prompt
