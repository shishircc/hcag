"""`evalgen` CLI entry point (§6.3).

    $ evalgen <kb_root> --out <output.csv> \
        [--total <N> | --simple <n1> --medium <n2> --complex <n3> --hard-1 <n4> --hard-2 <n5>] \
        [--personas <personas.csv>] [--persona <id>]... \
        [--seed <int>] [--id-prefix <str>] [--config <path>]

Exits non-zero only on ERROR-level events (empty KB, unwritable output,
missing prompt template, or mutually-exclusive flags at startup). Shortfalls
where the KB cannot support the requested count for a kind are WARN, not ERROR.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..cli.metadata_llm import LLMUnavailableError
from ..config import load_evalgen_config
from .generators import preflight
from ..logger import build_logger
from .personas import Persona, PersonaError, load_personas, select
from .runner import EvalGenRequest, KIND_ORDER, run_evalgen, split_total


def _cli(
    kb_root: Path = typer.Argument(..., help="Normalized KB directory (post `hcag <root>`)."),
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
    personas_file: Path | None = typer.Option(
        None, "--personas",
        help="Persona CSV (persona_id, persona_name, persona_description). "
             "Questions are asked as one of its personas would ask them.",
    ),
    persona_ids: list[str] = typer.Option(
        [], "--persona",
        help="Restrict generation to this persona_id. Repeatable. Requires --personas.",
    ),
    config: Path | None = typer.Option(
        None, "--config",
        help="Path to evalgen.toml. Defaults to <kb_root>/evalgen.toml if present.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Also stream debug logs to stderr (same JSON-lines shape as the log file).",
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

    logger = build_logger(cfg.log, name="evalgen", console=verbose)

    if not cfg_path.exists():
        # Question quality tracks model strength, and `hard-2` needs a
        # multimodal model — silently running the small default is the
        # difference between an eval set and a waste of tokens (§6.2.2).
        logger.warn(
            "evalgen.config.missing",
            path=str(cfg_path),
            provider=cfg.llm.provider,
            model=cfg.llm.litellm_model(),
            detail="using built-in defaults; create evalgen.toml to choose the model",
        )
        typer.echo(
            f"No {cfg_path} — using defaults: {cfg.llm.provider} / "
            f"{cfg.llm.litellm_model()}. Question quality tracks model strength, "
            "and hard-2 needs a multimodal model.",
            err=True,
        )

    personas: list[Persona] = []
    roster_path = personas_file or (Path(cfg.personas.file) if cfg.personas.file else None)
    if persona_ids and roster_path is None:
        typer.echo("--persona requires --personas (or personas.file in evalgen.toml).", err=True)
        raise typer.Exit(code=2)
    if roster_path is not None:
        try:
            personas = select(load_personas(roster_path), list(persona_ids))
        except PersonaError as e:
            # Fatal, never a warning: the roster shapes every row, so there is
            # no degraded mode worth continuing into (§6.2.3).
            logger.error("evalgen.personas.failed", path=str(roster_path), error=str(e))
            typer.echo(f"evalgen aborted, nothing written: {e}", err=True)
            raise typer.Exit(code=1) from e
        logger.info(
            "evalgen.personas.loaded",
            path=str(roster_path),
            personas=[p.id for p in personas],
            allocation=cfg.personas.allocation,
        )
        # Echoed before the first LLM call: a roster short by a row still
        # writes a full CSV, and nothing in the output says so (§6.2.2).
        typer.echo(
            f"Personas from {roster_path} ({cfg.personas.allocation}): "
            + ", ".join(p.id for p in personas),
            err=True,
        )

    if cfg.llm.preflight:
        try:
            preflight(cfg.llm, logger)
        except LLMUnavailableError as e:
            logger.error("evalgen.preflight.failed", error=str(e))
            typer.echo(f"evalgen aborted, nothing written: {e}", err=True)
            raise typer.Exit(code=1) from e

    request = EvalGenRequest(
        kb_root=kb_root,
        out=out,
        counts=counts,
        seed=seed,
        id_prefix=id_prefix,
        personas=personas,
    )
    stats = run_evalgen(request, cfg, logger)

    per_persona = (
        f" Generated per persona: {stats.generated_by_persona}."
        if stats.generated_by_persona else ""
    )
    typer.echo(
        f"evalgen complete: {stats.total_written} row(s) written to {out}. "
        f"Generated per kind: {stats.generated}.{per_persona} "
        f"Warnings: {stats.warnings}. Errors: {stats.errors}."
    )
    if stats.errors > 0:
        raise typer.Exit(code=1)


def main() -> None:
    typer.run(_cli)


if __name__ == "__main__":
    main()
