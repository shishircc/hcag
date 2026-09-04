"""Orchestrator: read CSV -> promptfoo eval -> completed CSV + HTML report.

Flow (§7.3 + §7.6):

1. Read + validate input CSV.
2. Filter rows by ``--kinds`` and ``--skip-completed`` if requested.
3. Probe the backend once with ``GET /health``, load the prompt registry, and
   preflight both eval LLMs; abort at startup on any failure (§7.3.1).
4. Serialize the resolved ``EvalConfig`` to a temp JSON file the provider
   will read at test time (via ``HCAG_EVAL_CONFIG_JSON``).
5. Emit a ``promptfooconfig.yaml`` in the tempdir; invoke
   ``npx promptfoo eval --config <yaml> --output <json>``, rendering live
   per-row progress from the workers' reports while it runs (§7.11.1).
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

from ..cli.metadata_llm import LLMUnavailableError
from ..logger import HcagLogger
from ..prompting import PromptError, load_prompts
from .backend import BackendClient
from .config import EvalConfig
from .csv_io import EvalRow, VALID_KINDS, read_csv, write_csv
from .llm_calls import preflight
from .progress import PROGRESS_ENV, ProgressReporter
from .promptfoo_config import PROVIDER_MODULE_PATH, write_config
from .report import render_report


@dataclass
class ResolvedRun:
    input_path: Path
    out_path: Path
    report_path: Path
    kinds: set[str] | None = None
    skip_completed: bool = False
    quiet: bool = False


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


@dataclass
class _Completed:
    """What the caller of `_run_promptfoo` needs — the same shape as
    `subprocess.run`'s result, minus everything unused."""

    returncode: int
    stdout: str
    stderr: str


def _run_promptfoo(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    progress: ProgressReporter,
    logger: HcagLogger,
) -> _Completed:
    """Run promptfoo, rendering progress while it works.

    promptfoo's own output is captured to files rather than pipes. Pipes would
    have to be drained concurrently or the child blocks once a buffer fills —
    a hang that would look exactly like the slow eval this progress display
    exists to distinguish from a slow eval. Files cannot deadlock, and the
    output is still there in full for the failure path.
    """
    out_path = cwd / "promptfoo-stdout.txt"
    err_path = cwd / "promptfoo-stderr.txt"

    with out_path.open("wb") as out_f, err_path.open("wb") as err_f:
        proc = subprocess.Popen(argv, env=env, cwd=str(cwd), stdout=out_f, stderr=err_f)
        try:
            while True:
                try:
                    proc.wait(timeout=_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    progress.poll()
                    progress.render()
        except KeyboardInterrupt:
            # Leave nothing running in the background: the operator asked for
            # it to stop, and an orphaned promptfoo keeps calling the backend
            # and spending judge tokens.
            progress.finish()
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            logger.warn("eval.promptfoo.interrupted", rows_done=progress.state.done)
            raise

    progress.finish()
    return _Completed(
        returncode=proc.returncode,
        stdout=_read_text(out_path),
        stderr=_read_text(err_path),
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


#: How often to refresh progress while promptfoo runs. Short enough that the
#: display feels live, long enough that polling costs nothing next to rows
#: that take seconds each.
_POLL_SECONDS = 1.0


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
            row.remark = "[no_result] see evalrun.log for details"
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
            "before running `evalrun`."
        )

    # Prompts are files (§2.15). Load them here, in the parent, so a missing or
    # malformed override is one startup error rather than one identical failure
    # per row inside a promptfoo worker, where it surfaces as N judge failures.
    try:
        load_prompts(cfg.prompts_dir)
    except PromptError as e:
        raise RunError(str(e)) from e

    # §7.3.1 — a run costs the backend before either eval model is called, so a
    # bad key must fail now and not after every row has been paid for.
    if cfg.classifier.llm.preflight or cfg.judge.llm.preflight:
        for role, llm in (("classifier", cfg.classifier.llm), ("judge", cfg.judge.llm)):
            if not llm.preflight:
                continue
            try:
                preflight(llm, role, logger)
            except LLMUnavailableError as e:
                raise RunError(f"LLM preflight failed: {e}") from e

    with tempfile.TemporaryDirectory(prefix="hcag-eval-") as workdir_s:
        workdir = Path(workdir_s)
        cfg_json_path = _write_provider_config(cfg, workdir)
        config_yaml_path = workdir / "promptfooconfig.yaml"
        results_json_path = workdir / "promptfoo-results.json"
        write_config(all_rows, cfg, config_yaml_path)

        progress_path = workdir / "progress.jsonl"
        progress_path.touch()

        env = os.environ.copy()
        env["HCAG_EVAL_CONFIG_JSON"] = str(cfg_json_path)
        env[PROGRESS_ENV] = str(progress_path)
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
        proc = _run_promptfoo(
            argv,
            env=env,
            cwd=workdir,
            progress=ProgressReporter(
                progress_path,
                total=len(all_rows),
                quiet=resolved.quiet,
                on_row=lambda e: logger.info(
                    "eval.row.done",
                    question_id=e.get("question_id"),
                    kind=e.get("kind"),
                    score=e.get("score"),
                    turns=e.get("turns"),
                    elapsed_ms=e.get("elapsed_ms"),
                ),
            ),
            logger=logger,
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
                f"promptfoo eval exited with {proc.returncode}. See evalrun.log for stderr."
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
