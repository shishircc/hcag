"""Fixed 7-column CSV output per §6.7.

Columns, in order:
  question_id, kind, question, expected_answer, source, actual_answer, score, remark

`evalgen` always writes the last three columns empty — they are populated by
a downstream evaluation pass.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .generators import GeneratedItem


COLUMNS = [
    "question_id",
    "kind",
    "question",
    "expected_answer",
    "source",
    "actual_answer",
    "score",
    "remark",
]


def question_id(prefix: str, index: int, width: int = 4) -> str:
    """Stable id like `q-0001` (prefix `q`, 1-based index, zero-padded)."""
    return f"{prefix}-{str(index).zfill(width)}"


def write_csv(path: Path, rows: Iterable[tuple[str, GeneratedItem]]) -> int:
    """Write `(question_id, GeneratedItem)` pairs. Returns count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(COLUMNS)
        for qid, item in rows:
            writer.writerow([
                qid,
                item.kind,
                item.question,
                item.expected_answer,
                # Space-separated: unambiguous because a URL cannot
                # contain an unescaped space (§6.7.1).
                " ".join(item.source_urls),
                "",  # actual_answer — populated during evaluation
                "",  # score — populated during evaluation
                "",  # remark — populated during evaluation
            ])
            n += 1
    return n
