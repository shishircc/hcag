"""An eval CSV saved by a spreadsheet must still read (§6.7).

Excel and Google Sheets both write a UTF-8 **BOM** when you choose "CSV UTF-8".
Decoded as plain utf-8, those three bytes attach to the first header name:
`question_id` becomes `﻿question_id`, the required-column check fails, and
a perfectly good file is rejected as "missing required columns: ['question_id']".

Reported as "a CSV with newline breaks is not reading properly" — the multi-line
answers were a red herring, so both are pinned here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hcag.eval.csv_io import read_csv

HEADER = "question_id,kind,question,expected_answer,source,actual_answer,score,remark\n"

# expected_answer spans several lines inside one quoted field, the way an
# evalgen reference answer does.
ROWS = (
    'q-0001,simple,What are the conditions?,'
    '"MOM will grant it only if:\n\n1. **Related companies** — corporate shareholding.\n'
    '2. **Approval** — from the board.",https://example.gov/a,,,\n'
    'q-0002,hard-1,Second question?,Single line answer.,https://example.gov/b,,,\n'
)


def _write(path: Path, body: str, *, bom: bool) -> Path:
    raw = body.encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + raw)
    return path


def test_the_writers_emit_the_mark(tmp_path: Path) -> None:
    """An eval set is read by people before it is read by anything else, and the
    tool they open a `.csv` with is a spreadsheet. Without the mark, Excel for
    Mac falls back to the legacy system encoding and every em dash in a
    model-written answer arrives as `‚Äî`."""
    from hcag.evalgen.csv_writer import write_csv as write_generated
    from hcag.evalgen.generators import GeneratedItem
    from hcag.eval.csv_io import EvalRow, write_csv as write_scored

    generated = tmp_path / "eval.csv"
    write_generated(
        generated,
        [("q-0001", GeneratedItem("simple", "How long — roughly?", "Five days.", ["p.a"]))],
    )
    assert generated.read_bytes().startswith(b"\xef\xbb\xbf")

    scored = tmp_path / "scored.csv"
    write_scored(scored, [EvalRow("q-0001", "simple", "Q — ?", "A — B")])
    assert scored.read_bytes().startswith(b"\xef\xbb\xbf")


def test_the_mark_appears_once_and_the_em_dash_survives(tmp_path: Path) -> None:
    """The mark is a prefix, not a per-line escape, and it changes nothing about
    how the content itself is encoded."""
    from hcag.eval.csv_io import EvalRow, read_csv, write_csv

    out = tmp_path / "scored.csv"
    write_csv(out, [EvalRow("q-0001", "simple", "Q?", "Cancel within one week — no later.")])

    raw = out.read_bytes()
    assert raw.count(b"\xef\xbb\xbf") == 1
    assert raw.count("—".encode("utf-8")) == 1
    # And the file still reads back as itself.
    assert read_csv(out).rows[0].expected_answer.endswith("one week — no later.")


def test_a_generated_eval_set_round_trips_through_scoring(tmp_path: Path) -> None:
    """`evalgen` writes the mark and `evalrun` preserves it — an eval set that
    survives generation only to lose its em dashes at scoring time is no better
    off."""
    from hcag.evalgen.csv_writer import write_csv as write_generated
    from hcag.evalgen.generators import GeneratedItem
    from hcag.eval.csv_io import read_csv, write_csv as write_scored

    src = tmp_path / "eval.csv"
    write_generated(
        src,
        [("q-0001", GeneratedItem("simple", "Q — ?", "A — B", ["p.a"], [], "hr"))],
    )

    rows = read_csv(src).rows
    assert rows[0].question_id == "q-0001"   # not "\ufeffq-0001"
    assert rows[0].persona == "hr"

    out = tmp_path / "scored.csv"
    write_scored(out, rows)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert read_csv(out).rows[0].expected_answer == "A — B"


def test_a_bom_does_not_hide_the_first_column(tmp_path: Path) -> None:
    p = _write(tmp_path / "validation.csv", HEADER + ROWS, bom=True)

    result = read_csv(p)

    assert [r.question_id for r in result.rows] == ["q-0001", "q-0002"]
    assert result.rows[0].kind == "simple"


def test_the_same_file_without_a_bom_is_unchanged(tmp_path: Path) -> None:
    with_bom = read_csv(_write(tmp_path / "a.csv", HEADER + ROWS, bom=True))
    without = read_csv(_write(tmp_path / "b.csv", HEADER + ROWS, bom=False))

    assert [r.question_id for r in with_bom.rows] == [r.question_id for r in without.rows]
    assert with_bom.rows[0].expected_answer == without.rows[0].expected_answer


def test_newlines_inside_a_quoted_field_survive(tmp_path: Path) -> None:
    """The reported symptom: multi-line answers must arrive whole, not split
    into extra rows."""
    p = _write(tmp_path / "validation.csv", HEADER + ROWS, bom=True)

    rows = read_csv(p).rows

    assert len(rows) == 2, "a multi-line field must not become extra rows"
    answer = rows[0].expected_answer
    assert answer.count("\n") == 3
    assert answer.startswith("MOM will grant it only if:")
    assert answer.endswith("from the board.")


def test_a_genuinely_missing_column_still_raises_and_names_what_it_found(
    tmp_path: Path,
) -> None:
    p = _write(
        tmp_path / "bad.csv",
        "question_id,kind,question\nq-1,simple,Why?\n",
        bom=True,
    )

    with pytest.raises(ValueError) as e:
        read_csv(p)

    msg = str(e.value)
    assert "expected_answer" in msg
    # The header actually parsed is in the message, so the next person can see
    # at a glance whether a column is missing or merely misspelled.
    assert "found:" in msg and "question_id" in msg
