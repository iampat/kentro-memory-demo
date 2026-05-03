"""kentro-server CLI + FastAPI app entrypoint."""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
import typer
import uvicorn
from fastapi import Depends, FastAPI, Request
from rich.console import Console

from kentro_server import __version__
from kentro_server.settings import Settings
from kentro_server.skills.factory import cache_metadata, cache_stats, make_llm_client
from kentro_server.skills.llm_client import LLMClient
from kentro_server.store import TenantRegistry

logger = logging.getLogger(__name__)
console = Console()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    app.state.settings = settings
    app.state.llm_client = make_llm_client(settings)
    app.state.tenant_registry = TenantRegistry.from_paths(
        state_dir=settings.kentro_state_dir,
        config_path=settings.kentro_tenants_json,
    )
    try:
        yield
    finally:
        app.state.tenant_registry.dispose_all()


app = FastAPI(title="kentro-server", version=__version__, lifespan=lifespan)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_llm_client(request: Request) -> LLMClient:
    return request.app.state.llm_client


def get_tenant_registry(request: Request) -> TenantRegistry:
    return request.app.state.tenant_registry


SettingsDep = Annotated[Settings, Depends(get_settings)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
TenantRegistryDep = Annotated[TenantRegistry, Depends(get_tenant_registry)]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/llm/stats")
def llm_stats(client: LLMClientDep) -> dict:
    """Cache hit/miss counters for the running process.

    Aggregates counters across the (one or two) `CachingProvider`s wired into
    the active `DefaultLLMClient`. Returns `cache_enabled=False` when the
    client has no cache wrapper (e.g. test injections that wire raw Providers).
    """
    meta = cache_metadata(client)
    stats = cache_stats(client)
    if meta is None or stats is None:
        return {"cache_enabled": False, "stats": None}
    return {
        "cache_enabled": meta["enabled"],
        "cache_dir": meta["cache_dir"],
        "hits": stats.hits,
        "inner_calls": stats.inner_calls,
        "total": stats.total,
        "hit_rate": round(stats.hit_rate, 4),
    }


cli = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)


@cli.command()
def start(
    host: str | None = typer.Option(None, help="Bind host (defaults to KENTRO_HOST or 127.0.0.1)"),
    port: int | None = typer.Option(None, help="Bind port (defaults to KENTRO_PORT or 8000)"),
    log_level: str = typer.Option("info", help="uvicorn log level"),
) -> None:
    """Start the kentro-server FastAPI app."""
    settings = Settings()
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
    base_url: str = typer.Option("http://127.0.0.1:8000", help="Server base URL to query."),
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
