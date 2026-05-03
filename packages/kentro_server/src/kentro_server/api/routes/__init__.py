"""HTTP routers grouped by domain. Mounted into the app from `main.py`."""

from kentro_server.api.routes.documents import router as documents_router
from kentro_server.api.routes.entities import router as entities_router
from kentro_server.api.routes.memory import router as memory_router
from kentro_server.api.routes.rules import router as rules_router
from kentro_server.api.routes.schema import router as schema_router

__all__ = [
    "documents_router",
    "entities_router",
    "memory_router",
    "rules_router",
    "schema_router",
]
