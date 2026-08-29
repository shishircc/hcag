"""`eval` CLI entry point (§7.3).

Flags map 1:1 to the parameter table in DESIGN.md §7.3. Everything below
`serve` is a thin translation layer over ``runner.run_eval``.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..logger import build_logger
from .config import EvalConfig, apply_cli_overrides, load_eval_config
from .csv_io import VALID_KINDS
from .runner import ResolvedRun, RunError, run_eval


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Run an evalgen CSV against a live chatbot and score answers (DESIGN §7).",
)


def _parse_kinds(value: str | None) -> set[str] | None:
    if value is None:
        return None
    kinds = {k.strip() for k in value.split(",") if k.strip()}
    if not kinds:
        return None
    unknown = kinds - VALID_KINDS
    if unknown:
        raise typer.BadParameter(
            f"unknown kinds: {sorted(unknown)}. Valid: {sorted(VALID_KINDS)}"
        )
    return kinds


@app.command()
def run(
    input_csv: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Path to the evalgen-produced CSV (§6.7).",
    ),
    backend_url: str = typer.Option(
        None,
        "--backend-url",
        help="Base URL of the chatbot backend. Overrides eval.toml [backend].url.",
    ),
    out: Path = typer.Option(
        ...,
        "--out",
        help="Path to the completed output CSV. Overwritten if it exists.",
    ),
    report: Path = typer.Option(
        ...,
        "--report",
        help="Path to the HTML report file. Overwritten if it exists.",
    ),
    max_turns: int = typer.Option(
        None,
        "--max-turns",
        help="Max chatbot turns per question. Default: 5.",
    ),
    concurrency: int = typer.Option(
        None,
        "--concurrency",
        help="Parallel test cases. Default: 4.",
    ),
    request_timeout: float = typer.Option(
        None,
        "--request-timeout",
        help="Per-/chat HTTP timeout in seconds. Default: 60.",
    ),
    session_scope: str = typer.Option(
        None,
        "--session-scope",
        help="`per-question` (default) or `per-run`.",
    ),
    kinds: str = typer.Option(
        None,
        "--kinds",
        help="Comma-separated subset of question kinds to run.",
    ),
    skip_completed: bool = typer.Option(
        False,
        "--skip-completed",
        help="Skip input rows whose `score` is already populated.",
    ),
    seed: int = typer.Option(
        None,
        "--seed",
        help="Seed for judge sampling / clarifier tie-breaking.",
    ),
    config: Path = typer.Option(
        Path("./eval.toml"),
        "--config",
        "-c",
        help="Path to eval.toml. Falls back to defaults if the file is missing.",
    ),
    baseline: Path = typer.Option(
        None,
        "--baseline",
        help="Prior --out CSV to compare against in the HTML report.",
    ),
    log_file: str = typer.Option(None, "--log-file", help="Log file path."),
    log_level: str = typer.Option(None, "--log-level", help="DEBUG|INFO|WARN|ERROR."),
) -> None:
    """Score an eval set against a chatbot backend."""
    if session_scope is not None and session_scope not in ("per-question", "per-run"):
        raise typer.BadParameter(
            f"invalid --session-scope: {session_scope!r}. Expected `per-question` or `per-run`."
        )

    parsed_kinds = _parse_kinds(kinds)

    cfg = load_eval_config(config) if config.exists() else EvalConfig()
    cfg = apply_cli_overrides(
        cfg,
        backend_url=backend_url,
        max_turns=max_turns,
        concurrency=concurrency,
        request_timeout=request_timeout,
        session_scope=session_scope,  # type: ignore[arg-type]
        seed=seed,
        baseline=str(baseline) if baseline else None,
        log_file=log_file,
        log_level=log_level,
    )

    logger = build_logger(cfg.log, name="hcag.eval")

    resolved = ResolvedRun(
        input_path=input_csv,
        out_path=out,
        report_path=report,
        kinds=parsed_kinds,
        skip_completed=skip_completed,
    )

    try:
        summary = run_eval(cfg, resolved, logger)
    except RunError as e:
        typer.echo(f"eval failed: {e}", err=True)
        logger.error("eval.aborted", reason=str(e))
        raise typer.Exit(code=1) from e
    except Exception as e:  # noqa: BLE001
        typer.echo(f"eval crashed: {type(e).__name__}: {e}", err=True)
        logger.error("eval.crash", error=str(e), kind=type(e).__name__)
        raise typer.Exit(code=2) from e

    typer.echo(json.dumps(summary, indent=2, default=str))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
