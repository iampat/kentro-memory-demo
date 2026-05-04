"""MCP server mounted at `/mcp` over Streamable HTTP.

Per the SDK reference (https://py.sdk.modelcontextprotocol.io/server/#mounting-to-an-existing-asgi-server)
we build a `FastMCP` instance, take its `streamable_http_app()`, wrap it in
`AuthMiddleware` (Bearer-token resolved against `TenantRegistry`), and `app.mount(...)`
the result on the parent FastAPI app. The MCP session manager runs inside the
parent app's lifespan via `async with mcp.session_manager.run(): ...`.

Dependencies (LLM client, tenant registry, smart model name) are read from
`scope["app"].state` at request time — never captured at module import time.
This keeps the module singleton-free per CLAUDE.md and lets tests override
state on a per-app-instance basis.

Tools mirror the most-used HTTP routes:

| Tool                | Mirrors                            |
|---------------------|------------------------------------|
| `kentro_remember`   | POST /memory/remember              |
| `kentro_read`       | GET  /entities/{type}/{key}        |
| `kentro_write`      | POST /entities/{type}/{key}/{f}    |
| `kentro_ingest`     | POST /documents                    |
| `kentro_register_schema` | POST /schema/register         |
| `kentro_apply_rules` | POST /rules/apply                 |
| `kentro_parse_rules` | POST /rules/parse                 |
| `kentro_get_rules`  | GET  /rules/active                 |
| `kentro_list_schema`| GET  /schema                       |

Tools return JSON-serializable dicts (Pydantic `model_dump(mode="json")`)
rather than Pydantic models — the MCP wire protocol prefers explicit JSON,
and pinning the shape keeps it identical to the HTTP responses.
"""

import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from kentro.acl import evaluate_write
from kentro.types import AutoResolverSpec, EntityTypeDef, RuleSet, WriteStatus
from mcp.server.fastmcp import FastMCP
from pydantic import TypeAdapter
from starlette.types import ASGIApp, Receive, Scope, Send

from kentro_server.api.auth import Principal
from kentro_server.core.read import read_entity
from kentro_server.core.rules import apply_ruleset, load_active_ruleset
from kentro_server.core.schema_registry import SchemaRegistry
from kentro_server.core.write import write_field, write_fields_bulk
from kentro_server.extraction import ingest_document
from kentro_server.skills.llm_client import LLMClient
from kentro_server.skills.nl_to_ruleset import parse_nl_to_ruleset
from kentro_server.store import TenantRegistry

logger = logging.getLogger(__name__)


# === Per-request context ============================================================
#
# An MCP tool function knows its arguments but has no clean handle on FastAPI
# state. We thread the per-request dependencies through a contextvar set by
# `AuthMiddleware` and read by `_current_ctx()` inside each tool.


@dataclass(frozen=True)
class McpRequestContext:
    principal: Principal
    llm: LLMClient
    registry: TenantRegistry
    smart_model: str


_ctx: ContextVar[McpRequestContext | None] = ContextVar("kentro_mcp_ctx", default=None)


def _current_ctx() -> McpRequestContext:
    ctx = _ctx.get()
    if ctx is None:
        raise RuntimeError(
            "kentro MCP tool called without an authenticated request context. "
            "AuthMiddleware did not run — check that the MCP sub-app is mounted "
            "behind it."
        )
    return ctx


class McpAdminRequiredError(RuntimeError):
    """Raised by an MCP tool when called by a non-admin agent. Surfaces to the
    caller as an MCP tool error rather than an HTTP 403 (MCP transport doesn't
    speak HTTP error codes for tool calls)."""


def _require_admin(ctx: McpRequestContext) -> None:
    if not ctx.principal.is_admin:
        raise McpAdminRequiredError(
            f"agent {ctx.principal.agent_id!r} is not admin; this tool requires the admin role"
        )


# === ASGI auth middleware ===========================================================


