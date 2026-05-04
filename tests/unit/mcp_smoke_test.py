"""MCP server smoke tests — auth middleware + tool registration.

Layers covered:
1. **Auth middleware** (`kentro_server.mcp_server.AuthMiddleware`) — drives the
   ASGI app directly with synthetic scopes/messages and asserts unauthenticated
   requests get 401 *before* the inner app is invoked.
2. **Tool registration** — `build_mcp()` returns a `FastMCP` whose `list_tools()`
   includes every kentro_* tool we wired.
3. **Atomic remember** (Codex 2026-05-03 finding #2) — exercises `kentro_remember`
   with a per-field deny rule active and asserts no partial Note state lands.

We do not boot a full MCP client session here — that's an integration concern
and adds a lot of moving parts (session manager, streaming protocol). The tests
above cover the surfaces most likely to break: auth, tool registration, and
the remember-atomicity invariant.
"""

import pytest
from kentro.types import (
    EntityVisibilityRule,
    FieldReadRule,
    WriteRule,
)
from kentro_server.core.rules import apply_ruleset
from kentro_server.core.schema_registry import SchemaRegistry
from kentro_server.mcp_server import (
    AuthMiddleware,
    McpRequestContext,
    _ctx,
    build_mcp,
)
from kentro_server.store import (
    AgentConfig,
    TenantConfig,
    TenantRegistry,
    TenantsConfig,
)

from tests.unit._helpers import FakeLLM

_API_KEY = "mcp-test-key"


@pytest.fixture
def registry(tmp_path):
    config = TenantsConfig(
        tenants=(
            TenantConfig(
                id="local",
                agents=(AgentConfig(id="ingestion_agent", api_key=_API_KEY),),
            ),
        )
    )
    reg = TenantRegistry(tmp_path / "kentro_state", config)
    yield reg
    reg.dispose_all()


# === Tool registration ==============================================================


@pytest.mark.asyncio
async def test_build_mcp_registers_all_kentro_tools() -> None:
    mcp = build_mcp()
    tool_names = {t.name for t in await mcp.list_tools()}
    expected = {
        "kentro_remember",
        "kentro_read",
        "kentro_write",
        "kentro_ingest",
        "kentro_register_schema",
        "kentro_apply_rules",
        "kentro_parse_rules",
        "kentro_get_rules",
        "kentro_list_schema",
    }
    missing = expected - tool_names
    if missing:
        raise AssertionError(f"missing tools: {missing}; got {tool_names}")


# === Auth middleware ================================================================


