"""`hcag` CLI entry point (§3.3).

A single subcommand — ``preprocess`` — walks the KB with DFS post-order and
emits one ``compiled.md`` per folder (leaf, taxonomy node, mixed, and root).
Aggregation is folded into the same pass; there is no separate
``hcag aggregate`` command anymore.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..config import load_cli_config
from ..logger import build_logger
from .preprocess import preprocess_tree


app = typer.Typer(no_args_is_help=True, add_completion=False, help="HCAG knowledge base build tool.")


def _load(root: Path, *, verbose: bool = False):
    if not root.is_dir():
        typer.echo(f"KB root not found or not a directory: {root}", err=True)
        raise typer.Exit(code=2)
    cfg_path = root / "hcag.toml"
    cfg = load_cli_config(cfg_path)
    logger = build_logger(cfg.log, name="hcag.cli", console=verbose)
    return cfg, logger


@app.command()
def preprocess(
    root: Path = typer.Argument(..., help="KB root directory."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Regenerate every compiled.md, overwriting HCAG-marked files.",
    ),
    only: Path = typer.Option(
        None,
        "--only",
        help=(
            "Preprocess only this subtree, then re-emit its ancestors up to the "
            "root so their ## Sub-topics sections pick up the changed child summary."
        ),
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Also stream debug logs to stderr (in the same JSON-lines shape as the log file).",
    ),
) -> None:
    """DFS build: emit compiled.md at every folder, including the root."""
    cfg, logger = _load(root, verbose=verbose)
    preprocess_tree(root, cfg, logger, force=force, only=only)
    typer.echo(f"Preprocess complete for {root}")


if __name__ == "__main__":
    app()
