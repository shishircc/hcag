"""Every turn reaches the CSV and the report (§7.4, §7.7, §7.8).

The exchange was recorded in three widths: the CSV held the closing reply, the
judge scored every reply joined, and the report held the transcript as a JSON
dump. So a row whose answer arrived over two turns showed a fragment in the CSV
next to a score computed from more than that, and the question "why was the
agent asked twice" could not be answered from either deliverable.
"""

from __future__ import annotations

import csv
from pathlib import Path

from hcag.eval.config import EvalConfig
from hcag.eval.csv_io import COLUMNS, REQUIRED_COLUMNS, EvalRow, read_csv, write_csv
from hcag.eval.loop import RowExchange, TranscriptTurn
from hcag.eval.report import render_report
from hcag.eval.runner import _apply_results


def _exchange() -> RowExchange:
    row = EvalRow("q-0001", "medium", "How long?", "One week.")
    ex = RowExchange(row=row, session_id="eval-q-0001-abcd1234")
    ex.turns = [
        TranscriptTurn(role="user", text="How long?", source="user"),
        TranscriptTurn(role="bot", text="Within one week.", source="bot", elapsed_ms=900),
        TranscriptTurn(role="user", text="And the fee?", source="clarifier"),
        TranscriptTurn(role="bot", text="It is 65.40.", source="bot_final", elapsed_ms=800),
    ]
    ex.turn_count = 2
    ex.actual_answer = "It is 65.40."
    ex.terminated_by = "answer"
    return ex


# --- CSV (§7.7) ------------------------------------------------------------


def test_the_schema_carries_the_turn_count_and_the_exchange() -> None:
    assert COLUMNS[-2:] == ["turns", "transcript"]
    # Optional, so an evalgen file is still readable.
    assert "turns" not in REQUIRED_COLUMNS and "transcript" not in REQUIRED_COLUMNS


def test_turns_and_transcript_survive_a_round_trip(tmp_path: Path) -> None:
    ex = _exchange()
    row = ex.row
    row.turns = 2
    row.transcript = ex.transcript_text()

    out = tmp_path / "out.csv"
    write_csv(out, [row])
    back = read_csv(out).rows[0]

    assert back.turns == 2
    assert "USER (clarifier): And the fee?" in back.transcript
    assert "Within one week." in back.transcript and "It is 65.40." in back.transcript


def test_an_evalgen_file_without_the_columns_still_loads(tmp_path: Path) -> None:
    src = tmp_path / "old.csv"
    with src.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow([c for c in COLUMNS if c not in ("turns", "transcript")])
        w.writerow(["q-0001", "simple", "", "Q?", "A", "", "", "", ""])

    row = read_csv(src).rows[0]
    assert row.turns is None and row.transcript == ""


def test_a_non_integer_turn_count_warns_rather_than_raising(tmp_path: Path) -> None:
    src = tmp_path / "bad.csv"
    with src.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLUMNS)
        w.writerow(["q-0001", "simple", "", "Q?", "A", "", "a", "3", "r", "two", "T"])

    result = read_csv(src)
    assert result.rows[0].turns is None
    assert any("turns=" in w for w in result.warnings)


def test_the_runner_fills_both_columns_from_the_provider() -> None:
    row = EvalRow("q-0001", "medium", "How long?", "One week.")
    merged = _apply_results(
        [row],
        {
            "q-0001": {
                "output": "[part 1 of 2]\nWithin one week.\n\n[part 2 of 2]\nIt is 65.40.",
                "metadata": {
                    "score": 3,
                    "remark": "ok",
                    "bot_replies": 2,
                    "transcript_text": "USER: How long?\n\nBOT: Within one week.",
                },
            }
        },
    )
    assert merged[0].row.turns == 2
    assert merged[0].row.transcript.startswith("USER: How long?")
    # And the CSV's answer is what was scored, not the closing fragment.
    assert "[part 1 of 2]" in merged[0].row.actual_answer


def test_a_row_with_no_result_claims_no_turns() -> None:
    row = EvalRow("q-0001", "medium", "How long?", "One week.")
    merged = _apply_results([row], {})
    assert merged[0].row.turns is None
    assert merged[0].row.transcript == ""


# --- Report (§7.8) ---------------------------------------------------------


def _render(tmp_path: Path, rows_with_meta) -> str:
    out = tmp_path / "report.html"
    render_report(
        rows_with_meta=rows_with_meta, baseline_rows=None, cfg=EvalConfig(), path=out
    )
    return out.read_text(encoding="utf-8")


def _scored(qid: str, turns: int | None) -> EvalRow:
    return EvalRow(qid, "medium", "Q?", "A", actual_answer="x", score=3, remark="r", turns=turns)


def test_the_table_shows_a_turns_column_and_flags_multi_turn_rows(tmp_path: Path) -> None:
    """One reply is the ordinary case; more means the clarifier stepped in, and
    that changes how the row should be read."""
    html = _render(
        tmp_path,
        [
            (_scored("q-0001", 1), {"bot_replies": 1}),
            (_scored("q-0002", 3), {"bot_replies": 3}),
        ],
    )
    assert "<th>Turns</th>" in html
    assert '<td class="turns-cell">1</td>' in html
    assert 'class="turns-cell multi"' in html
    assert "clarifier drove 2 extra turn(s)" in html


