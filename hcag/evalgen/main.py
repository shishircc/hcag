"""`evalgen` CLI entry point (§6.3).

    $ evalgen <kb_root> --out <output.csv> \
        [--total <N> | --simple <n1> --medium <n2> --complex <n3> --hard-1 <n4> --hard-2 <n5>] \
        [--seed <int>] [--id-prefix <str>] [--config <path>]

Exits non-zero only on ERROR-level events (empty KB, unwritable output,
missing prompt template, or mutually-exclusive flags at startup). Shortfalls
where the KB cannot support the requested count for a kind are WARN, not ERROR.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..config import load_evalgen_config
from ..logger import build_logger
from .runner import EvalGenRequest, KIND_ORDER, run_evalgen, split_total


def _cli(
    kb_root: Path = typer.Argument(..., help="Normalized KB directory (post `hcag preprocess`)."),
    out: Path = typer.Option(..., "--out", "-o", help="Output CSV path."),
    total: int | None = typer.Option(
        None, "--total", "-n",
        help="Total questions, split equally across the five kinds (mutually exclusive with per-kind flags).",
    ),
    simple: int = typer.Option(0, "--simple", help="Number of `simple` questions."),
    medium: int = typer.Option(0, "--medium", help="Number of `medium` questions."),
    complex_: int = typer.Option(0, "--complex", help="Number of `complex` questions."),
    hard_1: int = typer.Option(0, "--hard-1", help="Number of `hard-1` (cross-packet) questions."),
    hard_2: int = typer.Option(0, "--hard-2", help="Number of `hard-2` (multimodal) questions."),
    seed: int | None = typer.Option(None, "--seed", help="Random seed for reproducibility."),
    id_prefix: str = typer.Option("q", "--id-prefix", help="Prefix for question_id values."),
    config: Path | None = typer.Option(
        None, "--config",
        help="Path to evalgen.toml. Defaults to <kb_root>/evalgen.toml if present.",
    ),
) -> None:
    """Generate evaluation questions/answers from a normalized KB."""
    if not kb_root.is_dir():
        typer.echo(f"KB root not found or not a directory: {kb_root}", err=True)
        raise typer.Exit(code=2)

    # Per-kind flags (non-zero) and --total are mutually exclusive per §6.3.
    per_kind_counts = {
        "simple": simple,
        "medium": medium,
        "complex": complex_,
        "hard-1": hard_1,
        "hard-2": hard_2,
    }
    per_kind_specified = any(v > 0 for v in per_kind_counts.values())
    if total is not None and per_kind_specified:
        typer.echo(
            "Pass either --total OR one-to-five --<kind> flags, not both.",
            err=True,
        )
        raise typer.Exit(code=2)
    if total is None and not per_kind_specified:
        typer.echo(
            "Nothing to generate. Pass --total N or at least one --<kind> flag.",
            err=True,
        )
        raise typer.Exit(code=2)
    if total is not None and total <= 0:
        typer.echo("--total must be positive.", err=True)
        raise typer.Exit(code=2)
    if any(v < 0 for v in per_kind_counts.values()):
        typer.echo("Per-kind counts must be non-negative.", err=True)
        raise typer.Exit(code=2)

    if total is not None:
        counts = split_total(total)
    else:
        counts = {k: per_kind_counts[k] for k in KIND_ORDER}

    cfg_path = config if config is not None else (kb_root / "evalgen.toml")
    cfg = load_evalgen_config(cfg_path)

    logger = build_logger(cfg.log, name="evalgen")

    request = EvalGenRequest(
        kb_root=kb_root,
        out=out,
        counts=counts,
        seed=seed,
        id_prefix=id_prefix,
    )
    stats = run_evalgen(request, cfg, logger)

    typer.echo(
        f"evalgen complete: {stats.total_written} row(s) written to {out}. "
        f"Generated per kind: {stats.generated}. "
        f"Warnings: {stats.warnings}. Errors: {stats.errors}."
    )
    if stats.errors > 0:
        raise typer.Exit(code=1)


def main() -> None:
    typer.run(_cli)


if __name__ == "__main__":
    main()
