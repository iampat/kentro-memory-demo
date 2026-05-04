"""HTTP routers grouped by domain. Mounted into the app from `main.py`."""

from kentro_server.api.routes.demo import router as demo_router
from kentro_server.api.routes.documents import router as documents_router
from kentro_server.api.routes.entities import router as entities_router
from kentro_server.api.routes.events import router as events_router
from kentro_server.api.routes.memory import router as memory_router
from kentro_server.api.routes.rules import router as rules_router
from kentro_server.api.routes.schema import router as schema_router

__all__ = [
    "demo_router",
    "documents_router",
    "entities_router",
    "events_router",
    "memory_router",
    "rules_router",
    "schema_router",
]
