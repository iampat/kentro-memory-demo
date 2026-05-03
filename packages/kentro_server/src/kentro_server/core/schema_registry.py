"""Per-tenant schema registry.

Backed by `SchemaTypeRow` in the tenant's SQLite. Reads are cached in-process; cache
invalidates on every register call. The cache is per-instance, so multiple registries
attached to the same TenantStore do not share state — for v0 the read path constructs
one registry per request and that's fine.
"""

import logging

from sqlmodel import select

from kentro.types import EntityTypeDef
from kentro_server.store import TenantStore
from kentro_server.store.models import SchemaTypeRow

logger = logging.getLogger(__name__)


class SchemaRegistry:
    """Per-tenant registered entity types."""

    def __init__(self, store: TenantStore) -> None:
        self._store = store
        self._cache: list[EntityTypeDef] | None = None

    def register(self, type_def: EntityTypeDef) -> None:
        """Insert or replace the stored definition for `type_def.name`."""
        with self._store.session() as session:
            existing = session.exec(
                select(SchemaTypeRow).where(SchemaTypeRow.name == type_def.name)
            ).first()
            payload = type_def.model_dump_json()
            if existing is None:
                session.add(SchemaTypeRow(name=type_def.name, definition_json=payload))
            else:
                existing.definition_json = payload
                session.add(existing)
            session.commit()
        self._cache = None
        logger.info("schema registered: tenant=%s type=%s fields=%d",
                    self._store.tenant_id, type_def.name, len(type_def.fields))

    def register_many(self, type_defs: list[EntityTypeDef]) -> None:
        for td in type_defs:
            self.register(td)

    def list_all(self) -> list[EntityTypeDef]:
        """All registered types for this tenant. Cached after first load."""
        if self._cache is not None:
            return self._cache
        with self._store.session() as session:
            rows = list(session.exec(select(SchemaTypeRow)).all())
        self._cache = [
            EntityTypeDef.model_validate_json(row.definition_json) for row in rows
        ]
        return self._cache

    def names(self) -> list[str]:
        return [td.name for td in self.list_all()]

    def get(self, name: str) -> EntityTypeDef | None:
        for td in self.list_all():
            if td.name == name:
                return td
        return None


__all__ = ["SchemaRegistry"]
