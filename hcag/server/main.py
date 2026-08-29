"""``hcag-server`` CLI — uvicorn entry point for the FastAPI backend."""

from __future__ import annotations

import os
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="HCAG web backend (FastAPI + uvicorn).")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8000, "--port", help="Bind port."),
    agent_config: Path = typer.Option(
        Path("./agent.toml"),
        "--agent-config",
        "-c",
        help="Path to agent.toml. Falls back to HCAG_KB_ROOT env if the file is missing.",
    ),
    reload: bool = typer.Option(False, "--reload", help="Enable uvicorn reload (dev only)."),
    cors_origins: str = typer.Option(
        "*",
        "--cors-origins",
        help="Comma-separated CORS origins. Default: '*'.",
    ),
) -> None:
    """Run the FastAPI server."""
    try:
        import uvicorn
    except ImportError as e:  # pragma: no cover - install guard
        typer.echo("uvicorn not installed. Install with `pip install hcag[web]`.", err=True)
        raise typer.Exit(code=2) from e

    # Stash the resolved agent-config path so `create_app` can find it after
    # uvicorn imports the factory string in a worker.
    if agent_config.exists():
        os.environ["HCAG_AGENT_CONFIG"] = str(agent_config.resolve())
    os.environ["HCAG_CORS_ORIGINS"] = cors_origins

    from .app import create_app

    origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    factory_app = create_app(
        agent_toml=agent_config if agent_config.exists() else None,
        cors_origins=origins,
    )
    uvicorn.run(factory_app, host=host, port=port, reload=reload)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
