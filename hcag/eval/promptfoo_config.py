"""Build a ``promptfooconfig.yaml`` from an EvalConfig + row set (§7.6).

We deliberately keep the yaml minimal: one provider (our Python file), one
prompt (``{{question}}``), and one test per row. Row metadata rides in the
test's ``vars`` — the Python provider re-reads them from ``context.vars`` at
call time. Concurrency + output path are passed on the promptfoo CLI, not in
the yaml, since promptfoo's yaml surface for those has changed across
versions and CLI flags stay stable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from .config import EvalConfig
from .csv_io import EvalRow


PROVIDER_MODULE_PATH = Path(__file__).parent / "promptfoo_provider.py"


def build_config(
    rows: Iterable[EvalRow],
    cfg: EvalConfig,
    *,
    provider_path: Path | None = None,
) -> dict:
    """Return a plain-dict promptfoo config. Caller serializes it to yaml."""
    provider = (provider_path or PROVIDER_MODULE_PATH).resolve()
    tests = []
    for row in rows:
        tests.append(
            {
                "description": f"{row.question_id} [{row.kind}]",
                "vars": {
                    "question_id": row.question_id,
                    "kind": row.kind,
                    "question": row.question,
                    "expected_answer": row.expected_answer,
                },
                # promptfoo shows metadata in reports and lets us filter by kind.
                "metadata": {"kind": row.kind, "question_id": row.question_id},
            }
        )

    return {
        "description": cfg.report.title,
        "providers": [f"file://{provider}"],
        "prompts": ["{{question}}"],
        "tests": tests,
    }


def write_config(rows: Iterable[EvalRow], cfg: EvalConfig, path: Path) -> None:
    """Serialize the config to ``path`` as YAML."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            build_config(rows, cfg),
            f,
            sort_keys=False,
            allow_unicode=True,
            width=100,
        )
