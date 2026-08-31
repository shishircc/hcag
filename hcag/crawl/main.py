"""`crawl` CLI entry point (§4.2).

    $ crawl --depth <N> <seed_url> [<seed_url> ...]

Writes a mirrored Markdown tree under `./kb/` and a JSON-lines log to
`./crawl.log` (paths overridable with `--output` and `--log-file`).
Exits non-zero if any ERROR-level event was recorded during the run.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..config import LogConfig
from ..logger import build_logger
from .core import crawl


def _cli(
    seeds: list[str] = typer.Argument(
        ...,
        metavar="SEED_URL...",
        help="One or more starting URLs. Each seed also defines a prefix scope (§4.3.1).",
    ),
    depth: int = typer.Option(
        2, "--depth", "-d",
        min=0,
        help="Maximum link-following depth from any seed (§4.3.3). 0 fetches only the seeds.",
    ),
    output: Path = typer.Option(
        Path("./kb"), "--output", "-o",
        help="Output root; the domain becomes the first folder under it (§4.5).",
    ),
    log_file: Path = typer.Option(
        Path("./crawl.log"), "--log-file",
        help="Log file path (JSON-lines, §4.7).",
    ),
    log_level: str = typer.Option(
        "INFO", "--log-level",
        help="Log level: DEBUG | INFO | WARN | ERROR.",
    ),
    boilerplate_threshold: float = typer.Option(
        0.7, "--boilerplate-threshold",
        min=0.0, max=1.0,
        help=(
            "Fraction of pages a block must appear in (at the top or bottom) to be "
            "stripped as site chrome (§4.4.4). 0.0 disables detection (same as "
            "--no-boilerplate); 1.0 strips only blocks present on every page."
        ),
    ),
    no_boilerplate: bool = typer.Option(
        False, "--no-boilerplate",
        help="Disable boilerplate detection entirely — write every fetched page verbatim.",
    ),
    min_image_bytes: int = typer.Option(
        10240, "--min-image-bytes",
        min=0,
        help=(
            "Skip images whose fetched byte size is below this threshold and remove "
            "their Markdown references (§4.4.3). Default 10240 (10 KB). Set to 0 to "
            "keep every image regardless of size."
        ),
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Also stream debug logs to stderr (same JSON-lines shape as the log file).",
    ),
) -> None:
    """Crawl seed URLs and mirror them into a local Markdown knowledge base."""
    level = log_level.upper()
    if level not in {"DEBUG", "INFO", "WARN", "ERROR"}:
        typer.echo(f"Invalid --log-level: {log_level}", err=True)
        raise typer.Exit(code=2)

    log_cfg = LogConfig(file_path=str(log_file), level=level)  # type: ignore[arg-type]
    logger = build_logger(log_cfg, name="crawl", console=verbose)

    stats = crawl(
        seeds=seeds,
        depth=depth,
        kb_root=output,
        logger=logger,
        boilerplate_threshold=boilerplate_threshold,
        no_boilerplate=no_boilerplate,
        min_image_bytes=min_image_bytes,
    )

    typer.echo(
        f"crawl complete: {stats.pages_written} page(s), "
        f"{stats.images_extracted} image(s), "
        f"{stats.warnings} warning(s), {stats.errors} error(s). "
        f"Log: {log_file}"
    )
    if stats.errors > 0:
        raise typer.Exit(code=1)


def main() -> None:
    typer.run(_cli)


if __name__ == "__main__":
    main()
