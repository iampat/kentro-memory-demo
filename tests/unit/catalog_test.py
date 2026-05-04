"""Tests for the toggleable-event catalog (`core.catalog` + `/catalog` routes).

Covers the contract from the design discussion in CHANGE_LOG:
- Seed registers events as inactive.
- First activation runs the underlying ingest and tags created rows with
  `event_id`. activation_seq starts at 1.
- Deactivate is a flag flip; rows persist (filtering happens in the read
  path, exercised separately).
- Re-activation bumps `activation_seq` past any prior max so resolver
  tie-breaking sees the re-toggled event as "newest".
- Auth: only admin can toggle.
- Idempotency: registering the same `catalog_key` twice returns the same
  row.
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from kentro.types import EntityTypeDef, FieldDef
from kentro_server.api.deps import get_llm_client
from kentro_server.core.catalog import (
    activate_event,
    deactivate_event,
    list_events,
    register_ingest_event,
)
from kentro_server.core.schema_registry import SchemaRegistry
from kentro_server.main import app
from kentro_server.skills.llm_client import (
    ExtractedEntity,
    ExtractedField,
    ExtractionResult,
)
from kentro_server.store import (
    AgentConfig,
    TenantConfig,
    TenantRegistry,
    TenantsConfig,
    TenantStore,
)
from kentro_server.store.models import (
    AgentRow,
    DocumentRow,
    EventRow,
    FieldWriteRow,
    RuleVersionRow,
)
from sqlmodel import select

from tests.unit._helpers import ADMIN_KEY, AGENT_KEY, FakeLLM

# === Module-helper fixtures ===============================================


@pytest.fixture
def store(tmp_path: Path) -> TenantStore:
    """A fresh tenant store with one admin agent + a baseline rule version."""
    config = TenantsConfig(
        tenants=(
            TenantConfig(
                id="demo-1",
                agents=(AgentConfig(id="ingestion_agent", api_key="demo-1-key"),),
            ),
        )
    )
    reg = TenantRegistry(tmp_path / "kentro_state", config)
    s = reg.get("demo-1")
    with s.session() as session:
        session.add(AgentRow(id="ingestion_agent"))
        session.add(RuleVersionRow(version=1))
        session.commit()
    return s


@pytest.fixture
def http_client(isolated_state: None, fake_llm: FakeLLM) -> Iterator[TestClient]:
    """TestClient with the LLM client overridden — used for HTTP tests."""
    fake_llm.extraction_result = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Customer",
                key="Acme",
                fields=(ExtractedField(field_name="name", value_json='"Acme"', confidence=0.99),),
            ),
        ),
    )
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_llm_client, None)


def _admin() -> dict:
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


def _agent() -> dict:
    return {"Authorization": f"Bearer {AGENT_KEY}"}


# === core.catalog domain tests ============================================


def test_register_ingest_event_creates_inactive_row(store: TenantStore) -> None:
    """First registration creates an inactive event with no activation_seq."""
    event = register_ingest_event(
        store,
        catalog_key="corpus:foo.md",
        title="foo.md",
        description="a fixture",
        content="hello",
        label="foo.md",
        source_class="written",
        catalog_order=1,
    )
    if event.active:
        raise AssertionError("freshly registered event should be inactive")
    if event.activation_seq is not None:
        raise AssertionError("freshly registered event should have no activation_seq")
    if event.catalog_order != 1:
        raise AssertionError(f"expected catalog_order=1, got {event.catalog_order}")


def test_register_ingest_event_is_idempotent_on_catalog_key(store: TenantStore) -> None:
    """Re-registering the same key returns the existing row, doesn't duplicate."""
    first = register_ingest_event(
        store,
        catalog_key="corpus:foo.md",
        title="foo.md",
        description=None,
        content="hello",
        label="foo.md",
        source_class=None,
        catalog_order=1,
    )
    second = register_ingest_event(
        store,
        catalog_key="corpus:foo.md",
        title="UPDATED title that should be ignored",
        description="UPDATED description that should be ignored",
        content="UPDATED content",
        label="foo.md",
        source_class=None,
        catalog_order=99,
    )
    if second.id != first.id:
        raise AssertionError("expected re-registration to return the same EventRow.id")
    # Catalog metadata stays the original — no UPDATE on re-register.
    if second.title != "foo.md":
        raise AssertionError(f"re-register must not overwrite title; got {second.title!r}")


