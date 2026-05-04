"""Unit tests for the persistence layer."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from kentro_server.store import (
    AgentConfig,
    TenantConfig,
    TenantRegistry,
    TenantsConfig,
)
from kentro_server.store.models import (
    AgentRow,
    DocumentRow,
    EntityRow,
    FieldWriteRow,
    RuleVersionRow,
)
from sqlmodel import select


def _make_registry(state_dir: Path, *tenant_ids: str) -> TenantRegistry:
    config = TenantsConfig(
        tenants=tuple(
            TenantConfig(
                id=tid,
                agents=(AgentConfig(id="ingestion_agent", api_key=f"{tid}-key"),),
            )
            for tid in tenant_ids
        )
    )
    return TenantRegistry(state_dir, config)


@pytest.fixture
def registry(tmp_path: Path) -> TenantRegistry:
    return _make_registry(tmp_path / "kentro_state", "demo-1", "demo-2")


def test_tenant_store_creates_schema(registry: TenantRegistry) -> None:
    store = registry.get("demo-1")
    if not store.tenant_dir.exists():
        raise AssertionError("tenant dir missing after construction")
    if not store.docs_dir.exists():
        raise AssertionError("docs dir missing")
    if not store.witchcraft_dir.exists():
        raise AssertionError("witchcraft dir missing")
    if not (store.tenant_dir / "state.sqlite").exists():
        raise AssertionError("state.sqlite not created")


def test_round_trip_agent_and_entity_and_field_write(registry: TenantRegistry) -> None:
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
            written_at=datetime.now(UTC),
            rule_version_at_write=0,
        )
        s.add(write)
        s.commit()
        s.refresh(write)
        write_id = write.id

    # New session — confirm persistence to disk.
    with store.session() as s:
        rows = s.exec(select(FieldWriteRow).where(FieldWriteRow.id == write_id)).all()
        if len(rows) != 1:
            raise AssertionError(f"expected 1 row, got {len(rows)}")
        if rows[0].value_json != "250000":
            raise AssertionError(f"unexpected value_json: {rows[0].value_json!r}")


def test_blob_store_put_get_delete(registry: TenantRegistry) -> None:
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


def test_blob_store_rejects_path_escape(registry: TenantRegistry) -> None:
    store = registry.get("demo-1")
    with pytest.raises(ValueError, match="escapes store root"):
        store.blobs.put("../escape.md", b"nope")


def test_two_tenants_are_isolated(registry: TenantRegistry) -> None:
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


def test_reset_tenant_wipes_state(registry: TenantRegistry) -> None:
    store = registry.get("demo-1")
    with store.session() as s:
        s.add(AgentRow(id="sales"))
        s.add(RuleVersionRow(version=0))
        s.add(DocumentRow(blob_key="x.md", content_hash="abc"))
        s.commit()
    if "demo-1" not in registry.known_tenants():
        raise AssertionError("tenant not listed before reset")

    registry.reset("demo-1")

    # The tenant remains in the registry (per its config entry), but with empty state.
    if "demo-1" not in registry.known_tenants():
        raise AssertionError("tenant should be re-created from config after reset")
    with registry.get("demo-1").session() as s:
        agents = s.exec(select(AgentRow)).all()
        if agents:
            raise AssertionError(f"reset failed: still see agents {agents!r}")


def test_invalid_tenant_id_rejected(tmp_path: Path) -> None:
    """Codex finding: tenant IDs that escape the state root must be rejected."""
    bad_config = TenantsConfig(
        tenants=(
            TenantConfig(
                id="../escape",
                agents=(AgentConfig(id="x", api_key="x"),),
            ),
        ),
    )
    with pytest.raises(ValueError, match="invalid tenant_id"):
        TenantRegistry(tmp_path / "kentro_state", bad_config)


def test_unknown_tenant_id_raises(registry: TenantRegistry) -> None:
    with pytest.raises(KeyError, match="unknown tenant_id"):
        registry.get("not-configured")


def test_lookup_by_api_key_returns_tenant_agent_and_admin_flag(
    registry: TenantRegistry,
) -> None:
    store, agent_id, is_admin = registry.by_api_key("demo-1-key")
    if store.tenant_id != "demo-1":
        raise AssertionError(f"api-key lookup wrong: got tenant {store.tenant_id}")
    if agent_id != "ingestion_agent":
        raise AssertionError(f"api-key lookup wrong: got agent {agent_id}")
    # The default fixture's agent has is_admin=False (set explicitly in store_test.py).
    if is_admin:
        raise AssertionError("fixture agent should not be admin by default")
    with pytest.raises(KeyError, match="unknown api_key"):
        registry.by_api_key("not-a-real-key")


def test_duplicate_api_keys_rejected_at_load(tmp_path: Path) -> None:
    """Same key in two different tenants would silently route to last-loaded — deny."""
    with pytest.raises(ValueError, match="duplicate api_key"):
        TenantsConfig(
            tenants=(
                TenantConfig(id="t1", agents=(AgentConfig(id="a", api_key="same"),)),
                TenantConfig(id="t2", agents=(AgentConfig(id="b", api_key="same"),)),
            ),
        )


def test_duplicate_agent_ids_in_one_tenant_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate agent id"):
        TenantConfig(
            id="t",
            agents=(
                AgentConfig(id="dup", api_key="k1"),
                AgentConfig(id="dup", api_key="k2"),
            ),
        )


def test_blob_store_rejects_sibling_prefix(registry: TenantRegistry) -> None:
    """Codex finding: string-prefix path check let `/state/docs2/x` pass when root was `/state/docs`."""
    store = registry.get("demo-1")
    sibling = store.docs_dir.parent / (store.docs_dir.name + "2")
    sibling.mkdir(exist_ok=True)
    rel_to_root = sibling.relative_to(store.docs_dir.parent)
    bad_key = f"../{rel_to_root}/secret.txt"
    with pytest.raises(ValueError, match="escapes store root"):
        store.blobs.put(bad_key, b"secret")


def test_blob_store_rejects_absolute_key(registry: TenantRegistry) -> None:
    store = registry.get("demo-1")
    with pytest.raises(ValueError, match="must be a relative path"):
        store.blobs.put("/etc/passwd", b"x")


def test_from_paths_creates_empty_config_when_missing(tmp_path: Path) -> None:
    """When `tenants.json` is missing, write an empty config (no hardcoded default
    tenant in code) and boot with zero tenants. The operator must populate the
    file before any authenticated request will work.
    """
    config_path = tmp_path / "tenants.json"
    state_dir = tmp_path / "kentro_state"
    if config_path.exists():
        raise AssertionError("precondition: tenants.json should not exist")

    registry = TenantRegistry.from_paths(state_dir=state_dir, config_path=config_path)

    if not config_path.exists():
        raise AssertionError("from_paths must write an empty tenants.json on first run")
    if registry.known_tenants() != []:
        raise AssertionError(
            f"expected zero tenants from empty config, got {registry.known_tenants()}"
        )
    # Re-load from the same file: still empty, no surprise default appearing.
    registry2 = TenantRegistry.from_paths(state_dir=state_dir, config_path=config_path)
    if registry2.known_tenants() != []:
        raise AssertionError(f"empty config didn't persist: {registry2.known_tenants()}")
