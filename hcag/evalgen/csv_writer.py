"""Fixed 9-column CSV output per §6.7.

Columns, in order:
  question_id, kind, persona, question, expected_answer, source,
  actual_answer, score, remark

`evalgen` always writes the last three columns empty — they are populated by
a downstream evaluation pass.

`persona` sits after `kind` because that is where it reads. Columns are
addressed by header name, never by position — the header row is always present
precisely so that costs nothing.

Written as UTF-8 **with a byte-order mark** — see `write_csv`.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .generators import GeneratedItem


COLUMNS = [
    "question_id",
    "kind",
    "persona",
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
    """Write `(question_id, GeneratedItem)` pairs. Returns count written.

    `utf-8-sig`, not `utf-8`: the file gets a three-byte mark at the front.

    An eval set is read by people before it is read by anything else — §6.7.1
    exists entirely because a generated question needs a human pass — and the
    tool those people open a `.csv` with is a spreadsheet. Excel for Mac, given
    a plain UTF-8 file with nothing to identify it, falls back to the legacy
    system encoding, and every em dash in a model-written answer becomes
    `‚Äî`. The reference answers are full of them. The mark is what makes a
    double-click decode correctly.

    Nothing downstream is disturbed by it: `hcag.eval.csv_io` reads with
    `utf-8-sig` — it had to already, since a spreadsheet writes the same mark
    back — and so does the persona roster reader.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(COLUMNS)
        for qid, item in rows:
            writer.writerow([
                qid,
                item.kind,
                # Empty means persona-free (§6.7.2) — a supported mode, not a
                # missing value.
                item.persona_id,
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
