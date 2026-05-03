"""kentro-server CLI + FastAPI app entrypoint."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
import typer
import uvicorn
from fastapi import Depends, FastAPI, Request
from rich.console import Console

from kentro_server import __version__
from kentro_server.api import (
    documents_router,
    entities_router,
    memory_router,
    rules_router,
    schema_router,
)
from kentro_server.mcp_server import AuthMiddleware, build_mcp
from kentro_server.settings import Settings
from kentro_server.skills.factory import cache_metadata, cache_stats, make_llm_client
from kentro_server.skills.llm_client import LLMClient
from kentro_server.store import TenantRegistry

logger = logging.getLogger(__name__)
console = Console()


class _LazyMcpMount:
    """Lazy mount for the MCP ASGI sub-app.

    `FastMCP.session_manager` raises if `.run()` is entered twice, so we cannot
    keep a single FastMCP instance across multiple lifespan cycles (which is the
    normal pattern in tests that use `with TestClient(app)` repeatedly). Instead,
    we mount this delegator at module load time and attach a freshly-built MCP
    sub-app every time the lifespan starts.
    """

    def __init__(self) -> None:
        self._inner = None

    def attach(self, inner) -> None:
        self._inner = inner

    def detach(self) -> None:
        self._inner = None

    async def __call__(self, scope, receive, send) -> None:
        if self._inner is None:
            if scope["type"] == "http":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                    }
                )
                await send({"type": "http.response.body", "body": b"mcp not initialized"})
                return
            # lifespan + websocket scopes: drop silently
            return
        await self._inner(scope, receive, send)


_mcp_mount = _LazyMcpMount()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    app.state.settings = settings
    app.state.llm_client = make_llm_client(settings)
    app.state.tenant_registry = TenantRegistry.from_paths(
        state_dir=settings.kentro_state_dir,
        config_path=settings.kentro_tenants_json,
    )
    # Fresh FastMCP per lifespan cycle. AuthMiddleware reads request-time deps
    # from `scope["app"].state`, so it doesn't need to capture the LLM/registry.
    mcp = build_mcp()
    _mcp_mount.attach(AuthMiddleware(mcp.streamable_http_app()))
    # The MCP streamable HTTP transport requires its session manager running for
    # the full lifetime of any HTTP requests it serves.
    async with mcp.session_manager.run():
        try:
            yield
        finally:
            _mcp_mount.detach()
            app.state.tenant_registry.dispose_all()


app = FastAPI(title="kentro-server", version=__version__, lifespan=lifespan)

app.include_router(documents_router)
app.include_router(entities_router)
app.include_router(rules_router)
app.include_router(schema_router)
app.include_router(memory_router)

# Mount the lazy delegator; lifespan attaches the real MCP sub-app each cycle.
app.mount("/mcp", _mcp_mount)


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


@cli.command("seed-demo")
def seed_demo(
    base_url: str = typer.Option("http://127.0.0.1:8000", help="Server base URL."),
    api_key: str = typer.Option(
        ...,
        envvar="KENTRO_API_KEY",
        help="Bearer key (the ingestion_agent's key from tenants.json works for seed).",
    ),
    skip_ingest: bool = typer.Option(
        False,
        "--skip-ingest",
        help="Register schema only; don't ingest the corpus markdown files.",
    ),
) -> None:
    """Register the demo schemas, then ingest every markdown file in `examples/synthetic_corpus/`.

    This is the one-line "make a fresh tenant ready to demo" command. Idempotent
    — register is no-op for unchanged definitions, and re-ingest of the same
    blob produces a new document row but the field writes are corroboration on
    top of the prior ones (no conflicts unless content differs).
    """
    from kentro.schema import entity_type_def_from

    from kentro_server.demo import Customer, Person

    # main.py is at packages/kentro_server/src/kentro_server/main.py — parents[4] is the repo root.
    repo_root = Path(__file__).resolve().parents[4]
    corpus_dir = repo_root / "examples" / "synthetic_corpus"
    if not corpus_dir.is_dir():
        console.print(f"[red]corpus dir not found:[/red] {corpus_dir}")
        raise typer.Exit(code=1)

    headers = {"Authorization": f"Bearer {api_key}"}
    base = base_url.rstrip("/")

    type_defs = [entity_type_def_from(Customer), entity_type_def_from(Person)]
    body = {"type_defs": [td.model_dump(mode="json") for td in type_defs]}
    r = httpx.post(f"{base}/schema/register", headers=headers, json=body, timeout=10.0)
    if r.status_code != 200:
        console.print(f"[red]/schema/register failed[/red] {r.status_code}: {r.text}")
        raise typer.Exit(code=1)
    registered = [td["name"] for td in r.json()["type_defs"]]
    console.print(f"[green]registered schemas[/green]: {registered}")

    if skip_ingest:
        console.print("--skip-ingest set; not ingesting corpus.")
        return

    docs = sorted(corpus_dir.glob("*.md"))
    if not docs:
        console.print(f"[yellow]no .md files in {corpus_dir}[/yellow]")
        return
    for path in docs:
        body = {"content": path.read_text(encoding="utf-8"), "label": path.name}
        r = httpx.post(f"{base}/documents", headers=headers, json=body, timeout=120.0)
        if r.status_code != 200:
            console.print(f"[red]ingest {path.name} failed[/red] {r.status_code}: {r.text}")
            raise typer.Exit(code=1)
        out = r.json()
        n = len(out.get("entities", []))
        console.print(f"  ingested [bold]{path.name}[/bold] → {n} entities")
    console.print(f"[green]done[/green]: {len(docs)} documents ingested")


@cli.command("reset-tenant")
def reset_tenant(
    tenant_id: str = typer.Argument(..., help="Tenant id to wipe (must exist in tenants.json)."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt."),
) -> None:
    """Wipe a tenant's on-disk state (SQLite + blobs + Witchcraft index).

    Operates on local files — does NOT touch a running server's in-memory state.
    Best run while the server is stopped. The CLI rebuilds the empty tenant
    directory after wiping so the next server start finds it ready.
    """
    settings = Settings()
    if not yes:
        confirmed = typer.confirm(
            f"WIPE all state for tenant {tenant_id!r} under {settings.kentro_state_dir}?"
        )
        if not confirmed:
            console.print("aborted.")
            raise typer.Exit(code=1)
    registry = TenantRegistry.from_paths(
        state_dir=settings.kentro_state_dir,
        config_path=settings.kentro_tenants_json,
    )
    if tenant_id not in registry.known_tenants():
        console.print(
            f"[red]unknown tenant[/red] {tenant_id!r}; known: {registry.known_tenants()}"
        )
        registry.dispose_all()
        raise typer.Exit(code=1)
    registry.reset(tenant_id)
    registry.dispose_all()
    console.print(f"[green]reset tenant[/green] {tenant_id}")


@cli.command("smoke-test")
def smoke_test(
    base_url: str = typer.Option("http://127.0.0.1:8000", help="Server base URL."),
    api_key: str = typer.Option(
        ...,
        envvar="KENTRO_API_KEY",
        help="Bearer key for any agent that has read+write on the demo schema.",
    ),
) -> None:
    """End-to-end HTTP smoke: write a Customer, read it back, see the value.

    Doesn't ingest a document or invoke the LLM — that's the long-running smoke.
    This is the fast 'is the wiring alive' check: schema register → write → read.
    """
    from kentro.schema import entity_type_def_from

    from kentro_server.demo import Customer

    headers = {"Authorization": f"Bearer {api_key}"}
    base = base_url.rstrip("/")

    td = entity_type_def_from(Customer)
    r = httpx.post(
        f"{base}/schema/register",
        headers=headers,
        json={"type_defs": [td.model_dump(mode="json")]},
        timeout=10.0,
    )
    if r.status_code != 200:
        console.print(f"[red]/schema/register failed[/red] {r.status_code}: {r.text}")
        raise typer.Exit(code=1)

    r = httpx.post(
        f"{base}/entities/Customer/SmokeCo/name",
        headers=headers,
        json={"value_json": '"SmokeCo"'},
        timeout=10.0,
    )
    if r.status_code != 200:
        console.print(f"[red]write failed[/red] {r.status_code}: {r.text}")
        raise typer.Exit(code=1)

    r = httpx.get(f"{base}/entities/Customer/SmokeCo", headers=headers, timeout=10.0)
    if r.status_code != 200:
        console.print(f"[red]read failed[/red] {r.status_code}: {r.text}")
        raise typer.Exit(code=1)
    record = r.json()
    name_field = record["fields"]["name"]
    if name_field.get("status") != "known" or name_field.get("value") != "SmokeCo":
        console.print(f"[red]read-back mismatch[/red]: {name_field!r}")
        raise typer.Exit(code=1)
    console.print("[green]smoke-test passed[/green]: schema → write → read round-trips.")


if __name__ == "__main__":
    cli()
