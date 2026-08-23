"""`hcag` CLI entry point — Typer app with two subcommands (§3.3)."""

from __future__ import annotations

from pathlib import Path

import typer

from ..config import load_cli_config
from ..logger import build_logger
from .aggregate import aggregate_tree
from .preprocess import preprocess_tree


app = typer.Typer(no_args_is_help=True, add_completion=False, help="HCAG knowledge base build tool.")


def _load(root: Path):
    if not root.is_dir():
        typer.echo(f"KB root not found or not a directory: {root}", err=True)
        raise typer.Exit(code=2)
    cfg_path = root / "hcag.toml"
    cfg = load_cli_config(cfg_path)
    logger = build_logger(cfg.log, name="hcag.cli")
    return cfg, logger


@app.command()
def preprocess(
    root: Path = typer.Argument(..., help="KB root directory."),
    force: bool = typer.Option(False, "--force", help="Regenerate everything, overwriting HCAG-marked files."),
) -> None:
    """Bottom-up: assemble packet.md at leaves/mixed and catalog.md at nodes/mixed."""
    cfg, logger = _load(root)
    preprocess_tree(root, cfg, logger, force=force)
    typer.echo(f"Preprocess complete for {root}")


@app.command()
def aggregate(
    root: Path = typer.Argument(..., help="KB root directory."),
) -> None:
    """Top-down: combine per-level catalog.md files into a root catalog.md."""
    _, logger = _load(root)
    aggregate_tree(root, logger)
    typer.echo(f"Aggregate complete — root catalog at {root / 'catalog.md'}")


if __name__ == "__main__":
    app()
