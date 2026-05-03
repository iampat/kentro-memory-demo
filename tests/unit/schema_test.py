"""Tests for the SDK schema introspection helper + the server-side SchemaRegistry."""

from pathlib import Path

import pytest
from kentro.schema import entity_type_def_from
from kentro.types import Entity, EntityTypeDef, FieldDef
from kentro_server.core.schema_registry import SchemaEvolutionError, SchemaRegistry
from kentro_server.store import (
    AgentConfig,
    TenantConfig,
    TenantRegistry,
    TenantsConfig,
    TenantStore,
)

# === SDK introspection ===


class Customer(Entity):
    name: str
    contact: str | None = None
    deal_size: float | None = None
    sales_notes: str = ""


class Person(Entity):
    name: str
    phone: str | None = None
    email: str | None = None


def test_introspect_captures_all_field_names() -> None:
    """All fields are optional in the wire form (no `required` attribute).
    The schema-evolution contract treats every field as optional; an entity can
    exist as a bare row with zero known values."""
    td = entity_type_def_from(Customer)
    if td.name != "Customer":
        raise AssertionError(f"expected name=Customer, got {td.name!r}")
    by_name = {f.name: f for f in td.fields}
    if {"name", "contact", "deal_size", "sales_notes"} != set(by_name):
        raise AssertionError(f"unexpected field set: {set(by_name)}")
    # No `required` attribute exists on FieldDef anymore — by design.
    if hasattr(by_name["name"], "required"):
        raise AssertionError("FieldDef.required must not exist; everything is optional")
    if by_name["name"].deprecated:
        raise AssertionError("freshly introspected fields must not be deprecated")


def test_introspect_renders_optional_type_as_pipe_form() -> None:
    td = entity_type_def_from(Customer)
    by_name = {f.name: f for f in td.fields}
    if "None" not in by_name["contact"].type_str:
        raise AssertionError(
            f"optional type should mention None, got {by_name['contact'].type_str!r}"
        )


def test_introspect_captures_string_default() -> None:
    td = entity_type_def_from(Customer)
    by_name = {f.name: f for f in td.fields}
    if by_name["sales_notes"].default_json != '""':
        raise AssertionError(
            f"string default not captured, got {by_name['sales_notes'].default_json!r}"
        )


# === Server-side SchemaRegistry ===


@pytest.fixture
def store(tmp_path: Path) -> TenantStore:
    config = TenantsConfig(
        tenants=(
            TenantConfig(
                id="demo-1",
                agents=(AgentConfig(id="ingestion_agent", api_key="demo-1-key"),),
            ),
        )
    )
    return TenantRegistry(tmp_path / "kentro_state", config).get("demo-1")


def test_register_then_list_round_trips(store: TenantStore) -> None:
    reg = SchemaRegistry(store)
    customer = entity_type_def_from(Customer)
    person = entity_type_def_from(Person)

    reg.register(customer)
    reg.register(person)

    names = set(reg.names())
    # `Note` is auto-seeded on first list_all() — built-in catch-all type.
    expected = {"Customer", "Person", "Note"}
    if not expected.issubset(names):
        raise AssertionError(f"missing expected names: {expected - names}")

    got = reg.get("Customer")
    if got is None or got != customer:
        raise AssertionError(f"round-trip mismatch: {got!r}")


def test_idempotent_re_register_is_noop(store: TenantStore) -> None:
    reg = SchemaRegistry(store)
    customer = entity_type_def_from(Customer)
    reg.register(customer)
    # Re-registering the EXACT same definition is a no-op (idempotent).
    reg.register(customer)
    got = reg.get("Customer")
    if got is None or got != customer:
        raise AssertionError(f"idempotent re-register changed the def: {got!r}")


def test_register_rejects_field_removal(store: TenantStore) -> None:
    """Removing a field is denied — deprecate instead."""
    reg = SchemaRegistry(store)
    reg.register(
        EntityTypeDef(
            name="Customer",
            fields=(
                FieldDef(name="x", type_str="str"),
                FieldDef(name="y", type_str="int"),
            ),
        )
    )
    with pytest.raises(SchemaEvolutionError, match="removing fields not allowed"):
        reg.register(EntityTypeDef(name="Customer", fields=(FieldDef(name="x", type_str="str"),)))


def test_register_rejects_type_change(store: TenantStore) -> None:
    """Changing a field's type is denied — add a new field with the new type."""
    reg = SchemaRegistry(store)
    reg.register(EntityTypeDef(name="Customer", fields=(FieldDef(name="age", type_str="int"),)))
    with pytest.raises(SchemaEvolutionError, match="changing type"):
        reg.register(
            EntityTypeDef(name="Customer", fields=(FieldDef(name="age", type_str="str"),))
        )


def test_register_allows_field_addition(store: TenantStore) -> None:
    reg = SchemaRegistry(store)
    reg.register(EntityTypeDef(name="Customer", fields=(FieldDef(name="x", type_str="str"),)))
    reg.register(
        EntityTypeDef(
            name="Customer",
            fields=(
                FieldDef(name="x", type_str="str"),
                FieldDef(name="y", type_str="int"),
            ),
        )
    )
    got = reg.get("Customer")
    if got is None or {f.name for f in got.fields} != {"x", "y"}:
        raise AssertionError(f"new field not added, got {got!r}")


def test_register_allows_field_deprecation(store: TenantStore) -> None:
    reg = SchemaRegistry(store)
    reg.register(
        EntityTypeDef(
            name="Customer",
            fields=(
                FieldDef(name="x", type_str="str"),
                FieldDef(name="y", type_str="int"),
            ),
        )
    )
    reg.register(
        EntityTypeDef(
            name="Customer",
            fields=(
                FieldDef(name="x", type_str="str"),
                FieldDef(name="y", type_str="int", deprecated=True),
            ),
        )
    )
    got = reg.get("Customer")
    if got is None:
        raise AssertionError("Customer disappeared")
    by_name = {f.name: f for f in got.fields}
    if not by_name["y"].deprecated:
        raise AssertionError("y should be deprecated after re-register")
    if by_name["x"].deprecated:
        raise AssertionError("x should remain non-deprecated")


def test_note_is_auto_seeded(store: TenantStore) -> None:
    """The built-in catch-all `Note` entity is registered automatically per tenant."""
    reg = SchemaRegistry(store)
    if reg.get("Note") is None:
        raise AssertionError("Note built-in not auto-seeded on first list_all()")
    note = reg.get("Note")
    if note is None or {f.name for f in note.fields} != {
        "subject",
        "predicate",
        "object_json",
        "confidence",
        "source_label",
    }:
        raise AssertionError(f"unexpected Note schema: {note!r}")


def test_register_many_persists_across_instances(store: TenantStore) -> None:
    SchemaRegistry(store).register_many(
        [
            entity_type_def_from(Customer),
            entity_type_def_from(Person),
        ]
    )

    fresh = SchemaRegistry(store)
    names = set(fresh.names())
    # `Note` is auto-seeded; only check that user-registered ones survived.
    if not {"Customer", "Person"}.issubset(names):
        raise AssertionError(f"new SchemaRegistry didn't see persisted rows: {names}")


def test_get_returns_none_for_unknown(store: TenantStore) -> None:
    reg = SchemaRegistry(store)
    if reg.get("NotARealType") is not None:
        raise AssertionError("get should return None for an unregistered type")
