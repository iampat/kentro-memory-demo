"""kentro-server CLI + FastAPI app entrypoint."""

import logging

import httpx
import typer
import uvicorn
from fastapi import FastAPI, HTTPException
from rich.console import Console

from kentro_server import __version__
from kentro_server.settings import get_settings
from kentro_server.skills.cache import CachingLLMClient
from kentro_server.skills.factory import get_llm_client
from kentro_server.skills.llm_client import LLMConfigError

logger = logging.getLogger(__name__)
console = Console()

app = FastAPI(title="kentro-server", version=__version__)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/llm/stats")
def llm_stats() -> dict:
    """Cache hit/miss counters for the running process."""
    try:
        client = get_llm_client()
    except LLMConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not isinstance(client, CachingLLMClient):
        return {"cache_enabled": False, "stats": None}
    s = client.stats
    return {
        "cache_enabled": client.enabled,
        "cache_dir": str(client.cache_dir),
        "hits": s.hits,
        "inner_calls": s.inner_calls,
        "total": s.total,
        "hit_rate": round(s.hit_rate, 4),
    }


cli = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)


@cli.command()
def start(
    host: str | None = typer.Option(None, help="Bind host (defaults to KENTRO_HOST or 127.0.0.1)"),
    port: int | None = typer.Option(None, help="Bind port (defaults to KENTRO_PORT or 8000)"),
    log_level: str = typer.Option("info", help="uvicorn log level"),
) -> None:
    """Start the kentro-server FastAPI app."""
    settings = get_settings()
    bind_host = host or settings.kentro_host
    bind_port = port or settings.kentro_port
    console.print(
        f"[bold]kentro-server[/bold] {__version__} starting on http://{bind_host}:{bind_port}"
    )
    uvicorn.run(app, host=bind_host, port=bind_port, log_level=log_level)


@cli.command()
def version() -> None:
    """Print the kentro-server version."""
    console.print(__version__)


@cli.command("llm-stats")
def llm_stats_cli(
    base_url: str = typer.Option(
        "http://127.0.0.1:8000", help="Server base URL to query."
    ),
) -> None:
    """Query a running kentro-server for its LLM cache hit/miss counters."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/llm/stats", timeout=5.0)
    except httpx.HTTPError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=1) from exc
    if r.status_code != 200:
        console.print(f"[red]server returned {r.status_code}[/red]: {r.text}")
        raise typer.Exit(code=1)
    payload = r.json()
    if payload.get("stats") is None and not payload.get("cache_enabled", True):
        console.print("[yellow]cache disabled[/yellow]")
        return
    console.print(
        f"hits={payload['hits']} "
        f"inner_calls={payload['inner_calls']} "
        f"hit_rate={payload['hit_rate']:.1%} "
        f"(cache_enabled={payload['cache_enabled']}, "
        f"dir={payload.get('cache_dir', '?')})"
    )


if __name__ == "__main__":
    cli()
