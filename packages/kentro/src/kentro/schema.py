"""SDK helper: turn a `kentro.Entity` subclass into a wire-form `EntityTypeDef`.

Used by `admin.schema.register([Customer, Deal, ...])`. The SDK doesn't validate
field shapes itself; it serializes the Pydantic field declarations and ships them
to the server. The server stores the definition and the ingestor uses the names.
"""

import json
from typing import Any

from pydantic.fields import PydanticUndefined

from kentro.types import Entity, EntityTypeDef, FieldDef


def entity_type_def_from(cls: type[Entity]) -> EntityTypeDef:
    """Introspect a Pydantic `Entity` subclass into the wire-form definition.

    Every field becomes optional in the wire form — Pydantic's `is_required()` is
    not honored because the schema-evolution contract treats all fields as optional
    (an entity can exist with zero known values; reads return UNKNOWN for missing
    fields). The Pydantic class's `required=True` declarations affect what the user
    is allowed to construct in their own code; they don't affect the server.
    """
    fields: list[FieldDef] = []
    for name, info in cls.model_fields.items():
        type_str = _format_annotation(info.annotation)
        default_json: str | None = None
        if info.default is not PydanticUndefined:
            try:
                default_json = json.dumps(info.default)
            except (TypeError, ValueError):
                # Non-JSON-serializable default — drop it; server can't reconstruct anyway.
                default_json = None
        fields.append(
            FieldDef(
                name=name,
                type_str=type_str,
                default_json=default_json,
            )
        )
    return EntityTypeDef(name=cls.__name__, fields=tuple(fields))


def _format_annotation(annotation: Any) -> str:
    """Render a Pydantic field annotation as a stable string for the wire form."""
    if annotation is None or annotation is type(None):
        return "None"
    if isinstance(annotation, type):
        return annotation.__name__
    # `int | None`, `list[str]`, `dict[str, int]` etc. all stringify cleanly in 3.13.
    return str(annotation)


__all__ = ["entity_type_def_from"]
