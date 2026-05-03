"""Per-tenant schema registry — protobuf-style evolution rules.

Backed by `SchemaTypeRow` in the tenant's SQLite. Read calls cache in-process; cache
invalidates on every register call.

Schema evolution rules (locked in Step 7):

| Operation                           | Decision                                      |
|-------------------------------------|-----------------------------------------------|
| Re-register identical definition    | Idempotent no-op                              |
| Add a new field                     | Allowed                                       |
| Mark a field deprecated             | Allowed (extractor stops emitting it)         |
| Rename a field                      | DENIED — add new + deprecate old              |
| Change a field's type_str           | DENIED — add a new field                      |
| Remove a field                      | DENIED — deprecate instead                    |
| Un-deprecate a field                | Allowed (resurrection)                        |

Built-in `Note` entity is auto-seeded on first list_all() call so a fresh tenant
can always remember free-form facts that don't fit the registered schema.
"""

import logging

from kentro.types import EntityTypeDef, FieldDef
from sqlmodel import select

from kentro_server.store import TenantStore
from kentro_server.store.models import SchemaTypeRow

logger = logging.getLogger(__name__)


# Built-in catch-all entity. Auto-registered for every tenant.
NOTE_TYPE_DEF = EntityTypeDef(
    name="Note",
    fields=(
        FieldDef(
            name="subject",
            type_str="str",
            default_json=None,
        ),
        FieldDef(
            name="predicate",
            type_str="str | None",
            default_json="null",
        ),
        FieldDef(
            # TODO(v0.1): `type_str="str"` is a v0 documentation lie — `RememberRequest.object_json`
            # is typed `Any` and the route json.dumps's it before storing. Reads decode once and
            # return the original Python value. Real fix: introduce a `value_type=any|json`
            # discriminator on FieldDef and let the extractor / writer respect it. For now, the
            # type_str is descriptive only — the server doesn't validate stored values against it.
            name="object_json",
            type_str="str",
            default_json='""',
        ),
        FieldDef(
            name="confidence",
            type_str="float | None",
            default_json="null",
        ),
        FieldDef(
            name="source_label",
            type_str="str | None",
            default_json="null",
        ),
    ),
)


class SchemaEvolutionError(ValueError):
    """A schema-register call violated the protobuf-style evolution rules."""


class SchemaRegistry:
    """Per-tenant registered entity types."""

    def __init__(self, store: TenantStore) -> None:
        self._store = store
        self._cache: list[EntityTypeDef] | None = None

    def register(self, type_def: EntityTypeDef) -> None:
        """Insert or replace the stored definition for `type_def.name`.

        Idempotent if `type_def` matches the existing definition byte-for-byte.
        Otherwise validates the change against the protobuf-style evolution rules
        and raises `SchemaEvolutionError` on disallowed transitions.
        """
        with self._store.session() as session:
            existing_row = session.exec(
                select(SchemaTypeRow).where(SchemaTypeRow.name == type_def.name)
            ).first()
            payload = type_def.model_dump_json()

            if existing_row is None:
                session.add(SchemaTypeRow(name=type_def.name, definition_json=payload))
                session.commit()
                self._cache = None
                logger.info(
                    "tenant=%s schema NEW type=%s fields=%d",
                    self._store.tenant_id,
                    type_def.name,
                    len(type_def.fields),
                )
                return

            existing = EntityTypeDef.model_validate_json(existing_row.definition_json)
            if existing == type_def:
                logger.debug(
                    "tenant=%s schema idempotent re-register type=%s",
                    self._store.tenant_id,
                    type_def.name,
                )
                return

            # Validate the diff. Raises SchemaEvolutionError on disallowed changes.
            _validate_evolution(existing, type_def)

            existing_row.definition_json = payload
            session.add(existing_row)
            session.commit()

        self._cache = None
        logger.info(
            "tenant=%s schema EVOLVED type=%s fields=%d",
            self._store.tenant_id,
            type_def.name,
            len(type_def.fields),
        )

    def register_many(self, type_defs: list[EntityTypeDef]) -> None:
        for td in type_defs:
            self.register(td)

    def list_all(self) -> list[EntityTypeDef]:
        """All registered types. Auto-seeds the built-in `Note` on first call."""
        if self._cache is not None:
            return self._cache
        with self._store.session() as session:
            rows = list(session.exec(select(SchemaTypeRow)).all())
        if not any(row.name == NOTE_TYPE_DEF.name for row in rows):
            # Seed the built-in catch-all so agents can always remember free-form facts.
            self._seed_note_unlocked()
            with self._store.session() as session:
                rows = list(session.exec(select(SchemaTypeRow)).all())
        self._cache = [EntityTypeDef.model_validate_json(row.definition_json) for row in rows]
        return self._cache

    def names(self) -> list[str]:
        return [td.name for td in self.list_all()]

    def get(self, name: str) -> EntityTypeDef | None:
        for td in self.list_all():
            if td.name == name:
                return td
        return None

    def _seed_note_unlocked(self) -> None:
        """Insert the built-in Note type. Called once per tenant on first list_all()."""
        with self._store.session() as session:
            existing = session.exec(
                select(SchemaTypeRow).where(SchemaTypeRow.name == NOTE_TYPE_DEF.name)
            ).first()
            if existing is not None:
                return
            session.add(
                SchemaTypeRow(
                    name=NOTE_TYPE_DEF.name,
                    definition_json=NOTE_TYPE_DEF.model_dump_json(),
                )
            )
            session.commit()


def _validate_evolution(old: EntityTypeDef, new: EntityTypeDef) -> None:
    """Enforce the protobuf-style schema-evolution rules."""
    old_by_name = {f.name: f for f in old.fields}
    new_by_name = {f.name: f for f in new.fields}

    removed = set(old_by_name) - set(new_by_name)
    if removed:
        raise SchemaEvolutionError(
            f"type {old.name!r}: removing fields not allowed (deprecate instead): {sorted(removed)}"
        )

    for name, new_field in new_by_name.items():
        old_field = old_by_name.get(name)
        if old_field is None:
            # New field — allowed, no constraint to check.
            continue
        if old_field.type_str != new_field.type_str:
            raise SchemaEvolutionError(
                f"type {old.name!r} field {name!r}: changing type "
                f"({old_field.type_str!r} -> {new_field.type_str!r}) is not allowed; "
                "add a new field with the new type instead"
            )
        # Default change is allowed (defaults are advisory metadata in v0).
        # `deprecated` true -> false is allowed (resurrection).
        # `deprecated` false -> true is allowed (deprecation).


__all__ = [
    "NOTE_TYPE_DEF",
    "SchemaEvolutionError",
    "SchemaRegistry",
]