class _RecorderApp:
    """Minimal ASGI app that records whether it was invoked."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})


class _MockState:
    """Minimal app.state surface that AuthMiddleware reads on every request."""

    def __init__(self, registry: TenantRegistry) -> None:
        self.tenant_registry = registry
        self.llm_client = None  # only read after auth succeeds; OK to be None for unauth tests
        self.settings = _MockSettings()


class _MockSettings:
    kentro_llm_smart_model = "claude-sonnet-4-6"


class _MockParentApp:
    def __init__(self, registry: TenantRegistry) -> None:
        self.state = _MockState(registry)


async def _drive(
    middleware: AuthMiddleware,
    *,
    registry: TenantRegistry,
    headers: list[tuple[bytes, bytes]],
) -> tuple[int, bytes]:
    """Drive the middleware once with a synthetic HTTP scope; return (status, body)."""
    captured_status = 0
    captured_body = bytearray()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal captured_status
        if message["type"] == "http.response.start":
            captured_status = message["status"]
        elif message["type"] == "http.response.body":
            captured_body.extend(message.get("body", b""))

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": headers,
        "app": _MockParentApp(registry),
    }
    await middleware(scope, receive, send)
    return captured_status, bytes(captured_body)


@pytest.mark.asyncio
async def test_auth_middleware_rejects_missing_bearer(registry: TenantRegistry) -> None:
    inner = _RecorderApp()
    mw = AuthMiddleware(inner)
    status, body = await _drive(mw, registry=registry, headers=[])
    if status != 401:
        raise AssertionError(f"missing bearer must return 401, got {status} {body!r}")
    if inner.called:
        raise AssertionError("inner app must NOT be invoked when auth fails")


@pytest.mark.asyncio
async def test_auth_middleware_rejects_unknown_key(registry: TenantRegistry) -> None:
    inner = _RecorderApp()
    mw = AuthMiddleware(inner)
    status, body = await _drive(
        mw, registry=registry, headers=[(b"authorization", b"Bearer not-a-real-key")]
    )
    if status != 401:
        raise AssertionError(f"unknown key must return 401, got {status} {body!r}")
    if inner.called:
        raise AssertionError("inner app must NOT be invoked on bad key")


@pytest.mark.asyncio
async def test_auth_middleware_accepts_valid_key(registry: TenantRegistry) -> None:
    inner = _RecorderApp()
    mw = AuthMiddleware(inner)
    status, body = await _drive(
        mw, registry=registry, headers=[(b"authorization", f"Bearer {_API_KEY}".encode())]
    )
    if not inner.called:
        raise AssertionError("valid key should let the request through to the inner app")
    if status != 200:
        raise AssertionError(f"inner returned 200; got {status} {body!r}")


@pytest.mark.asyncio
async def test_auth_middleware_passes_through_lifespan_scope(registry: TenantRegistry) -> None:
    """Lifespan and other non-HTTP scopes should not be auth-checked."""
    inner = _RecorderApp()
    mw = AuthMiddleware(inner)

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        pass

    _ = registry  # not used by the lifespan path; included so the fixture loads
    await mw({"type": "lifespan"}, receive, send)
    if not inner.called:
        raise AssertionError("lifespan scope must reach the inner app unauthenticated")


# === Atomic remember (finding #2) ===================================================


@pytest.mark.asyncio
async def test_kentro_remember_atomic_no_partial_writes_on_field_denial(
    registry: TenantRegistry,
) -> None:
    """Codex 2026-05-03 finding #2: MCP `kentro_remember` must be atomic.

    Mirrors `test_remember_atomic_no_partial_writes_on_field_denial` from
    `api_smoke_test.py`. Setup: grant write on Note, but explicitly deny
    write on Note.object_json. The previous per-field loop persisted
    subject + predicate then PD'd on object_json; the bulk path short-
    circuits up-front, so zero writes land.
    """
    store, agent_id, _ = registry.by_api_key(_API_KEY)
    schema = SchemaRegistry(store)
    schema.list_all()  # auto-seed Note

    apply_ruleset(
        store,
        rules=(
            EntityVisibilityRule(agent_id=agent_id, entity_type="Note", allowed=True),
            WriteRule(agent_id=agent_id, entity_type="Note", allowed=True),
            WriteRule(
                agent_id=agent_id,
                entity_type="Note",
                field_name="object_json",
                allowed=False,
            ),
            FieldReadRule(
                agent_id=agent_id, entity_type="Note", field_name="subject", allowed=True
            ),
            FieldReadRule(
                agent_id=agent_id, entity_type="Note", field_name="predicate", allowed=True
            ),
            FieldReadRule(
                agent_id=agent_id, entity_type="Note", field_name="object_json", allowed=True
            ),
        ),
    )

    mcp = build_mcp()
    fake_llm = FakeLLM()

    # Set the per-request contextvar by hand — this is what AuthMiddleware
    # does for real requests. We bypass the middleware to keep the test
    # focused on tool behavior.
    from kentro_server.api.auth import Principal  # noqa: PLC0415 — test-local

    principal = Principal(tenant_id=store.tenant_id, agent_id=agent_id, store=store, is_admin=True)
    ctx = McpRequestContext(
        principal=principal, llm=fake_llm, registry=registry, smart_model="claude-sonnet-4-6"
    )
    token = _ctx.set(ctx)
    try:
        result = await mcp.call_tool(
            "kentro_remember",
            {
                "subject": "demo-prep",
                "predicate": "scheduled_at",
                "object_value": "2026-05-10T14:00:00Z",
                "source_label": "kentro-cli",
            },
        )
    finally:
        _ctx.reset(token)

    # FastMCP returns either (content_blocks, structured_payload),
    # a structured dict, or a list of TextContent blocks. Normalize to a
    # dict so the assertions are uniform.
    import json  # noqa: PLC0415 — test-local

    from mcp.types import TextContent  # noqa: PLC0415

    raw_payload: object
    if isinstance(result, tuple):
        raw_payload = result[1]
    elif isinstance(result, list) and result:
        first = result[0]
        if not isinstance(first, TextContent):
            raise AssertionError(f"expected TextContent block, got {type(first).__name__}")
        # Each block carries the JSON-encoded tool return value as `.text`.
        raw_payload = json.loads(first.text)
    else:
        raw_payload = result
    if not isinstance(raw_payload, dict):
        raise AssertionError(f"unexpected MCP payload shape: {raw_payload!r}")
    payload: dict[str, object] = {str(k): v for k, v in raw_payload.items()}
    if payload.get("status") != "permission_denied":
        raise AssertionError(
            f"expected permission_denied (object_json deny gate), got {payload!r}"
        )

    # The crucial invariant: NO Note rows or field writes should exist for the
    # subject. A leaky per-field loop would have written subject + predicate.
    from kentro_server.store.models import EntityRow, FieldWriteRow  # noqa: PLC0415
    from sqlmodel import select  # noqa: PLC0415 — test-local

    with store.session() as session:
        entity = session.exec(
            select(EntityRow).where(EntityRow.type == "Note", EntityRow.key == "demo-prep")
        ).first()
        if entity is not None:
            writes = list(
                session.exec(
                    select(FieldWriteRow).where(FieldWriteRow.entity_id == entity.id)
                ).all()
            )
            if writes:
                raise AssertionError(
                    f"expected zero field writes after atomic-deny, got {len(writes)}: "
                    f"{[(w.field_name, w.value_json) for w in writes]!r}"
                )
