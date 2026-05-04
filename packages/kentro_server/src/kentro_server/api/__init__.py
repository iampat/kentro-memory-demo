"""HTTP API surface — auth, request DTOs, and the per-domain routers.

Wire-form response types live in `kentro.types` (the SDK's source of truth).
The server imports them directly — there is no parallel mirror. If a server-side
type ever needs to diverge (e.g. MCP-facing string statuses), introduce a
server-only subclass at that point; do not pre-emptively duplicate.

Routers are mounted into the FastAPI app from `kentro_server.main` via
`app.include_router(...)`.
"""

from kentro_server.api.routes import (
    catalog_router,
    demo_router,
    documents_router,
    entities_router,
    events_router,
    memory_router,
    rules_router,
    schema_router,
    viz_router,
)

__all__ = [
    "catalog_router",
    "demo_router",
    "documents_router",
    "entities_router",
    "events_router",
    "memory_router",
    "rules_router",
    "schema_router",
    "viz_router",
]
