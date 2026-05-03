"""Unit tests for the persistence layer."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import select

from kentro_server.store import StoreRegistry
from kentro_server.store.models import (
    AgentRow,
    DocumentRow,
    EntityRow,
    FieldWriteRow,
    RuleVersionRow,
)


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    return StoreRegistry(tmp_path / "kentro_state")


def test_tenant_store_creates_schema(registry: StoreRegistry) -> None:
    store = registry.get("demo-1")
    if not store.tenant_dir.exists():
        raise AssertionError("tenant dir missing after construction")
    if not store.docs_dir.exists():
        raise AssertionError("docs dir missing")
    if not store.witchcraft_dir.exists():
        raise AssertionError("witchcraft dir missing")
    if not (store.tenant_dir / "state.sqlite").exists():
        raise AssertionError("state.sqlite not created")


def test_round_trip_agent_and_entity_and_field_write(registry: StoreRegistry) -> None:
    store = registry.get("demo-1")

    with store.session() as s:
        s.add(AgentRow(id="sales", display_name="Sales"))
        s.add(RuleVersionRow(version=0, summary="initial"))
        ent = EntityRow(type="Customer", key="Acme")
        s.add(ent)
        s.commit()
        s.refresh(ent)

        write = FieldWriteRow(
            entity_id=ent.id,
            field_name="deal_size",
            value_json="250000",
            written_by_agent_id="sales",
            written_at=datetime.now(timezone.utc),
            rule_version_at_write=0,
        )
        s.add(write)
        s.commit()
        s.refresh(write)
        write_id = write.id

    # New session — confirm persistence to disk.
    with store.session() as s:
        rows = s.exec(
            select(FieldWriteRow).where(FieldWriteRow.id == write_id)
        ).all()
        if len(rows) != 1:
            raise AssertionError(f"expected 1 row, got {len(rows)}")
        if rows[0].value_json != "250000":
            raise AssertionError(f"unexpected value_json: {rows[0].value_json!r}")


def test_blob_store_put_get_delete(registry: StoreRegistry) -> None:
    store = registry.get("demo-1")
    key = f"{uuid4()}.md"
    content = b"# Acme call notes\n\nRenewal floated at $250K."
    store.blobs.put(key, content)
    if store.blobs.get(key) != content:
        raise AssertionError("blob round-trip mismatch")
    if not store.blobs.exists(key):
        raise AssertionError("exists() returned False after put")
    store.blobs.delete(key)
    if store.blobs.exists(key):
        raise AssertionError("blob still present after delete")


def test_blob_store_rejects_path_escape(registry: StoreRegistry) -> None:
    store = registry.get("demo-1")
    with pytest.raises(ValueError, match="escapes store root"):
        store.blobs.put("../escape.md", b"nope")


def test_two_tenants_are_isolated(registry: StoreRegistry) -> None:
    a = registry.get("demo-1")
    b = registry.get("demo-2")

    with a.session() as s:
        s.add(AgentRow(id="sales", display_name="A-Sales"))
        s.commit()

    with b.session() as s:
        s.add(AgentRow(id="sales", display_name="B-Sales"))
        s.commit()

    with a.session() as s:
        a_agent = s.exec(select(AgentRow).where(AgentRow.id == "sales")).one()
    with b.session() as s:
        b_agent = s.exec(select(AgentRow).where(AgentRow.id == "sales")).one()

    if a_agent.display_name != "A-Sales":
        raise AssertionError(f"tenant A leaked: {a_agent.display_name}")
    if b_agent.display_name != "B-Sales":
        raise AssertionError(f"tenant B leaked: {b_agent.display_name}")


def test_reset_tenant_wipes_state(registry: StoreRegistry) -> None:
    store = registry.get("demo-1")
    with store.session() as s:
        s.add(AgentRow(id="sales"))
        s.add(RuleVersionRow(version=0))
        s.add(DocumentRow(blob_key="x.md", content_hash="abc"))
        s.commit()
    if "demo-1" not in registry.known_tenants():
        raise AssertionError("tenant not listed before reset")

    registry.reset("demo-1")
    if "demo-1" in registry.known_tenants():
        raise AssertionError("tenant still on disk after reset")

    # Re-creating the tenant should give an empty schema, not the old data.
    fresh = registry.get("demo-1")
    with fresh.session() as s:
        agents = s.exec(select(AgentRow)).all()
        if agents:
            raise AssertionError(f"reset failed: still see agents {agents!r}")
