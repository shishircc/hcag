"""Orchestrator: read CSV -> promptfoo eval -> completed CSV + HTML report.

Flow (§7.3 + §7.6):

1. Read + validate input CSV.
2. Filter rows by ``--kinds`` and ``--skip-completed`` if requested.
3. Probe the backend once with ``GET /health``; abort at startup on failure.
4. Serialize the resolved ``EvalConfig`` to a temp JSON file the provider
   will read at test time (via ``HCAG_EVAL_CONFIG_JSON``).
5. Emit a ``promptfooconfig.yaml`` in the tempdir; invoke
   ``npx promptfoo eval --config <yaml> --output <json>``.
6. Parse promptfoo's JSON output. Merge per-row results back into input order.
7. Write the completed CSV atomically, then render the HTML report.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..logger import HcagLogger
from .backend import BackendClient
from .config import EvalConfig
from .csv_io import EvalRow, VALID_KINDS, read_csv, write_csv
from .promptfoo_config import PROVIDER_MODULE_PATH, write_config
from .report import render_report


@dataclass
class ResolvedRun:
    input_path: Path
    out_path: Path
    report_path: Path
    kinds: set[str] | None = None
    skip_completed: bool = False


@dataclass
class RowResult:
    """The eval outcome for one input row, ready for CSV + report."""

    row: EvalRow
    metadata: dict[str, Any] = field(default_factory=dict)


class RunError(RuntimeError):
    """Raised for any ERROR-level condition per §7.10."""


def _resolve_promptfoo_bin() -> list[str]:
    """Return the argv prefix for invoking promptfoo.

    ``HCAG_PROMPTFOO_BIN`` overrides the default. Default is ``npx -y promptfoo@latest``,
    which is zero-install but re-verifies the package on every invocation. If
    that's too slow, users can ``npm i -g promptfoo`` and set the env var.
    """
    override = os.environ.get("HCAG_PROMPTFOO_BIN", "").strip()
    if override:
        return override.split()
    if shutil.which("promptfoo"):
        return ["promptfoo"]
    if shutil.which("npx"):
        return ["npx", "-y", "promptfoo@latest"]
    raise RunError(
        "promptfoo is not available. Install with `npm i -g promptfoo` and re-run, "
        "or set HCAG_PROMPTFOO_BIN to a launcher command."
    )


def _filter_rows(
    rows: list[EvalRow],
    kinds: set[str] | None,
    skip_completed: bool,
) -> list[EvalRow]:
    out = []
    for r in rows:
        if kinds and r.kind not in kinds:
            continue
        if skip_completed and r.is_completed():
            continue
        out.append(r)
    return out


def _write_provider_config(cfg: EvalConfig, workdir: Path) -> Path:
    path = workdir / "eval-config.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(cfg.model_dump(mode="json"), f)
    return path


def _extract_result_map(promptfoo_json: dict) -> dict[str, dict[str, Any]]:
    """Pull one entry per question_id from promptfoo's output.

    promptfoo has changed its JSON shape across versions; we probe the two
    common layouts (``results`` at top level vs. nested under ``results.results``).
    """
    candidates: list[list[dict[str, Any]]] = []
    if isinstance(promptfoo_json.get("results"), list):
        candidates.append(promptfoo_json["results"])
    if isinstance(promptfoo_json.get("results"), dict):
        inner = promptfoo_json["results"].get("results")
        if isinstance(inner, list):
            candidates.append(inner)
    if not candidates:
        raise RunError("could not find results[] in promptfoo output JSON")

    entries = candidates[0]
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        vars_ = entry.get("vars") or (entry.get("testCase") or {}).get("vars") or {}
        qid = str(vars_.get("question_id") or "")
        response = entry.get("response") or {}
        # promptfoo also mirrors the output at the top level in some versions.
        output = response.get("output") if isinstance(response, dict) else None
        if output is None:
            output = entry.get("response", "") if isinstance(entry.get("response"), str) else ""
        meta = (response.get("metadata") if isinstance(response, dict) else None) or {}
        if not qid:
            # No question_id — skip; runner will treat as missing (score empty).
            continue
        out[qid] = {"output": output or "", "metadata": meta}
    return out


def _apply_results(rows: list[EvalRow], results: dict[str, dict[str, Any]]) -> list[RowResult]:
    """Merge promptfoo results back onto input rows in original order."""
    out: list[RowResult] = []
    for row in rows:
        entry = results.get(row.question_id)
        if entry is None:
            row.actual_answer = "[no_result] promptfoo did not return a result for this row"
            row.score = None
            row.remark = "[no_result] see eval.log for details"
            out.append(RowResult(row=row, metadata={}))
            continue
        meta = entry.get("metadata") or {}
        row.actual_answer = entry.get("output") or ""
        raw_score = meta.get("score")
        row.score = raw_score if isinstance(raw_score, int) and raw_score in (0, 1, 2, 3) else None
        row.remark = str(meta.get("remark") or "").strip()
        if row.score is None and not row.remark:
            err = meta.get("judge_error") or "no_score_returned"
            row.remark = f"[judge_failed] {err}"
        out.append(RowResult(row=row, metadata=meta))
    return out


def run_eval(cfg: EvalConfig, resolved: ResolvedRun, logger: HcagLogger) -> dict[str, Any]:
    """End-to-end run. Returns a summary dict for the CLI to print."""
    started = time.monotonic()

    if not resolved.input_path.is_file():
        raise RunError(f"input CSV not found: {resolved.input_path}")

    read = read_csv(resolved.input_path)
    for w in read.warnings:
        logger.warn("eval.csv.warning", detail=w)
    if not read.rows:
        raise RunError("input CSV has no rows")

    all_rows = _filter_rows(read.rows, resolved.kinds, resolved.skip_completed)
    if not all_rows:
        raise RunError("no rows matched the --kinds / --skip-completed filters")

    if resolved.kinds:
        unknown = resolved.kinds - VALID_KINDS
        if unknown:
            raise RunError(f"unknown kinds in --kinds filter: {sorted(unknown)}")

    logger.info(
        "eval.start",
        input=str(resolved.input_path),
        rows=len(all_rows),
        by_kind={k: sum(1 for r in all_rows if r.kind == k) for k in VALID_KINDS},
        backend_url=cfg.backend.url,
        classifier_model=cfg.classifier.llm.model,
        judge_model=cfg.judge.llm.model,
        concurrency=cfg.run.concurrency,
        seed=cfg.run.seed,
    )

    backend = BackendClient(
        url=cfg.backend.url,
        chat_path=cfg.backend.chat_path,
        request_timeout=cfg.backend.request_timeout,
        retries=cfg.backend.retries,
    )
    ok, err = backend.health()
    if not ok:
        raise RunError(
            f"backend health check failed: {err}. Start the chatbot at {cfg.backend.url} "
            "before running `eval`."
        )

    with tempfile.TemporaryDirectory(prefix="hcag-eval-") as workdir_s:
        workdir = Path(workdir_s)
        cfg_json_path = _write_provider_config(cfg, workdir)
        config_yaml_path = workdir / "promptfooconfig.yaml"
        results_json_path = workdir / "promptfoo-results.json"
        write_config(all_rows, cfg, config_yaml_path)

        env = os.environ.copy()
        env["HCAG_EVAL_CONFIG_JSON"] = str(cfg_json_path)
        # promptfoo spawns its own Python for the provider. Pin it to the same
        # interpreter that's running the CLI so the `hcag` package is on sys.path
        # for the provider's absolute imports.
        env.setdefault("PROMPTFOO_PYTHON", sys.executable)
        if cfg.backend.session_scope == "per-run":
            env["HCAG_EVAL_SHARED_SESSION_ID"] = f"eval-run-{int(time.time())}"

        argv = _resolve_promptfoo_bin() + [
            "eval",
            "--config", str(config_yaml_path),
            "--output", str(results_json_path),
            "-j", str(cfg.run.concurrency),
            "--no-cache",
            "--no-progress-bar",
        ]

        logger.info("eval.promptfoo.spawn", argv=argv)
        proc = subprocess.run(
            argv,
            env=env,
            cwd=str(workdir),
            capture_output=True,
            text=True,
        )
        if proc.returncode not in (0, 100):
            # promptfoo returns 100 when some tests failed assertions — that's OK,
            # we score independently. Any other non-zero is an infra failure.
            logger.error(
                "eval.promptfoo.failed",
                returncode=proc.returncode,
                stdout=proc.stdout[-2000:],
                stderr=proc.stderr[-2000:],
            )
            raise RunError(
                f"promptfoo eval exited with {proc.returncode}. See eval.log for stderr."
            )

        if not results_json_path.is_file():
            raise RunError(
                f"promptfoo did not write {results_json_path}. Command stderr:\n{proc.stderr[-2000:]}"
            )
        with results_json_path.open("r", encoding="utf-8") as f:
            promptfoo_json = json.load(f)

    results_map = _extract_result_map(promptfoo_json)
    row_results = _apply_results(all_rows, results_map)

    write_csv(resolved.out_path, [rr.row for rr in row_results])
    logger.info("eval.csv.written", path=str(resolved.out_path), rows=len(row_results))

    baseline_rows: list[EvalRow] | None = None
    if cfg.report.baseline:
        baseline_path = Path(cfg.report.baseline)
        if not baseline_path.is_file():
            raise RunError(f"baseline CSV not found: {baseline_path}")
        baseline_rows = read_csv(baseline_path).rows

    summary = render_report(
        rows_with_meta=[(rr.row, rr.metadata) for rr in row_results],
        baseline_rows=baseline_rows,
        cfg=cfg,
        path=resolved.report_path,
    )
    logger.info("eval.report.written", path=str(resolved.report_path), **summary)

    elapsed = time.monotonic() - started
    return {
        "rows": len(row_results),
        "elapsed_sec": round(elapsed, 2),
        "out": str(resolved.out_path),
        "report": str(resolved.report_path),
        **summary,
    }


__all__ = ["ResolvedRun", "RunError", "run_eval"]
