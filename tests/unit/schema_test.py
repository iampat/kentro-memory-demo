"""Tests for the SDK schema introspection helper + the server-side SchemaRegistry."""

from pathlib import Path

import pytest

from kentro.schema import entity_type_def_from
from kentro.types import Entity, EntityTypeDef, FieldDef
from kentro_server.core.schema_registry import SchemaRegistry
from kentro_server.store import TenantConfig, TenantRegistry, TenantsConfig, TenantStore


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


def test_introspect_required_and_optional_fields() -> None:
    td = entity_type_def_from(Customer)
    if td.name != "Customer":
        raise AssertionError(f"expected name=Customer, got {td.name!r}")
    by_name = {f.name: f for f in td.fields}
    if {"name", "contact", "deal_size", "sales_notes"} != set(by_name):
        raise AssertionError(f"unexpected field set: {set(by_name)}")
    if not by_name["name"].required:
        raise AssertionError("name should be required")
    if by_name["contact"].required:
        raise AssertionError("contact should be optional")


def test_introspect_renders_optional_type_as_pipe_form() -> None:
    td = entity_type_def_from(Customer)
    by_name = {f.name: f for f in td.fields}
    if "None" not in by_name["contact"].type_str:
        raise AssertionError(f"optional type should mention None, got {by_name['contact'].type_str!r}")


def test_introspect_captures_string_default() -> None:
    td = entity_type_def_from(Customer)
    by_name = {f.name: f for f in td.fields}
    if by_name["sales_notes"].default_json != '""':
        raise AssertionError(f"string default not captured, got {by_name['sales_notes'].default_json!r}")


# === Server-side SchemaRegistry ===

@pytest.fixture
def store(tmp_path: Path) -> TenantStore:
    config = TenantsConfig(tenants=(TenantConfig(id="demo-1", api_key="demo-1-key"),))
    return TenantRegistry(tmp_path / "kentro_state", config).get("demo-1")


def test_register_then_list_round_trips(store: TenantStore) -> None:
    reg = SchemaRegistry(store)
    customer = entity_type_def_from(Customer)
    person = entity_type_def_from(Person)

    reg.register(customer)
    reg.register(person)

    names = reg.names()
    if set(names) != {"Customer", "Person"}:
        raise AssertionError(f"unexpected names: {names}")

    got = reg.get("Customer")
    if got is None or got != customer:
        raise AssertionError(f"round-trip mismatch: {got!r}")


def test_register_replaces_existing(store: TenantStore) -> None:
    reg = SchemaRegistry(store)
    reg.register(EntityTypeDef(name="Customer", fields=(FieldDef(name="x", type_str="str"),)))
    reg.register(EntityTypeDef(name="Customer", fields=(FieldDef(name="y", type_str="int"),)))

    got = reg.get("Customer")
    if got is None or len(got.fields) != 1 or got.fields[0].name != "y":
        raise AssertionError(f"register did not replace, got {got!r}")


def test_register_many_persists_across_instances(store: TenantStore) -> None:
    SchemaRegistry(store).register_many([
        entity_type_def_from(Customer),
        entity_type_def_from(Person),
    ])

    fresh = SchemaRegistry(store)
    if set(fresh.names()) != {"Customer", "Person"}:
        raise AssertionError(f"new SchemaRegistry didn't see persisted rows: {fresh.names()}")


def test_get_returns_none_for_unknown(store: TenantStore) -> None:
    reg = SchemaRegistry(store)
    if reg.get("NotARealType") is not None:
        raise AssertionError("get should return None for an unregistered type")
