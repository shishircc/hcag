"""The expanded row is the full record of a scored question (§7.8).

Two defects, reported together as "the remark column is not wide enough to read
and expanding it does not show the remark":

1. The detail row rendered Expected / Actual / Transcript — the judge's remark,
   which is the reason the row scored what it did, appeared nowhere but in a
   single ellipsised table cell.
2. `colspan` was one short of the header (6 of 7 columns, 7 of 8 with a
   baseline), so the expanded block stopped before the table's right edge.
"""

from __future__ import annotations

import re
from pathlib import Path

from hcag.eval.config import EvalConfig
from hcag.eval.csv_io import EvalRow
from hcag.eval.report import render_report

LONG_REMARK = (
    "The chatbot covers the related-company conditions and the ACRA records "
    "requirement, but omits the indirect-relationship case the reference answer "
    "treats as essential, so it reads as complete while leaving out a branch a "
    "user would need."
)


def _rows() -> list[EvalRow]:
    return [
        EvalRow(
            question_id="q-0001",
            kind="simple",
            question="Under what conditions will MOM grant an LOC?",
            expected_answer="Only if the companies are related.",
            source="https://example.gov/loc",
            actual_answer="MOM grants it when the companies are related.",
            score=2,
            remark=LONG_REMARK,
        ),
        EvalRow(
            question_id="q-0002",
            kind="hard-2",
            question="What is on the form?",
            expected_answer="A checkbox.",
            source="",
            actual_answer="",
            score=None,
            remark="",
        ),
    ]


def _render(tmp_path: Path, baseline: list[EvalRow] | None = None) -> str:
    out = tmp_path / "report.html"
    cfg = EvalConfig(
        input_path=tmp_path / "in.csv", out_path=tmp_path / "out.csv", report_path=out
    )
    render_report(
        rows_with_meta=[(r, {}) for r in _rows()],
        baseline_rows=baseline,
        cfg=cfg,
        path=out,
    )
    return out.read_text(encoding="utf-8")


def test_the_expanded_row_carries_the_whole_remark(tmp_path: Path) -> None:
    html = _render(tmp_path)

    assert "<b>Judge remark (scored 2):</b>" in html
    # verbatim, not the clipped cell copy
    assert f"<pre>{LONG_REMARK}</pre>" in html


def test_the_expanded_row_also_carries_the_question(tmp_path: Path) -> None:
    """An expanded row should read without scrolling back to the clipped cell."""
    html = _render(tmp_path)

    assert "<b>Question:</b>" in html
    assert "<b>Expected:</b>" in html and "<b>Actual:</b>" in html


def test_a_row_with_no_remark_says_so_rather_than_rendering_an_empty_block(
    tmp_path: Path,
) -> None:
    html = _render(tmp_path)

    assert "(none recorded)" in html


def test_the_detail_cell_spans_every_column(tmp_path: Path) -> None:
    html = _render(tmp_path)
    header = html.split("</thead>")[0]

    columns = len(re.findall(r"<th>", header))
    spans = {int(x) for x in re.findall(r'<td colspan="(\d+)"', html)}
    assert spans == {columns}, f"detail colspan {spans} != {columns} header columns"


def test_the_detail_cell_spans_every_column_with_a_baseline(tmp_path: Path) -> None:
    """The baseline run adds a Δ column, which the colspan has to follow."""
    html = _render(tmp_path, baseline=_rows())
    header = html.split("</thead>")[0]

    columns = len(re.findall(r"<th>", header))
    spans = {int(x) for x in re.findall(r'<td colspan="(\d+)"', html)}
    assert columns == 8
    assert spans == {columns}


def test_the_remark_column_wraps_instead_of_ellipsising_one_line(tmp_path: Path) -> None:
    html = _render(tmp_path)

    assert 'class="clip-remark"' in html
    assert "line-clamp: 3" in html
    # and the full text is reachable on hover without expanding
    assert f'title="{LONG_REMARK}"' in html