def test_list_events_orders_by_catalog_order(store: TenantStore) -> None:
    """The catalog list is sorted by `catalog_order` for stable UI rendering."""
    register_ingest_event(
        store,
        catalog_key="b",
        title="B",
        description=None,
        content="b",
        label="b",
        source_class=None,
        catalog_order=2,
    )
    register_ingest_event(
        store,
        catalog_key="a",
        title="A",
        description=None,
        content="a",
        label="a",
        source_class=None,
        catalog_order=1,
    )
    rows = list_events(store)
    if [r.catalog_key for r in rows] != ["a", "b"]:
        raise AssertionError(f"expected ['a', 'b'] order, got {[r.catalog_key for r in rows]}")


# === HTTP route tests =====================================================


def test_get_catalog_empty_returns_empty_tuple(http_client: TestClient) -> None:
    response = http_client.get("/catalog", headers=_admin())
    if response.status_code != 200:
        raise AssertionError(f"expected 200, got {response.status_code}: {response.text}")
    payload = response.json()
    if payload["events"] != []:
        raise AssertionError(f"expected empty events, got {payload['events']}")


def test_seed_then_list_catalog_returns_inactive_events(http_client: TestClient) -> None:
    """`POST /demo/seed` registers the corpus as inactive catalog events."""
    seed = http_client.post("/demo/seed", headers=_admin())
    if seed.status_code != 200:
        raise AssertionError(f"seed failed: {seed.status_code} {seed.text}")
    body = seed.json()
    if body["catalog_events_registered"] < 1:
        raise AssertionError(
            f"expected catalog_events_registered >= 1, got {body['catalog_events_registered']}"
        )

    listing = http_client.get("/catalog", headers=_admin())
    events = listing.json()["events"]
    if len(events) < 1:
        raise AssertionError("expected at least one seeded catalog event")
    for ev in events:
        if ev["active"]:
            raise AssertionError(f"seeded events should start inactive, got: {ev}")
        if ev["activation_seq"] is not None:
            raise AssertionError(f"seeded events should have no activation_seq, got: {ev}")


def test_toggle_first_activation_sets_seq_and_active(http_client: TestClient) -> None:
    """First toggle activates: active=True, activation_seq=1, ingest runs."""
    http_client.post("/demo/seed", headers=_admin())
    listing = http_client.get("/catalog", headers=_admin())
    target = listing.json()["events"][0]

    toggled = http_client.post(f"/catalog/{target['id']}/toggle", headers=_admin())
    if toggled.status_code != 200:
        raise AssertionError(f"toggle failed: {toggled.status_code} {toggled.text}")
    event = toggled.json()["event"]
    if not event["active"]:
        raise AssertionError(f"event should be active after first toggle: {event}")
    if event["activation_seq"] != 1:
        raise AssertionError(
            f"first activation should set activation_seq=1, got {event['activation_seq']}"
        )


