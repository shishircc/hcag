"""`evalrun` carries `persona` through, filters on it, and reports by it (§6.7.2, §7.2, §7.8)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from hcag.eval.config import EvalConfig
from hcag.eval.csv_io import COLUMNS, REQUIRED_COLUMNS, EvalRow, read_csv, write_csv
from hcag.eval.report import render_report
from hcag.eval.runner import _filter_rows


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def test_persona_survives_a_read_write_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "in.csv"
    _write(src, COLUMNS, [["q-0001", "simple", "hr-professional", "Q?", "A", "", "", "", ""]])

    rows = read_csv(src).rows
    assert rows[0].persona == "hr-professional"

    out = tmp_path / "out.csv"
    write_csv(out, rows)
    written = list(csv.reader(out.open(encoding="utf-8", newline="")))
    assert written[0] == COLUMNS
    assert written[1][COLUMNS.index("persona")] == "hr-professional"


def test_a_pre_persona_eval_set_still_loads(tmp_path: Path) -> None:
    """Refusing eight-column files would strand every eval set already in use."""
    assert "persona" not in REQUIRED_COLUMNS

    src = tmp_path / "old.csv"
    _write(src, [c for c in COLUMNS if c != "persona"],
           [["q-0001", "simple", "Q?", "A", "https://x/a", "", "", ""]])

    result = read_csv(src)
    assert result.rows and result.rows[0].persona == ""
    assert result.rows[0].source == "https://x/a"
    assert not result.warnings


def test_reading_an_older_file_and_writing_it_back_upgrades_it(tmp_path: Path) -> None:
    src = tmp_path / "old.csv"
    _write(src, [c for c in COLUMNS if c != "persona"],
           [["q-0001", "simple", "Q?", "A", "", "", "", ""]])

    out = tmp_path / "out.csv"
    write_csv(out, read_csv(src).rows)
    written = list(csv.reader(out.open(encoding="utf-8", newline="")))
    assert written[0] == COLUMNS
    assert written[1] == ["q-0001", "simple", "", "Q?", "A", "", "", "", ""]


def test_persona_sits_after_kind() -> None:
    assert COLUMNS.index("persona") == COLUMNS.index("kind") + 1


def test_persona_is_never_sent_to_the_agent() -> None:
    """The role is already expressed in the question's wording. Passing it
    separately would test whether the agent can follow a label."""
    from hcag.eval.promptfoo_config import build_config

    row = EvalRow("q-0001", "simple", "Q?", "A", persona="hr-professional")
    cfg = build_config([row], EvalConfig())

    assert cfg["prompts"] == ["{{question}}"]
    assert "persona" not in cfg["tests"][0]["vars"]
    assert "hr-professional" not in str(cfg)


def test_scoring_preserves_persona() -> None:
    from hcag.eval.runner import _apply_results

    row = EvalRow("q-0001", "simple", "Q?", "A", persona="hr-professional")
    merged = _apply_results(
        [row], {"q-0001": {"output": "answer", "metadata": {"score": 3, "remark": "ok"}}}
    )
    assert merged[0].row.persona == "hr-professional"
    assert merged[0].row.score == 3


# --- Filtering (§7.3) ------------------------------------------------------


def _rows() -> list[EvalRow]:
    return [
        EvalRow("q-0001", "simple", "Q1?", "A", persona="hr"),
        EvalRow("q-0002", "simple", "Q2?", "A", persona="cand"),
        EvalRow("q-0003", "hard-1", "Q3?", "A", persona="hr"),
        EvalRow("q-0004", "simple", "Q4?", "A"),
    ]


def test_the_persona_filter_selects_one_role() -> None:
    kept = _filter_rows(_rows(), None, {"hr"}, False)
    assert [r.question_id for r in kept] == ["q-0001", "q-0003"]


def test_no_persona_filter_runs_every_row_including_persona_free() -> None:
    kept = _filter_rows(_rows(), None, None, False)
    assert len(kept) == 4


def test_the_persona_and_kind_filters_compose() -> None:
    kept = _filter_rows(_rows(), {"simple"}, {"hr"}, False)
    assert [r.question_id for r in kept] == ["q-0001"]


# --- Report (§7.8) ---------------------------------------------------------


def _render(tmp_path: Path, rows: list[EvalRow]) -> str:
    out = tmp_path / "report.html"
    cfg = EvalConfig(
        input_path=tmp_path / "in.csv", out_path=tmp_path / "out.csv", report_path=out
    )
    render_report(
        rows_with_meta=[(r, {}) for r in rows], baseline_rows=None, cfg=cfg, path=out
    )
    return out.read_text(encoding="utf-8")


def test_the_report_breaks_scores_down_by_persona(tmp_path: Path) -> None:
    """An overall pass rate can look healthy while one role's rows sit a point
    lower — invisible in every other panel on the page."""
    rows = [
        EvalRow("q-0001", "simple", "Q1?", "A", persona="hr", actual_answer="x", score=3),
        EvalRow("q-0002", "simple", "Q2?", "A", persona="cand", actual_answer="x", score=0),
    ]
    html = _render(tmp_path, rows)
    assert "Per-persona breakdown" in html
    assert "Persona × kind" in html
    assert "hr" in html and "cand" in html


def test_persona_free_rows_are_grouped_not_dropped(tmp_path: Path) -> None:
    rows = [
        EvalRow("q-0001", "simple", "Q1?", "A", persona="hr", actual_answer="x", score=3),
        EvalRow("q-0002", "simple", "Q2?", "A", actual_answer="x", score=1),
    ]
    html = _render(tmp_path, rows)
    assert "(unattributed)" in html


def test_a_persona_free_eval_set_gets_no_persona_section(tmp_path: Path) -> None:
    """An empty section would suggest the run lost something it never had."""
    rows = [EvalRow("q-0001", "simple", "Q1?", "A", actual_answer="x", score=3)]
    html = _render(tmp_path, rows)
    assert "Per-persona breakdown" not in html
