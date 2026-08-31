"""`rag` CLI entry point (§8.3).

Flags map 1:1 to the parameter table in DESIGN.md §8.3.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..logger import build_logger
from .config import RagConfig, apply_cli_overrides, load_rag_config
from .runner import RunError, run_rag


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Index a KB folder into LanceDB for hybrid search (DESIGN §8).",
)


@app.command()
def index(
    kb: Path = typer.Option(
        ...,
        "--kb",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Path to the KB folder to index.",
    ),
    index_dir: Path = typer.Option(
        Path("./local_lancedb"),
        "--index",
        help="Path to the LanceDB folder holding the index. Created if missing.",
    ),
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to rag.toml. Defaults to <kb_root>/rag.toml if present.",
    ),
    table: str = typer.Option(
        None,
        "--table",
        help="LanceDB table name. Default: `kb`.",
    ),
    recreate: bool = typer.Option(
        False,
        "--recreate",
        help="Drop the existing table before indexing.",
    ),
    include_images: bool = typer.Option(
        True,
        "--include-images/--no-include-images",
        help="Toggle the image-description pipeline.",
    ),
    log_file: str = typer.Option(None, "--log-file", help="Log file path."),
    log_level: str = typer.Option(None, "--log-level", help="DEBUG|INFO|WARN|ERROR."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Also stream debug logs to stderr (same JSON-lines shape as the log file).",
    ),
) -> None:
    """Index a KB folder into LanceDB."""
    # Resolve config path: explicit --config wins, then <kb>/rag.toml.
    if config is None:
        default = kb / "rag.toml"
        cfg = load_rag_config(default) if default.exists() else RagConfig()
    else:
        cfg = load_rag_config(config) if config.exists() else RagConfig()

    cfg = apply_cli_overrides(
        cfg,
        table=table,
        include_images=include_images,
        log_file=log_file,
        log_level=log_level,
    )

    logger = build_logger(cfg.log, name="hcag.rag", console=verbose)

    try:
        summary = run_rag(kb, index_dir, cfg, logger, recreate=recreate)
    except RunError as e:
        typer.echo(f"rag failed: {e}", err=True)
        logger.error("rag.aborted", reason=str(e))
        raise typer.Exit(code=1) from e
    except Exception as e:  # noqa: BLE001
        typer.echo(f"rag crashed: {type(e).__name__}: {e}", err=True)
        logger.error("rag.crash", error=str(e), kind=type(e).__name__)
        raise typer.Exit(code=2) from e

    typer.echo(json.dumps(summary.__dict__, indent=2, default=str))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