def test_the_transcript_is_rendered_turn_by_turn_with_labels(tmp_path: Path) -> None:
    ex = _exchange()
    meta = {
        "bot_replies": 2,
        "transcript": [
            {"role": t.role, "text": t.text, "source": t.source, "elapsed_ms": t.elapsed_ms}
            for t in ex.turns
        ],
    }
    html = _render(tmp_path, [(_scored("q-0001", 2), meta)])

    assert "User (from the eval set)" in html
    assert "User (written by the clarifier)" in html
    assert "Agent (final)" in html
    assert "Within one week." in html and "It is 65.40." in html


def test_a_report_built_from_a_csv_alone_falls_back_to_the_column(tmp_path: Path) -> None:
    """No promptfoo metadata — a re-render from a scored CSV still shows it."""
    row = _scored("q-0001", 2)
    row.transcript = "USER: How long?\n\nBOT: Within one week."
    html = _render(tmp_path, [(row, {})])
    assert "USER: How long?" in html


def test_a_row_with_no_transcript_says_so(tmp_path: Path) -> None:
    html = _render(tmp_path, [(_scored("q-0001", None), {})])
    assert "(no transcript recorded)" in html
    assert '<td class="muted">—</td>' in html


# --- Completeness (§7.7.1) -------------------------------------------------


def _run_with(monkeypatch, replies, classifications=("answer",), clarifications=("and?",)):
    """Drive `run_row` against a stubbed backend, classifier and clarifier."""
    from hcag.eval import loop as loop_mod
    from hcag.eval.backend import ChatResponse

    sent = iter(replies)
    cls = iter(classifications)
    clar = iter(clarifications)

    def fake_chat(self, session, message):  # noqa: ANN001, ARG001
        return next(sent)

    monkeypatch.setattr(loop_mod.BackendClient, "chat", fake_chat)
    monkeypatch.setattr(
        loop_mod, "classify_response",
        lambda **kw: loop_mod.ClassifyResult(category=next(cls)),
    )

    class _Clar:
        def __init__(self, text, error=""):
            self.text = text
            self.error = error

    monkeypatch.setattr(
        loop_mod, "generate_clarification", lambda **kw: _Clar(*next(clar))
    )
    cfg = EvalConfig()
    return loop_mod.run_row(EvalRow("q-0001", "simple", "How long?", "One week."), cfg)


def test_a_backend_failure_appears_in_the_transcript(monkeypatch) -> None:
    """It used to live only in `actual_answer`, so the transcript ended on a
    user turn with nothing after it — indistinguishable from a lost reply."""
    from hcag.eval.backend import ChatResponse

    ex = _run_with(
        monkeypatch,
        [ChatResponse(text="", elapsed_ms=12.0, http_status=503, error="http_503: boom")],
    )
    assert ex.terminated_by == "backend_error"
    assert "EVALRUN: [backend_error] http_503: boom" in ex.transcript_text()
    # and a failure is not a reply
    assert ex.reply_count() == 0


def test_a_timeout_is_labelled_as_one(monkeypatch) -> None:
    from hcag.eval.backend import ChatResponse

    ex = _run_with(
        monkeypatch,
        [ChatResponse(text="", elapsed_ms=60.0, http_status=0, error="timeout: read timed out")],
    )
    assert ex.terminated_by == "backend_timeout"
    assert "[backend_timeout]" in ex.transcript_text()


def test_hitting_the_turn_limit_says_so_in_the_transcript(monkeypatch) -> None:
    from hcag.eval.backend import ChatResponse

    cfg_turns = EvalConfig().loop.max_turns
    replies = [
        ChatResponse(text=f"which one? ({i})", elapsed_ms=5.0, http_status=200)
        for i in range(cfg_turns)
    ]
    ex = _run_with(
        monkeypatch,
        replies,
        classifications=["clarify"] * cfg_turns,
        clarifications=[("the trading desk",)] * cfg_turns,
    )
    assert ex.terminated_by == "max_turns_exceeded"
    text = ex.transcript_text()
    assert "[max_turns_exceeded]" in text
    # every reply and every clarifier turn is there, not just the last
    assert "which one? (0)" in text and f"which one? ({cfg_turns - 1})" in text
    assert text.count("USER (clarifier):") == cfg_turns - 1
    assert ex.reply_count() == cfg_turns


def test_a_clarifier_failure_appears_in_the_transcript(monkeypatch) -> None:
    from hcag.eval.backend import ChatResponse

    ex = _run_with(
        monkeypatch,
        [ChatResponse(text="which sector?", elapsed_ms=5.0, http_status=200)],
        classifications=["clarify"],
        clarifications=[("", "rate_limited")],
    )
    assert ex.terminated_by == "classifier_error"
    assert "EVALRUN: [clarifier_failed] rate_limited" in ex.transcript_text()


def test_an_error_turn_is_never_scored_as_an_answer(monkeypatch) -> None:
    """The sentinel must reach the judge as the whole answer, not as one part
    of a two-part exchange."""
    from hcag.eval.backend import ChatResponse

    ex = _run_with(
        monkeypatch,
        [ChatResponse(text="", elapsed_ms=1.0, http_status=500, error="http_500: x")],
    )
    assert ex.answer_text("conversation") == "[backend_error] http_500: x"


def test_the_report_marks_the_failure_turn(tmp_path: Path) -> None:
    row = _scored("q-0001", 0)
    meta = {
        "bot_replies": 0,
        "transcript": [
            {"role": "user", "text": "How long?", "source": "user", "elapsed_ms": 0},
            {"role": "system", "text": "[backend_timeout]", "source": "error", "elapsed_ms": 0},
        ],
    }
    html = _render(tmp_path, [(row, meta)])
    assert "evalrun (why it stopped)" in html
    assert "turn-error" in html
