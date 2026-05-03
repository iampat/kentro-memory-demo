"""kentro-server CLI + FastAPI app entrypoint."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
import typer
import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from kentro.schema import entity_type_def_from
from kentro.types import (
    EntityVisibilityRule,
    FieldReadRule,
    RuleSet,
    WriteRule,
)
from rich.console import Console

from kentro_server import __version__
from kentro_server.api import (
    demo_router,
    documents_router,
    entities_router,
    memory_router,
    rules_router,
    schema_router,
)
from kentro_server.demo import AuditLog, Customer, Deal, Person, initial_demo_ruleset
from kentro_server.mcp_server import AuthMiddleware, build_mcp
from kentro_server.settings import Settings
from kentro_server.skills.factory import cache_metadata, cache_stats, make_llm_client
from kentro_server.skills.llm_client import LLMClient
from kentro_server.store import TenantRegistry

logger = logging.getLogger(__name__)
console = Console()


class _LazyMcpMount:
    """Lazy mount for the MCP ASGI sub-app.

    Why this exists (and why it isn't a singleton in the harmful sense):

    `FastMCP.session_manager.run()` can only be entered ONCE per FastMCP
    instance (the SDK enforces this with a hard `RuntimeError` on a second
    `.run()`). In production that's fine — the lifespan starts exactly once.
    But the test suite uses `with TestClient(app):` per test, which enters the
    lifespan once per `with` block. With a module-level FastMCP, the second
    test would crash on the second `.run()`.

    `_LazyMcpMount` solves this by mounting at module load time as a thin
    delegator with no inner app. Each lifespan cycle constructs a *fresh*
    FastMCP, wraps it in `AuthMiddleware`, and `attach`es it; on lifespan exit
    we `detach`. The mount itself stays put on the FastAPI router.

    This is a deliberate, narrow exception to CLAUDE.md "No singletons":
    the FastAPI `app` itself is module-level (it has to be — uvicorn imports
    it by path), and `_mcp_mount` is its peer. Per-lifespan attach/detach
    keeps the actual MCP state owned by the lifespan, not the module. The
    mental test from CLAUDE.md ("could I spin up two of these in one test?")
    passes: each TestClient(app) gets its own attached MCP instance.
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


_DEMO_KEY_PATTERNS = (
    "local-ingestion-do-not-share",
    "local-sales-do-not-share",
    "local-cs-do-not-share",
)


def _enforce_demo_key_opt_in(registry: TenantRegistry, allow_demo_keys: bool) -> None:
    """Refuse to boot when committed demo keys are present unless `allow_demo_keys` is True.

    Inverted from the original `kentro_prod_mode` design (codex 2026-05-03 critical):
    the failure mode used to be "operator must remember to flip on prod-mode";
    now it's "operator must explicitly opt INTO using demo keys". The default
    (`allow_demo_keys=False`) makes the safe path the no-config path.

    For local development, `task dev` sets `KENTRO_ALLOW_DEMO_KEYS=true`. For
    any deployment touching a non-loopback bind, rotate the keys instead of
    flipping the opt-in.
    """
    leaked: list[str] = []
    for tcfg in registry.config.tenants:
        for acfg in tcfg.agents:
            if acfg.api_key in _DEMO_KEY_PATTERNS:
                leaked.append(f"{tcfg.id}:{acfg.id}")
    if not leaked:
        return
    msg = (
        f"tenants.json contains publicly-documented demo keys for: {', '.join(leaked)}. "
        "Rotate these keys before serving this process on a non-loopback interface, "
        "OR set KENTRO_ALLOW_DEMO_KEYS=true to acknowledge and proceed (intended for "
        "local development only — `task dev` sets this for you)."
    )
    if not allow_demo_keys:
        raise RuntimeError(f"refusing to boot with demo keys: {msg}")
    logger.warning("DEMO-KEY OPT-IN ACTIVE: %s", msg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App startup. Wires settings → LLM client → tenant registry → MCP mount.

    Failure handling: any step may raise (bad model name, missing API key,
    malformed tenants.json). The `finally` block uses `getattr(..., None)`
    rather than direct attribute access so a partial setup doesn't mask the
    real exception with a follow-on `AttributeError`. dispose_all() runs only
    on objects that were actually constructed.
    """
    app.state.settings = None
    app.state.llm_client = None
    app.state.tenant_registry = None
    mcp = None
    try:
        settings = Settings()
        app.state.settings = settings
        app.state.llm_client = make_llm_client(settings)
        registry = TenantRegistry.from_paths(
            state_dir=settings.kentro_state_dir,
            config_path=settings.kentro_tenants_json,
        )
        app.state.tenant_registry = registry
        _enforce_demo_key_opt_in(registry, allow_demo_keys=settings.kentro_allow_demo_keys)
        # Fresh FastMCP per lifespan cycle (see _LazyMcpMount docstring).
        mcp = build_mcp()
        _mcp_mount.attach(AuthMiddleware(mcp.streamable_http_app()))
        # The MCP streamable HTTP transport requires its session manager running
        # for the full lifetime of any HTTP requests it serves.
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                _mcp_mount.detach()
    finally:
        registry = getattr(app.state, "tenant_registry", None)
        if registry is not None:
            registry.dispose_all()


app = FastAPI(title="kentro-server", version=__version__, lifespan=lifespan)

app.include_router(documents_router)
app.include_router(entities_router)
app.include_router(rules_router)
app.include_router(schema_router)
app.include_router(memory_router)
app.include_router(demo_router)

# Mount the lazy delegator; lifespan attaches the real MCP sub-app each cycle.
app.mount("/mcp", _mcp_mount)


# `/mcp` (no trailing slash) needs an explicit redirect: Starlette's Mount only
# matches `/mcp/*` (with the slash), and the catch-all StaticFiles mount we
# install at the end would otherwise 404 the bare `/mcp` request before the
# inner app could redirect. Without this, `claude mcp add ... /mcp` fails.
@app.get("/mcp", include_in_schema=False)
def _mcp_redirect() -> RedirectResponse:
    return RedirectResponse(url="/mcp/", status_code=307)


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


# Serve the demo UI from `/`. Mounted LAST so that every explicit @app.get / Mount
# above wins first (Starlette walks routes in registration order). `html=True`
# makes `/` serve `index.html`. Mount path passed as `/` becomes the catch-all;
# any request that didn't match an explicit route lands here, which is exactly
# the behavior we want for an SPA-style static bundle.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="ui")
else:
    logger.warning(
        "static UI directory missing at %s — `/` will 404. Run `git pull` or "
        "reinstall the package to get the prototype bundle.",
        _STATIC_DIR,
    )


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
        help="Register schema + apply rules; don't ingest the corpus markdown files.",
    ),
    skip_rules: bool = typer.Option(
        False,
        "--skip-rules",
        help="Don't apply the initial demo ruleset (Sales/CS/AuditLog ACLs + latest-write).",
    ),
) -> None:
    """Register demo schemas, apply initial demo ACLs, then ingest the corpus.

    One-line "make a fresh tenant ready to demo" command. Three phases, each
    idempotent so re-running is safe:
      1. POST /schema/register with the four demo entity types.
      2. POST /rules/apply with `initial_demo_ruleset()` (Sales/CS/AuditLog
         access boundaries + latest-write conflict resolver). Without this,
         the right pane in the UI is empty and the ingestion_agent's
         /documents writes are silently dropped by default-deny ACL.
      3. POST /documents for every markdown file in `examples/synthetic_corpus/`.
    """
    # main.py is at packages/kentro_server/src/kentro_server/main.py — parents[4] is the repo root.
    repo_root = Path(__file__).resolve().parents[4]
    corpus_dir = repo_root / "examples" / "synthetic_corpus"
    if not corpus_dir.is_dir():
        console.print(f"[red]corpus dir not found:[/red] {corpus_dir}")
        raise typer.Exit(code=1)

    headers = {"Authorization": f"Bearer {api_key}"}
    base = base_url.rstrip("/")

    type_defs = [
        entity_type_def_from(Customer),
        entity_type_def_from(Person),
        entity_type_def_from(Deal),
        entity_type_def_from(AuditLog),
    ]
    body = {"type_defs": [td.model_dump(mode="json") for td in type_defs]}
    r = httpx.post(f"{base}/schema/register", headers=headers, json=body, timeout=10.0)
    if r.status_code != 200:
        console.print(f"[red]/schema/register failed[/red] {r.status_code}: {r.text}")
        raise typer.Exit(code=1)
    registered = [td["name"] for td in r.json()["type_defs"]]
    console.print(f"[green]registered schemas[/green]: {registered}")

    if not skip_rules:
        ruleset = initial_demo_ruleset()
        body = {"ruleset": ruleset.model_dump(mode="json")}
        r = httpx.post(f"{base}/rules/apply", headers=headers, json=body, timeout=10.0)
        if r.status_code != 200:
            console.print(f"[red]/rules/apply failed[/red] {r.status_code}: {r.text}")
            raise typer.Exit(code=1)
        version = r.json().get("version", "?")
        console.print(
            f"[green]applied demo ruleset[/green] (version {version}, "
            f"{len(ruleset.rules)} rules — sales/cs/auditlog access + latest-write)"
        )

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
        help="Bearer key for an ADMIN agent (smoke-test applies a permissive ruleset).",
    ),
) -> None:
    """End-to-end HTTP smoke: register schema, grant ACL, write a Customer, read it back.

    Doesn't ingest a document or invoke the LLM — that's the long-running smoke.
    This is the fast 'is the wiring alive' check: schema register → rules apply →
    write → read. Requires an admin key because it calls /rules/apply and
    /schema/register; both are admin-gated since PR #12.
    """
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

    # ACL is default-deny — apply a minimal permissive ruleset so the write/read
    # below succeed. The agent_id we grant must match the agent the api_key
    # resolves to; we don't know it directly, so we extract it from the response
    # to a probe write (which will return PERMISSION_DENIED with a reason that
    # mentions the agent). Simpler: ask /rules/active first, which echoes the
    # tenant_id but not the agent_id — neither helps. Cleanest: call /llm/stats
    # which is NOT auth-gated (just informational).
    #
    # Pragmatic v0: assume the admin key belongs to "ingestion_agent" (the
    # default tenants.json ships that as the only admin). If a deployment has a
    # different admin agent name, the user can edit this CLI or apply rules out
    # of band.
    agent_id = "ingestion_agent"
    permissive = RuleSet(
        rules=(
            EntityVisibilityRule(agent_id=agent_id, entity_type="Customer", allowed=True),
            WriteRule(agent_id=agent_id, entity_type="Customer", allowed=True),
            FieldReadRule(
                agent_id=agent_id, entity_type="Customer", field_name="name", allowed=True
            ),
        ),
        version=0,
    )
    r = httpx.post(
        f"{base}/rules/apply",
        headers=headers,
        json={
            "ruleset": permissive.model_dump(mode="json"),
            "summary": "smoke-test: minimal grant for Customer.name",
        },
        timeout=10.0,
    )
    if r.status_code != 200:
        console.print(f"[red]/rules/apply failed[/red] {r.status_code}: {r.text}")
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
    console.print("[green]smoke-test passed[/green]: schema → rules → write → read round-trips.")


if __name__ == "__main__":
    cli()
