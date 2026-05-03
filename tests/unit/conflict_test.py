"""Tests for `kentro_server.core.conflict.record_field_write`.

These tests use a real SQLModel session via the per-tenant store, since the function
flushes to detect existing live writes inside the same transaction.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from kentro_server.core.conflict import record_field_write
from kentro_server.store import TenantConfig, TenantRegistry, TenantsConfig, TenantStore
from kentro_server.store.models import (
    AgentRow,
    ConflictRow,
    EntityRow,
    FieldWriteRow,
    RuleVersionRow,
)
from sqlmodel import col, select


@pytest.fixture
def store(tmp_path: Path) -> TenantStore:
    config = TenantsConfig(tenants=(TenantConfig(id="demo-1", api_key="demo-1-key"),))
    reg = TenantRegistry(tmp_path / "kentro_state", config)
    s = reg.get("demo-1")
    with s.session() as session:
        session.add(AgentRow(id="ingestion_agent"))
        session.add(AgentRow(id="manual_sales"))
        session.add(RuleVersionRow(version=1))
        session.commit()
    return s


def _make_entity(store: TenantStore, type_: str = "Customer", key: str = "Acme") -> UUID:
    with store.session() as session:
        ent = EntityRow(type=type_, key=key)
        session.add(ent)
        session.commit()
        session.refresh(ent)
        return ent.id


def _open_conflicts(store: TenantStore, entity_id: UUID, field_name: str) -> list[ConflictRow]:
    with store.session() as session:
        return list(
            session.exec(
                select(ConflictRow).where(
                    ConflictRow.entity_id == entity_id,
                    ConflictRow.field_name == field_name,
                    col(ConflictRow.resolved_at).is_(None),
                )
            ).all()
        )


def test_single_write_creates_no_conflict(store: TenantStore) -> None:
    entity_id = _make_entity(store)
    with store.session() as session:
        write, conflict = record_field_write(
            session,
            entity_id=entity_id,
            field_name="deal_size",
            value_json="250000",
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        session.commit()
        if conflict is not None:
            raise AssertionError(f"single write should not create a conflict, got {conflict!r}")
        if write.value_json != "250000":
            raise AssertionError(f"expected value 250000, got {write.value_json!r}")
    if _open_conflicts(store, entity_id, "deal_size"):
        raise AssertionError("no ConflictRow should exist after a single write")


def test_two_writes_same_value_corroboration_no_conflict(store: TenantStore) -> None:
    entity_id = _make_entity(store)
    with store.session() as session:
        record_field_write(
            session,
            entity_id=entity_id,
            field_name="name",
            value_json='"Acme"',
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        _, conflict = record_field_write(
            session,
            entity_id=entity_id,
            field_name="name",
            value_json='"Acme"',
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        session.commit()
        if conflict is not None:
            raise AssertionError("identical-value writes are corroboration, not conflict")
    if _open_conflicts(store, entity_id, "name"):
        raise AssertionError("corroboration must not create a ConflictRow")


def test_two_writes_different_values_create_one_conflict(store: TenantStore) -> None:
    entity_id = _make_entity(store)
    with store.session() as session:
        _, c1 = record_field_write(
            session,
            entity_id=entity_id,
            field_name="deal_size",
            value_json="250000",
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        _, c2 = record_field_write(
            session,
            entity_id=entity_id,
            field_name="deal_size",
            value_json="300000",
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        session.commit()
    if c1 is not None:
        raise AssertionError("first write must not create a ConflictRow")
    if c2 is None:
        raise AssertionError("second (disagreeing) write must create a ConflictRow")
    open_conflicts = _open_conflicts(store, entity_id, "deal_size")
    if len(open_conflicts) != 1:
        raise AssertionError(f"expected exactly 1 open conflict, got {len(open_conflicts)}")


def test_three_writes_three_values_reuse_single_conflict(store: TenantStore) -> None:
    entity_id = _make_entity(store)
    conflict_ids: list[UUID] = []
    with store.session() as session:
        for value in ("250000", "300000", "350000"):
            _, conflict = record_field_write(
                session,
                entity_id=entity_id,
                field_name="deal_size",
                value_json=value,
                written_by_agent_id="ingestion_agent",
                rule_version_at_write=1,
            )
            if conflict is not None:
                conflict_ids.append(conflict.id)
        session.commit()
    if len(conflict_ids) != 2:
        raise AssertionError(
            f"expected conflict to be reported on writes 2 and 3 (re-using), got {len(conflict_ids)}"
        )
    if conflict_ids[0] != conflict_ids[1]:
        raise AssertionError("third write should reuse the open ConflictRow, not create a new one")
    open_conflicts = _open_conflicts(store, entity_id, "deal_size")
    if len(open_conflicts) != 1:
        raise AssertionError(f"only 1 open conflict expected, got {len(open_conflicts)}")


def test_two_writes_different_fields_no_conflict(store: TenantStore) -> None:
    """Multi-document entity hydration: phone in doc 1, email in doc 2 → no conflict."""
    entity_id = _make_entity(store, type_="Person", key="Ali")
    with store.session() as session:
        _, c1 = record_field_write(
            session,
            entity_id=entity_id,
            field_name="phone",
            value_json='"778-968-1361"',
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        _, c2 = record_field_write(
            session,
            entity_id=entity_id,
            field_name="email",
            value_json='"ali@kentro.ai"',
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        session.commit()
    if c1 is not None or c2 is not None:
        raise AssertionError("writes on different fields must not create conflicts")


def test_resolved_conflict_then_new_disagreement_creates_fresh_conflict(
    store: TenantStore,
) -> None:
    entity_id = _make_entity(store)
    with store.session() as session:
        record_field_write(
            session,
            entity_id=entity_id,
            field_name="deal_size",
            value_json="250000",
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        _, c2 = record_field_write(
            session,
            entity_id=entity_id,
            field_name="deal_size",
            value_json="300000",
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        if c2 is None:
            raise AssertionError("expected a conflict row after the second disagreeing write")
        c2_id = c2.id
        session.commit()

    # Simulate a resolution: mark the conflict resolved.
    with store.session() as session:
        first = session.get(ConflictRow, c2_id)
        if first is None:
            raise AssertionError("conflict row missing")
        first.resolved_at = datetime.now(UTC)
        session.add(first)
        session.commit()

    # Now a new disagreeing write should create a NEW open ConflictRow, not touch the resolved one.
    with store.session() as session:
        _, c3 = record_field_write(
            session,
            entity_id=entity_id,
            field_name="deal_size",
            value_json="350000",
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        if c3 is None:
            raise AssertionError(
                "disagreement after a resolved conflict must open a fresh ConflictRow"
            )
        c3_id = c3.id
        session.commit()
    if c3_id == c2_id:
        raise AssertionError("a resolved conflict must not be reused")


def test_write_persists_full_lineage_fields(store: TenantStore) -> None:
    entity_id = _make_entity(store)
    with store.session() as session:
        write, _ = record_field_write(
            session,
            entity_id=entity_id,
            field_name="deal_size",
            value_json="250000",
            confidence=0.9,
            written_by_agent_id="ingestion_agent",
            rule_version_at_write=1,
        )
        session.commit()
        write_id = write.id
    with store.session() as session:
        rows = list(session.exec(select(FieldWriteRow).where(FieldWriteRow.id == write_id)).all())
        if len(rows) != 1:
            raise AssertionError("write not persisted")
        row = rows[0]
        if row.confidence != 0.9:
            raise AssertionError(f"confidence not persisted, got {row.confidence}")
        if row.rule_version_at_write != 1:
            raise AssertionError(
                f"rule_version_at_write not persisted, got {row.rule_version_at_write}"
            )
        if row.superseded:
            raise AssertionError("new writes must not be marked superseded")
