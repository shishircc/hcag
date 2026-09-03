"""`eval` carries `source` through and never shows it to the agent (§6.7.1, Part 7)."""

from __future__ import annotations

import csv
from pathlib import Path

from hcag.eval.csv_io import COLUMNS, REQUIRED_COLUMNS, EvalRow, read_csv, write_csv

SRC = "https://example.gov/a https://example.gov/a.png"


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def test_source_survives_a_read_write_round_trip(tmp_path: Path) -> None:
    """It was silently dropped before: the header check only verified required
    columns were present, so an 8-column file validated and then lost data."""
    src = tmp_path / "in.csv"
    _write(src, COLUMNS, [["q-0001", "simple", "Q?", "A", SRC, "", "", ""]])

    result = read_csv(src)
    assert result.rows[0].source == SRC

    out = tmp_path / "out.csv"
    write_csv(out, result.rows)
    rows = list(csv.reader(out.open(encoding="utf-8", newline="")))
    assert rows[0] == COLUMNS
    assert rows[1][COLUMNS.index("source")] == SRC


def test_a_pre_provenance_seven_column_csv_still_loads(tmp_path: Path) -> None:
    """Eval sets generated before provenance existed have no `source`.
    Refusing them would strand every eval set already in use."""
    assert "source" not in REQUIRED_COLUMNS

    src = tmp_path / "old.csv"
    _write(src, [c for c in COLUMNS if c != "source"],
           [["q-0001", "simple", "Q?", "A", "", "", ""]])

    result = read_csv(src)
    assert result.rows and result.rows[0].source == ""
    assert not result.warnings


def test_an_upgraded_file_gains_an_empty_source_column(tmp_path: Path) -> None:
    """Reading a 7-column file and writing it back produces 8 columns, so a
    stale eval set upgrades in place rather than needing regeneration."""
    src = tmp_path / "old.csv"
    _write(src, [c for c in COLUMNS if c != "source"],
           [["q-0001", "simple", "Q?", "A", "", "", ""]])

    out = tmp_path / "out.csv"
    write_csv(out, read_csv(src).rows)
    rows = list(csv.reader(out.open(encoding="utf-8", newline="")))
    assert rows[0] == COLUMNS
    assert rows[1] == ["q-0001", "simple", "Q?", "A", "", "", "", ""]


def test_source_is_positioned_after_expected_answer() -> None:
    assert COLUMNS.index("source") == COLUMNS.index("expected_answer") + 1


def test_source_is_never_sent_to_the_agent() -> None:
    """Feeding provenance to the agent would make the eval measure
    retrieval-with-hints rather than retrieval."""
    from hcag.eval.promptfoo_config import build_config
    from hcag.eval.config import EvalConfig

    row = EvalRow("q-0001", "simple", "Q?", "A", source=SRC)
    cfg = build_config([row], EvalConfig())

    assert cfg["prompts"] == ["{{question}}"]
    variables = cfg["tests"][0]["vars"]
    assert "source" not in variables
    assert SRC not in str(cfg)


def test_scoring_preserves_source(tmp_path: Path) -> None:
    """The runner mutates the input rows in place, so provenance rides through
    scoring rather than being rebuilt from the provider's output."""
    from hcag.eval.runner import _apply_results

    row = EvalRow("q-0001", "simple", "Q?", "A", source=SRC)
    merged = _apply_results(
        [row], {"q-0001": {"output": "answer", "metadata": {"score": 3, "remark": "ok"}}}
    )
    assert merged[0].row.source == SRC
    assert merged[0].row.score == 3
