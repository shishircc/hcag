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
from .console import Console
from .core import crawl
from .html_conv import DEFAULT_MIN_EXTRACT_CHARS, FAVOR_CHOICES


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
    extract_favor: str = typer.Option(
        "balanced", "--extract-favor",
        help=(
            "Bias of the main-content extractor (§4.4.1): balanced | precision | recall. "
            "precision drops anything it is unsure about; recall keeps borderline blocks."
        ),
    ),
    no_extract: bool = typer.Option(
        False, "--no-extract",
        help=(
            "Disable main-content extraction — convert every page whole-DOM and write "
            "it verbatim, chrome included (§4.4.1 stage 3)."
        ),
    ),
    min_extract_chars: int = typer.Option(
        DEFAULT_MIN_EXTRACT_CHARS, "--min-extract-chars",
        min=0,
        help=(
            "Extractions shorter than this many characters are treated as a failure and "
            "the page falls back to whole-DOM conversion (§4.4.1). 0 accepts any "
            "non-empty extraction."
        ),
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
    asset_hosts: str = typer.Option(
        "",
        "--asset-hosts",
        help=(
            "Comma-separated extra hosts PDFs and images may be fetched from. "
            "By default an asset is fetched only from the same host as the page "
            "that cited it; use this for a CDN or media subdomain."
        ),
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        help="Suppress per-URL progress on stderr. The end-of-run report is still printed.",
    ),
    report_limit: int = typer.Option(
        20,
        "--report-limit",
        help=(
            "Example URLs shown per skip group in the end-of-run report. "
            "0 prints counts only; a negative value prints every URL."
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

    favor = extract_favor.lower()
    if favor not in FAVOR_CHOICES:
        typer.echo(
            f"Invalid --extract-favor: {extract_favor} (choose {' | '.join(FAVOR_CHOICES)})",
            err=True,
        )
        raise typer.Exit(code=2)

    log_cfg = LogConfig(file_path=str(log_file), level=level)  # type: ignore[arg-type]
    logger = build_logger(log_cfg, name="crawl", console=verbose)

    stats = crawl(
        seeds=seeds,
        depth=depth,
        kb_root=output,
        logger=logger,
        no_extract=no_extract,
        extract_favor=favor,
        min_extract_chars=min_extract_chars,
        min_image_bytes=min_image_bytes,
        asset_hosts=tuple(h.strip() for h in asset_hosts.split(",") if h.strip()),
        console=Console(quiet=quiet, report_limit=report_limit),
    )

    typer.echo(
        f"crawl complete: {stats.pages_written} page(s) "
        f"({stats.pages_extracted} extracted, {stats.pages_fallback} fallback), "
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