class AuthMiddleware:
    """Wraps the FastMCP ASGI sub-app with Bearer-token auth.

    On every HTTP request:
    1. Read `Authorization: Bearer <api-key>`. Reject 401 if missing/malformed.
    2. Resolve the key via `TenantRegistry.by_api_key`. Reject 401 on miss.
    3. Build an `McpRequestContext` from the resolved principal + the runtime
       deps fetched from `scope["app"].state`, set it in `_ctx`, and call the
       inner app. The contextvar is restored on exit (success or exception).

    Lifespan and other non-HTTP scopes flow through unauthenticated — the inner
    FastMCP app needs them for its own session manager.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token:
            await _send_unauthorized(
                send, b"missing or malformed Authorization header (expected 'Bearer <api-key>')"
            )
            return

        parent_app = scope.get("app")
        if parent_app is None:
            await _send_unauthorized(send, b"server misconfigured: no parent app on scope")
            return
        state = parent_app.state
        registry: TenantRegistry = state.tenant_registry
        llm: LLMClient = state.llm_client
        smart_model: str = state.settings.kentro_llm_smart_model

        try:
            store, agent_id, is_admin = registry.by_api_key(token)
        except KeyError:
            logger.info("mcp auth: unknown bearer key (length=%d)", len(token))
            await _send_unauthorized(send, b"invalid api key")
            return

        principal = Principal(
            tenant_id=store.tenant_id, agent_id=agent_id, store=store, is_admin=is_admin
        )
        ctx = McpRequestContext(
            principal=principal,
            llm=llm,
            registry=registry,
            smart_model=smart_model,
        )
        token_ref = _ctx.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            _ctx.reset(token_ref)


async def _send_unauthorized(send: Send, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"www-authenticate", b'Bearer realm="kentro"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# === FastMCP construction ===========================================================


def build_mcp() -> FastMCP:
    """Build the FastMCP instance with all kentro tools registered.

    No I/O happens here — this just registers the tool functions. Each tool
    looks up its runtime deps via `_current_ctx()` so this function is safe to
    call at module import time.

    `streamable_http_path="/"` is critical: by default FastMCP routes its
    streamable-HTTP endpoint at `/mcp` *inside* its own Starlette app. We then
    mount that app at `/mcp` on the parent FastAPI app — which would produce
    the doubled URL `/mcp/mcp`. Setting the inner path to `/` makes the parent
    mount the canonical entry point at `http://host/mcp`.
    """
    mcp = FastMCP("kentro", streamable_http_path="/")
    _register_tools(mcp)
    return mcp


def _register_tools(mcp: FastMCP) -> None:
    """Tools split out so `build_mcp()` is a one-line read."""

    @mcp.tool(description="Store a free-form fact in the catch-all `Note` entity.")
    def kentro_remember(
        subject: str,
        predicate: str,
        object_value: Any,
        confidence: float | None = None,
        source_label: str | None = None,
    ) -> dict:
        ctx = _current_ctx()
        schema = SchemaRegistry(ctx.principal.store)
        if schema.get("Note") is None:
            schema.list_all()  # auto-seeds Note
        # ACL once up-front; bail before any per-field writes if denied.
        ruleset = load_active_ruleset(ctx.principal.store)
        acl = evaluate_write(
            entity_type="Note",
            field_name=None,
            agent_id=ctx.principal.agent_id,
            ruleset=ruleset,
        )
        if not acl.allowed:
            return {
                "status": WriteStatus.PERMISSION_DENIED.value,
                "entity_type": "Note",
                "entity_key": subject,
                "reason": acl.reason,
            }
        # Atomic multi-field write — mirrors routes/memory.py::remember exactly.
        # Codex 2026-05-03 high finding: per-field commits could leave a half-
        # written Note when a per-field deny landed mid-loop. `write_fields_bulk`
        # pre-validates ACL across all fields and writes inside one transaction.
        fields: list[tuple[str, str, float | None]] = [
            ("subject", json.dumps(subject), confidence),
            ("predicate", json.dumps(predicate), confidence),
            # Single dumps: persists canonical JSON; one decode on read returns
            # the original value. The previous double-dumps left object_json as
            # an opaque string on read.
            ("object_json", json.dumps(object_value), confidence),
        ]
        if source_label is not None:
            fields.append(("source_label", json.dumps(source_label), confidence))
        results = write_fields_bulk(
            store=ctx.principal.store,
            schema=schema,
            agent_id=ctx.principal.agent_id,
            entity_type="Note",
            entity_key=subject,
            fields=fields,
        )
        # Mirror routes/memory.py::remember: return first PD (with the meaningful
        # reason) when present; otherwise the last result.
        for r in results:
            if r.status == WriteStatus.PERMISSION_DENIED:
                return r.model_dump(mode="json")
        return results[-1].model_dump(mode="json") if results else {}

    @mcp.tool(description="Read an entity by (type, key) using the default AutoResolver.")
    def kentro_read(entity_type: str, entity_key: str) -> dict:
        ctx = _current_ctx()
        schema = SchemaRegistry(ctx.principal.store)
        ruleset = load_active_ruleset(ctx.principal.store)
        record = read_entity(
            store=ctx.principal.store,
            schema=schema,
            ruleset=ruleset,
            agent_id=ctx.principal.agent_id,
            entity_type=entity_type,
            entity_key=entity_key,
            resolver=AutoResolverSpec(),
            llm=ctx.llm,
            bypass_acl=ctx.principal.is_admin,
        )
        return record.model_dump(mode="json")

    @mcp.tool(description="Write a single field on an entity.")
    def kentro_write(
        entity_type: str,
        entity_key: str,
        field_name: str,
        value_json: str,
        confidence: float | None = None,
    ) -> dict:
        ctx = _current_ctx()
        schema = SchemaRegistry(ctx.principal.store)
        result = write_field(
            store=ctx.principal.store,
            schema=schema,
            agent_id=ctx.principal.agent_id,
            entity_type=entity_type,
            entity_key=entity_key,
            field_name=field_name,
            value_json=value_json,
            confidence=confidence,
        )
        return result.model_dump(mode="json")

    @mcp.tool(description="Ingest a document (markdown body); returns the IngestionResult.")
    def kentro_ingest(content: str, label: str | None = None) -> dict:
        ctx = _current_ctx()
        schema = SchemaRegistry(ctx.principal.store)
        ruleset = load_active_ruleset(ctx.principal.store)
        result = ingest_document(
            store=ctx.principal.store,
            llm=ctx.llm,
            content=content.encode("utf-8"),
            label=label,
            registered_schemas=schema.list_all(),
            written_by_agent_id=ctx.principal.agent_id,
            rule_version=ruleset.version,
            smart_model=ctx.smart_model,
        )
        return result.model_dump(mode="json")

    @mcp.tool(
        description="ADMIN. Register one or more entity types (idempotent for unchanged defs)."
    )
    def kentro_register_schema(type_defs_json: str) -> dict:
        ctx = _current_ctx()
        _require_admin(ctx)
        adapter: TypeAdapter[list[EntityTypeDef]] = TypeAdapter(list[EntityTypeDef])
        type_defs = adapter.validate_json(type_defs_json)
        schema = SchemaRegistry(ctx.principal.store)
        schema.register_many(type_defs)
        return {"type_defs": [td.model_dump(mode="json") for td in schema.list_all()]}

    @mcp.tool(description="ADMIN. Apply a RuleSet (atomic version bump). Body is a JSON RuleSet.")
    def kentro_apply_rules(ruleset_json: str, summary: str | None = None) -> dict:
        ctx = _current_ctx()
        _require_admin(ctx)
        ruleset = RuleSet.model_validate_json(ruleset_json)
        new_version = apply_ruleset(
            ctx.principal.store,
            rules=ruleset.rules,
            summary=summary,
        )
        return {"version": new_version, "rules_applied": len(ruleset.rules)}

    @mcp.tool(
        description="Parse plain-English rule changes into a typed RuleSet (does NOT apply)."
    )
    def kentro_parse_rules(text: str) -> dict:
        ctx = _current_ctx()
        schema = SchemaRegistry(ctx.principal.store)
        agent_ids = tuple(a.id for a in ctx.registry.agents_for(ctx.principal.tenant_id))
        response = parse_nl_to_ruleset(
            llm=ctx.llm,
            text=text,
            registered_schemas=schema.list_all(),
            known_agent_ids=agent_ids,
        )
        return response.model_dump(mode="json")

    @mcp.tool(description="Return the active RuleSet for this tenant.")
    def kentro_get_rules() -> dict:
        ctx = _current_ctx()
        return load_active_ruleset(ctx.principal.store).model_dump(mode="json")

    @mcp.tool(description="List registered entity types for this tenant.")
    def kentro_list_schema() -> dict:
        ctx = _current_ctx()
        schema = SchemaRegistry(ctx.principal.store)
        return {"type_defs": [td.model_dump(mode="json") for td in schema.list_all()]}


__all__ = [
    "AuthMiddleware",
    "McpRequestContext",
    "build_mcp",
]
