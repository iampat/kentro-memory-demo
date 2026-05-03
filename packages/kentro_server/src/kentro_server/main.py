"""kentro-server CLI + FastAPI app entrypoint.

Step 0 scaffold: just enough to start the server and respond to /healthz.
Real routes land in Step 7.
"""

import logging

import typer
import uvicorn
from fastapi import FastAPI
from rich.console import Console

from kentro_server import __version__

logger = logging.getLogger(__name__)
console = Console()

app = FastAPI(title="kentro-server", version=__version__)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


cli = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)


@cli.command()
def start(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
    log_level: str = typer.Option("info", help="uvicorn log level"),
) -> None:
    """Start the kentro-server FastAPI app."""
    console.print(f"[bold]kentro-server[/bold] {__version__} starting on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level=log_level)


@cli.command()
def version() -> None:
    """Print the kentro-server version."""
    console.print(__version__)


if __name__ == "__main__":
    cli()