def test_toggle_off_then_on_bumps_activation_seq_past_others(http_client: TestClient) -> None:
    """Re-activation gets a fresh seq higher than every other event's seq.

    Models the demo's "re-toggle moves the event to the end of the stack"
    semantic. Resolver tie-breaks on `activation_seq`, so the re-toggled
    event becomes the LatestWrite winner.
    """
    http_client.post("/demo/seed", headers=_admin())
    events = http_client.get("/catalog", headers=_admin()).json()["events"]
    if len(events) < 2:
        raise AssertionError("need at least two seeded events for this test")
    e1, e2 = events[0], events[1]

    # Activate e1 (seq=1), then e2 (seq=2).
    a1 = http_client.post(f"/catalog/{e1['id']}/toggle", headers=_admin()).json()["event"]
    a2 = http_client.post(f"/catalog/{e2['id']}/toggle", headers=_admin()).json()["event"]
    if a1["activation_seq"] != 1 or a2["activation_seq"] != 2:
        raise AssertionError(
            f"unexpected initial seqs: e1={a1['activation_seq']} e2={a2['activation_seq']}"
        )

    # Toggle e1 off, then back on. Should land at seq=3 (above e2's seq=2).
    http_client.post(f"/catalog/{e1['id']}/toggle", headers=_admin())  # off
    re_e1 = http_client.post(f"/catalog/{e1['id']}/toggle", headers=_admin()).json()["event"]
    if re_e1["activation_seq"] != 3:
        raise AssertionError(
            f"re-activation should bump seq past max; expected 3, got {re_e1['activation_seq']}"
        )
    if not re_e1["active"]:
        raise AssertionError("re-activated event should be active")


def test_deactivate_keeps_rows_in_db(http_client: TestClient) -> None:
    """Deactivate is a flag flip; FieldWriteRows persist tagged with event_id.

    The read-path filter (added in a sibling step) is what hides them.
    """
    http_client.post("/demo/seed", headers=_admin())
    target = http_client.get("/catalog", headers=_admin()).json()["events"][0]
    http_client.post(f"/catalog/{target['id']}/toggle", headers=_admin())  # activate
    # Deactivate.
    response = http_client.post(f"/catalog/{target['id']}/toggle", headers=_admin())
    if response.json()["event"]["active"]:
        raise AssertionError("event should be inactive after second toggle")

    # Inspect the DB directly: rows still exist with event_id set.
    registry = app.state.tenant_registry
    target_uuid = UUID(target["id"])
    store = registry.get("local")
    with store.session() as session:
        docs = session.exec(select(DocumentRow).where(DocumentRow.event_id == target_uuid)).all()
        writes = session.exec(
            select(FieldWriteRow).where(FieldWriteRow.event_id == target_uuid)
        ).all()
    if not docs:
        raise AssertionError("DocumentRow should persist after deactivate")
    if not writes:
        raise AssertionError("FieldWriteRows should persist after deactivate")


def test_toggle_requires_admin(http_client: TestClient) -> None:
    """Non-admin agents get 403 on toggle (it can drive an LLM call)."""
    http_client.post("/demo/seed", headers=_admin())
    target = http_client.get("/catalog", headers=_admin()).json()["events"][0]
    response = http_client.post(f"/catalog/{target['id']}/toggle", headers=_agent())
    if response.status_code != 403:
        raise AssertionError(
            f"expected 403 for non-admin toggle, got {response.status_code}: {response.text}"
        )


def test_toggle_unknown_id_returns_404(http_client: TestClient) -> None:
    response = http_client.post(
        "/catalog/00000000-0000-0000-0000-000000000000/toggle", headers=_admin()
    )
    if response.status_code != 404:
        raise AssertionError(
            f"expected 404 for unknown event id, got {response.status_code}: {response.text}"
        )


