"""Read/write the 7-column eval CSV (§6.7, §7.7).

Same on-disk format as ``hcag.evalgen.csv_writer`` but supports the full
lifecycle — reading rows produced by ``evalgen``, and writing them back with
``actual_answer``, ``score``, and ``remark`` populated (atomically).
"""

from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal


Kind = Literal["simple", "medium", "complex", "hard-1", "hard-2"]

COLUMNS = [
    "question_id",
    "kind",
    "question",
    "expected_answer",
    "actual_answer",
    "score",
    "remark",
]

VALID_KINDS: set[str] = {"simple", "medium", "complex", "hard-1", "hard-2"}


@dataclass
class EvalRow:
    """One CSV row. `score` is Optional[int] (empty CSV cell => None)."""

    question_id: str
    kind: str
    question: str
    expected_answer: str
    actual_answer: str = ""
    score: int | None = None
    remark: str = ""

    def is_completed(self) -> bool:
        """A row is considered completed if it has both a score and an actual answer."""
        return self.score is not None and bool(self.actual_answer)


@dataclass
class ReadResult:
    rows: list[EvalRow] = field(default_factory=list)
    header_ok: bool = True
    warnings: list[str] = field(default_factory=list)


def read_csv(path: Path) -> ReadResult:
    """Read the 7-column eval CSV.

    Missing or misspelled columns raise; extra columns are ignored. Rows with
    unknown ``kind`` values pass through as-is with a warning — the runner
    decides whether to filter them.
    """
    result = ReadResult()
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in COLUMNS if c not in header]
        if missing:
            raise ValueError(
                f"input CSV is missing required columns: {missing}. Expected: {COLUMNS}"
            )
        for i, raw in enumerate(reader, start=2):  # line 1 is the header
            score_raw = (raw.get("score") or "").strip()
            score: int | None
            if score_raw == "":
                score = None
            else:
                try:
                    score = int(score_raw)
                except ValueError:
                    result.warnings.append(
                        f"line {i}: score={score_raw!r} is not an integer; treating as empty"
                    )
                    score = None
            row = EvalRow(
                question_id=(raw.get("question_id") or "").strip(),
                kind=(raw.get("kind") or "").strip(),
                question=raw.get("question") or "",
                expected_answer=raw.get("expected_answer") or "",
                actual_answer=raw.get("actual_answer") or "",
                score=score,
                remark=raw.get("remark") or "",
            )
            if not row.question_id:
                result.warnings.append(f"line {i}: empty question_id; skipping")
                continue
            if row.kind not in VALID_KINDS:
                result.warnings.append(
                    f"line {i} ({row.question_id}): unknown kind={row.kind!r}"
                )
            result.rows.append(row)
    return result


def write_csv(path: Path, rows: Iterable[EvalRow]) -> int:
    """Write rows atomically (temp file + rename) so a crash mid-run doesn't
    truncate the previous output. Returns the number of rows written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    n = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
            writer.writerow(COLUMNS)
            for row in rows:
                writer.writerow(
                    [
                        row.question_id,
                        row.kind,
                        row.question,
                        row.expected_answer,
                        row.actual_answer,
                        "" if row.score is None else str(row.score),
                        row.remark,
                    ]
                )
                n += 1
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return n
