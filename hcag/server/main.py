"""``hcag-server`` CLI — uvicorn entry point for the FastAPI backend.

Picks between the HCAG agent and the RAG chat agent at startup (§9.5) via
``--agent {hcag|rag}``. Precedence: CLI flag > ``HCAG_SERVER_AGENT`` env > default ``hcag``.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer


app = typer.Typer(add_completion=False, help="HCAG web backend (FastAPI + uvicorn).")


def _resolve_agent_type(flag_value: str | None) -> str:
    if flag_value:
        v = flag_value.strip().lower()
    else:
        v = os.environ.get("HCAG_SERVER_AGENT", "hcag").strip().lower()
    if v not in ("hcag", "rag"):
        raise typer.BadParameter(
            f"invalid agent: {flag_value or v!r}. Expected 'hcag' or 'rag'."
        )
    return v


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8000, "--port", help="Bind port."),
    agent: str = typer.Option(
        None,
        "--agent",
        help="Which agent to serve: `hcag` (default) or `rag`. "
             "Overrides HCAG_SERVER_AGENT env var.",
    ),
    agent_config: Path = typer.Option(
        Path("./agent.toml"),
        "--agent-config",
        "-c",
        help="Path to agent.toml. HCAG-only; ignored when --agent rag.",
    ),
    rag_index: Path = typer.Option(
        Path("./local_lancedb"),
        "--rag-index",
        help="Path to the LanceDB folder produced by `rag`. RAG-only; ignored when --agent hcag.",
    ),
    rag_config: Path = typer.Option(
        Path("./rag_agent.toml"),
        "--rag-config",
        help="Path to rag_agent.toml. RAG-only; ignored when --agent hcag.",
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
    except ImportError as e:  # pragma: no cover
        typer.echo("uvicorn not installed. Install with `pip install hcag[web]`.", err=True)
        raise typer.Exit(code=2) from e

    agent_type = _resolve_agent_type(agent)

    # Stash resolved paths so create_app can reach them after uvicorn imports.
    if agent_type == "hcag" and agent_config.exists():
        os.environ["HCAG_AGENT_CONFIG"] = str(agent_config.resolve())
    os.environ["HCAG_CORS_ORIGINS"] = cors_origins

    from .app import create_app

    origins = [o.strip() for o in cors_origins.split(",") if o.strip()]

    try:
        if agent_type == "hcag":
            factory_app = create_app(
                agent_type="hcag",
                agent_toml=agent_config if agent_config.exists() else None,
                cors_origins=origins,
            )
        else:
            from ..rag.agent import AgentBootstrapError

            try:
                factory_app = create_app(
                    agent_type="rag",
                    rag_index=rag_index,
                    rag_config=rag_config if rag_config.exists() else None,
                    cors_origins=origins,
                )
            except AgentBootstrapError as e:
                typer.echo(f"rag agent startup failed: {e}", err=True)
                raise typer.Exit(code=1) from e
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        typer.echo(f"server startup failed: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"hcag-server: agent={agent_type} host={host} port={port}", err=True)
    uvicorn.run(factory_app, host=host, port=port, reload=reload)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
