"""`--ids` runs named rows and inherits the rest (§7.3.3, §7.7, §7.8).

The unit of iteration on an eval set is rarely the whole file: a prompt change
is made to fix the four rows that scored a 1. Re-running all hundred costs the
judge and, because it samples, re-rolls the rows nobody was questioning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hcag.eval.config import EvalConfig
from hcag.eval.csv_io import EvalRow
from hcag.eval.report import render_report
from hcag.eval.row_select import IdSelectionError, parse_ids
from hcag.eval.runner import ResolvedRun, RunError, _select_by_id


def _rows() -> list[EvalRow]:
    """Ids deliberately out of sort order: a merged eval set (§6.3) can be."""
    return [
        EvalRow("q-0001", "simple", "Q1?", "A", actual_answer="a1", score=3, remark="r1"),
        EvalRow("b-0002", "medium", "Q2?", "A", actual_answer="a2", score=1, remark="r2"),
        EvalRow("q-0003", "hard-1", "Q3?", "A", actual_answer="a3", score=0, remark="r3"),
        EvalRow("b-0004", "simple", "Q4?", "A", actual_answer="a4", score=2, remark="r4"),
    ]


# --- Parsing (§7.3.3) ------------------------------------------------------


def test_a_single_id_parses_as_a_one_row_span() -> None:
    assert parse_ids("q-0001").spans == (("q-0001", "q-0001"),)


def test_spans_and_singles_mix() -> None:
    spec = parse_ids("q-0001,b-0002..q-0003")
    assert spec.spans == (("q-0001", "q-0001"), ("b-0002", "q-0003"))


def test_a_trailing_comma_is_not_an_error() -> None:
    """The list usually comes out of a shell one-liner."""
    assert parse_ids("q-0001, q-0003,").spans == (
        ("q-0001", "q-0001"),
        ("q-0003", "q-0003"),
    )


@pytest.mark.parametrize("bad", ["a..b..c", "..b", "a..", ".."])
def test_a_malformed_span_is_rejected(bad: str) -> None:
    with pytest.raises(IdSelectionError):
        parse_ids(bad)


# --- Resolution (§7.3.3) ---------------------------------------------------


def test_ids_resolve_in_file_order_not_flag_order() -> None:
    rows = _rows()
    assert parse_ids("b-0004,q-0001").resolve(rows) == ["q-0001", "b-0004"]


def test_duplicates_collapse() -> None:
    assert parse_ids("q-0001,q-0001").resolve(_rows()) == ["q-0001"]


def test_a_span_covers_every_row_between_its_ends_by_position() -> None:
    """Ids are strings and may not sort in execution order, so the span is
    resolved against the file, not against the ids."""
    assert parse_ids("q-0001..q-0003").resolve(_rows()) == [
        "q-0001",
        "b-0002",
        "q-0003",
    ]


def test_unknown_ids_are_all_named_at_once() -> None:
    """A run that executed only the ids it recognised would report a fixed row
    as untouched."""
    with pytest.raises(IdSelectionError) as e:
        parse_ids("q-0001,q-9998,q-9999").resolve(_rows())
    assert "q-9998" in str(e.value) and "q-9999" in str(e.value)


def test_a_backwards_span_is_an_error() -> None:
    with pytest.raises(IdSelectionError) as e:
        parse_ids("q-0003..q-0001").resolve(_rows())
    assert "before it starts" in str(e.value)


def test_an_empty_selection_is_an_error() -> None:
    with pytest.raises(IdSelectionError) as e:
        parse_ids(" , ").resolve(_rows())
    assert "zero rows" in str(e.value)


# --- Splitting the run (§7.3.3) --------------------------------------------


def _resolved(tmp_path: Path, ids: str, **kw) -> ResolvedRun:
    return ResolvedRun(
        input_path=tmp_path / "in.csv",
        out_path=tmp_path / "out.csv",
        report_path=tmp_path / "report.html",
        ids=parse_ids(ids),
        **kw,
    )


def test_only_the_named_rows_execute_and_the_rest_are_inherited(tmp_path: Path) -> None:
    rows = _rows()
    run, inherited = _select_by_id(rows, _resolved(tmp_path, "b-0002,b-0004"))

    assert [r.question_id for r in run] == ["b-0002", "b-0004"]
    assert [r.question_id for r in inherited] == ["q-0001", "q-0003"]
    # Inherited rows keep what they were read with — no backend, no judge.
    assert [(r.actual_answer, r.score, r.remark) for r in inherited] == [
        ("a1", 3, "r1"),
        ("a3", 0, "r3"),
    ]


@pytest.mark.parametrize(
    "kw, flag",
    [
        ({"kinds": {"simple"}}, "--kinds"),
        ({"personas": {"hr"}}, "--personas"),
        ({"skip_completed": True}, "--skip-completed"),
    ],
)
def test_ids_does_not_compose_with_the_other_row_selectors(
    tmp_path: Path, kw: dict, flag: str
) -> None:
    """The operator enumerated the rows; a second selector could only drop some
    of them silently — `--skip-completed` most sharply, since the rows worth
    naming usually already scored badly."""
    with pytest.raises(RunError) as e:
        _select_by_id(_rows(), _resolved(tmp_path, "q-0001", **kw))
    assert flag in str(e.value) and "--ids" in str(e.value)


def test_an_unknown_id_aborts_the_run(tmp_path: Path) -> None:
    with pytest.raises(RunError) as e:
        _select_by_id(_rows(), _resolved(tmp_path, "q-4242"))
    assert "q-4242" in str(e.value)


# --- Report (§7.8) ---------------------------------------------------------


def _render(tmp_path: Path, inherited: set[str], scope: dict | None = None) -> str:
    out = tmp_path / "report.html"
    cfg = EvalConfig()
    render_report(
        rows_with_meta=[
            (r, {"inherited": True} if r.question_id in inherited else {})
            for r in _rows()
        ],
        baseline_rows=None,
        cfg=cfg,
        path=out,
        scope=scope,
    )
    return out.read_text(encoding="utf-8")


def test_the_report_marks_inherited_rows_and_says_the_run_was_partial(
    tmp_path: Path,
) -> None:
    html = _render(
        tmp_path,
        {"q-0001", "q-0003"},
        scope={"flag": "--ids", "spec": "b-0002,b-0004", "executed": 2, "inherited": 2},
    )

    assert 'data-scope="inherited"' in html
    assert 'data-scope="executed"' in html
    assert html.count('<span class="tag">inherited</span>') == 2
    assert "Partial run." in html
    assert "b-0002,b-0004" in html
    # and the table can be narrowed to just the rows that were re-run
    assert 'data-group="scope" data-value="executed"' in html


def test_unscored_inherited_rows_are_called_out_on_the_page(tmp_path: Path) -> None:
    html = _render(
        tmp_path,
        {"q-0001"},
        scope={"flag": "--ids", "spec": "q-0003", "executed": 1, "inherited": 3,
               "empty_inherited": 2},
    )
    assert "carry no score" in html


def test_a_whole_file_run_gets_no_partial_banner_and_no_scope_chips(
    tmp_path: Path,
) -> None:
    """An inherited count of zero is not a caveat worth a box on the page."""
    html = _render(tmp_path, set())

    assert "Partial run." not in html
    assert 'data-group="scope"' not in html
    assert "inherited" not in html.split("<h2>Rows</h2>")[0]


# --- Output CSV (§7.7) -----------------------------------------------------


def test_a_targeted_run_writes_the_whole_input_back(tmp_path: Path) -> None:
    """The point of a targeted re-run is to end up holding the complete eval
    set, so the output is a whole file ready to be re-run against again."""
    from hcag.eval.csv_io import read_csv, write_csv
    from hcag.eval.runner import RowResult, _assemble_output, _select_by_id

    rows = _rows()
    run, _ = _select_by_id(rows, _resolved(tmp_path, "b-0002"))
    run[0].actual_answer, run[0].score, run[0].remark = "fresh", 3, "now correct"

    out_rows = _assemble_output(
        rows, [RowResult(row=run[0], metadata={"score": 3})], whole_file=True
    )
    assert [r.question_id for r, _ in out_rows] == [
        "q-0001",
        "b-0002",
        "q-0003",
        "b-0004",
    ]

    out = tmp_path / "out.csv"
    write_csv(out, [r for r, _ in out_rows])
    written = read_csv(out).rows
    assert [(r.actual_answer, r.score) for r in written] == [
        ("a1", 3),
        ("fresh", 3),
        ("a3", 0),
        ("a4", 2),
    ]
    # Only the report is told which rows were inherited; the CSV keeps its nine
    # columns (§7.3.2).
    header = out.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "inherited" not in header


def test_the_out_path_may_be_the_input_path(tmp_path: Path) -> None:
    """Repeated targeted runs against one pair of files is the workflow the flag
    exists for, and the atomic write is what makes it safe."""
    from hcag.eval.csv_io import read_csv, write_csv
    from hcag.eval.runner import RowResult, _assemble_output, _select_by_id

    path = tmp_path / "scored.csv"
    write_csv(path, _rows())

    rows = read_csv(path).rows
    run, _ = _select_by_id(rows, _resolved(tmp_path, "q-0003"))
    run[0].score, run[0].actual_answer = 2, "better"
    write_csv(
        path,
        [
            r
            for r, _ in _assemble_output(
                rows, [RowResult(row=run[0])], whole_file=True
            )
        ],
    )

    after = read_csv(path).rows
    assert len(after) == 4
    assert after[2].score == 2 and after[2].actual_answer == "better"
    assert after[0].score == 3 and after[3].score == 2


def test_a_filtered_run_still_subsets_the_output(tmp_path: Path) -> None:
    """`--kinds` and `--personas` narrow the output file too — that composition
    is wrong for a resume, which is why `--ids` does not share it."""
    from hcag.eval.runner import RowResult, _assemble_output

    rows = _rows()
    out_rows = _assemble_output(
        rows, [RowResult(row=rows[1])], whole_file=False
    )
    assert [r.question_id for r, _ in out_rows] == ["b-0002"]