def test_toggle_off_hides_writes_from_read(http_client: TestClient) -> None:
    """Activating an event surfaces writes; deactivating hides them again.

    This is the read-path contract that makes the catalog model work — the
    `event_id` JOIN filters out writes whose owning event is inactive.
    """
    # Apply a permissive ruleset so ingestion_agent can both write and read
    # Customer.name (default-deny otherwise).
    rules_resp = http_client.post(
        "/rules/apply",
        headers=_admin(),
        json={
            "ruleset": {
                "rules": [
                    {
                        "type": "entity_visibility",
                        "agent_id": "ingestion_agent",
                        "entity_type": "Customer",
                        "allowed": True,
                    },
                    {
                        "type": "write",
                        "agent_id": "ingestion_agent",
                        "entity_type": "Customer",
                        "allowed": True,
                    },
                    {
                        "type": "field_read",
                        "agent_id": "ingestion_agent",
                        "entity_type": "Customer",
                        "field_name": "name",
                        "allowed": True,
                    },
                ],
                "version": 0,
            },
            "summary": "test setup",
        },
    )
    if rules_resp.status_code != 200:
        raise AssertionError(f"rules/apply failed: {rules_resp.status_code} {rules_resp.text}")

    http_client.post("/demo/seed", headers=_admin())
    target = http_client.get("/catalog", headers=_admin()).json()["events"][0]

    # Activate: read should KNOW the field (extraction wrote Customer.Acme.name = "Acme").
    http_client.post(f"/catalog/{target['id']}/toggle", headers=_admin())
    read_active = http_client.get("/entities/Customer/Acme", headers=_admin())
    name_field_active = read_active.json()["fields"]["name"]
    if name_field_active["status"].upper() != "KNOWN":
        raise AssertionError(f"expected name=KNOWN after activation, got {name_field_active}")

    # Deactivate: same field should drop to UNKNOWN (no live writes).
    http_client.post(f"/catalog/{target['id']}/toggle", headers=_admin())
    read_inactive = http_client.get("/entities/Customer/Acme", headers=_admin())
    name_field_inactive = read_inactive.json()["fields"]["name"]
    if name_field_inactive["status"].upper() != "UNKNOWN":
        raise AssertionError(
            f"expected name=UNKNOWN after deactivation, got {name_field_inactive}"
        )


# === core.catalog activate/deactivate via direct call =====================


def test_activate_then_deactivate_via_core_api(store: TenantStore, fake_llm: FakeLLM) -> None:
    """Cover the pure-Python catalog API without the HTTP layer in the way."""
    fake_llm.extraction_result = ExtractionResult(
        entities=(
            ExtractedEntity(
                entity_type="Customer",
                key="Acme",
                fields=(ExtractedField(field_name="name", value_json='"Acme"', confidence=0.9),),
            ),
        ),
    )
    schema = SchemaRegistry(store)
    schema.register(
        EntityTypeDef(name="Customer", fields=(FieldDef(name="name", type_str="str"),))
    )

    event = register_ingest_event(
        store,
        catalog_key="ad-hoc:foo",
        title="foo",
        description=None,
        content="some text",
        label="foo",
        source_class="written",
        catalog_order=1,
    )
    activated, ingest_result = activate_event(
        store,
        schema=schema,
        llm=fake_llm,
        smart_model="gpt-stub",
        rule_version=1,
        written_by_agent_id="ingestion_agent",
        event_id=event.id,
    )
    if not activated.active:
        raise AssertionError("event should be active after activate_event")
    if activated.activation_seq != 1:
        raise AssertionError(f"first activation should set seq=1, got {activated.activation_seq}")
    if ingest_result is None:
        raise AssertionError("first activation should return an IngestionResult")

    # Second activate without intervening deactivate is a no-op for first-time-work
    # but still bumps seq + leaves active=True. ingest_result should be None.
    activated2, result2 = activate_event(
        store,
        schema=schema,
        llm=fake_llm,
        smart_model="gpt-stub",
        rule_version=1,
        written_by_agent_id="ingestion_agent",
        event_id=event.id,
    )
    if result2 is not None:
        raise AssertionError("second activation must not re-run extraction")
    if activated2.activation_seq != 2:
        raise AssertionError(
            f"second activation should bump seq to 2, got {activated2.activation_seq}"
        )

    deactivated = deactivate_event(store, event_id=event.id)
    if deactivated.active:
        raise AssertionError("event should be inactive after deactivate_event")
    # Rows persist with event_id set.
    with store.session() as session:
        rows = session.exec(select(EventRow).where(EventRow.id == event.id)).all()
    if not rows:
        raise AssertionError("EventRow should still exist after deactivate")
