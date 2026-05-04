"""SQLModel tables for kentro-server state.

One SQLite database per tenant lives at `kentro_state/<tenant_id>/state.sqlite`.
Tenants themselves are configuration, not a DB row, so there is no `tenant` table.

Conventions:
- UUID primary keys for content-bearing rows; string IDs for human-meaningful keys
  (agent_id, entity type, etc.).
- All timestamps are UTC-aware (`datetime.now(timezone.utc)`).
- All field-write rows persist; conflict resolution is computed at read time
  against the live row set. `superseded=True` marks a row that lost a resolution
  but is kept for lineage and possible re-resolution after source churn.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _now_utc() -> datetime:
    return datetime.now(UTC)


class AgentRow(SQLModel, table=True):
    """An agent registered with this tenant (Sales, Customer Service, ingestion_agent, ...)."""

    __tablename__ = "agent"

    id: str = Field(primary_key=True)
    display_name: str | None = None
    created_at: datetime = Field(default_factory=_now_utc)


class DocumentRow(SQLModel, table=True):
    """A source document (markdown blob lives in the tenant's blob store).

    `source_class` is a v0 hint used by the demo UI and by SkillResolvers that
    distinguish "verbal" sources (calls, transcripts) from "written" sources
    (emails, tickets). Free-form string for v0 — typical values are `"verbal"`,
    `"written"`, `"system"`. Optional and nullable for backward compatibility
    with documents ingested before this column existed.

    `event_id` ties this document to a catalog `EventRow`. NULL = "always-live"
    (e.g. an admin-direct ingest that bypasses the catalog). When set, the
    document and everything derived from it is filtered out of reads when the
    owning event is inactive — see `kentro_server.core.read`.
    """

    __tablename__ = "document"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    blob_key: str
    content_hash: str = Field(index=True)
    label: str | None = None
    source_class: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_now_utc)
    event_id: UUID | None = Field(default=None, foreign_key="event.id", index=True)


class EntityRow(SQLModel, table=True):
    """An entity instance — uniquely identified by (type, key) per strict-key resolution."""

    __tablename__ = "entity"
    __table_args__ = (UniqueConstraint("type", "key", name="uq_entity_type_key"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    type: str = Field(index=True)
    key: str = Field(index=True)
    created_at: datetime = Field(default_factory=_now_utc)


class FieldWriteRow(SQLModel, table=True):
    """A single raw write event for one field on one entity.

    Multiple `FieldWriteRow`s for the same (entity, field_name) constitute a conflict.
    Conflicts are resolved at read time, never at write time. `superseded` marks rows
    that lost a previous resolution; they remain in the table so resolution can fall
    back to surviving evidence after source churn.
    """

    __tablename__ = "field_write"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    entity_id: UUID = Field(foreign_key="entity.id", index=True)
    field_name: str = Field(index=True)
    value_json: str
    confidence: float | None = None
    written_by_agent_id: str = Field(foreign_key="agent.id")
    written_at: datetime = Field(default_factory=_now_utc, index=True)
    source_document_id: UUID | None = Field(default=None, foreign_key="document.id", index=True)
    rule_version_at_write: int
    extraction_step_id: UUID | None = Field(default=None, foreign_key="extraction_step.id")
    superseded: bool = Field(default=False, index=True)
    event_id: UUID | None = Field(default=None, foreign_key="event.id", index=True)


class ConflictRow(SQLModel, table=True):
    """A recorded conflict for (entity, field_name).

    Created when a 2nd `FieldWriteRow` lands for an (entity, field) where another live
    write already exists. `resolved_at` is set when a resolver picks a winner; cleared
    again if source churn invalidates the resolution.
    """

    __tablename__ = "conflict"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    entity_id: UUID = Field(foreign_key="entity.id", index=True)
    field_name: str
    detected_at: datetime = Field(default_factory=_now_utc)
    resolved_at: datetime | None = None
    resolution_winner_write_id: UUID | None = Field(default=None, foreign_key="field_write.id")
    resolver_used: str | None = None  # discriminator from ResolverSpec.type
    event_id: UUID | None = Field(default=None, foreign_key="event.id", index=True)


class RuleVersionRow(SQLModel, table=True):
    """A point-in-time snapshot of the rule set. Bumped atomically on `admin.rules.apply`."""

    __tablename__ = "rule_version"

    version: int = Field(primary_key=True)
    applied_at: datetime = Field(default_factory=_now_utc)
    summary: str | None = None


class RuleRow(SQLModel, table=True):
    """One rule belonging to one rule version. Stored as JSON of a `Rule` discriminated-union variant."""

    __tablename__ = "rule_record"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    rule_version: int = Field(foreign_key="rule_version.version", index=True)
    rule_type: str = Field(index=True)
    payload_json: str


class SchemaTypeRow(SQLModel, table=True):
    """One registered entity type with its declared field shape, per tenant.

    The server stores the SDK-provided `EntityTypeDef` (name + list of `FieldDef`s)
    serialized as JSON. The ingestor's `registered_entity_types` list is derived from
    this table, and future field-shape validation can read `definition_json` here.
    """

    __tablename__ = "schema_type"
    __table_args__ = (UniqueConstraint("name", name="uq_schema_type_name"),)

    name: str = Field(primary_key=True)
    definition_json: str
    registered_at: datetime = Field(default_factory=_now_utc)


class SkillActionExecutionRow(SQLModel, table=True):
    """One executed SkillResolver action — exists to dedupe replays across reads.

    Codex 2026-05-03 high finding #1: `read_entity()` runs `resolved.actions`
    immediately after resolution. Without persistence, any retried request,
    client refresh, or simple repeat read could re-execute the same
    `WriteEntityAction` / `NotifyAction` — making reads state-changing and
    re-entrant.

    Dedupe model:
      - `scope_key` is a stable identifier for the resolver decision the
        action came from. When a conflict row exists, scope_key is
        `"conflict:<uuid>"`; otherwise (single-candidate corroboration),
        scope_key is `"write:<uuid>"` of the winning field write.
      - `action_fingerprint` is a SHA-256 hex digest over the action's
        normalized payload (type + entity refs + field + value, or
        type + channel + message). Stable across retries.
      - `UNIQUE(scope_key, action_fingerprint)` blocks re-execution at the
        DB level. The read path checks first (cheap) and then catches
        `IntegrityError` as the race-condition safety net.
    """

    __tablename__ = "skill_action_execution"
    __table_args__ = (
        UniqueConstraint(
            "scope_key", "action_fingerprint", name="uq_skill_action_scope_fingerprint"
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    scope_key: str = Field(index=True)
    action_fingerprint: str = Field(index=True)
    executed_at: datetime = Field(default_factory=_now_utc)
    executed_by_agent_id: str = Field(foreign_key="agent.id")


class ExtractionStepRow(SQLModel, table=True):
    """Telemetry for a single LLM call made during ingestion."""

    __tablename__ = "extraction_step"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    model: str
    input_excerpt: str
    output_summary: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    created_at: datetime = Field(default_factory=_now_utc)
    event_id: UUID | None = Field(default=None, foreign_key="event.id", index=True)


class EventRow(SQLModel, table=True):
    """A toggleable demo event (currently: ingestion only).

    The catalog model: an event represents a discrete world-changing action
    the demo viewer can toggle on or off. Today the only kind is
    `ingest_document` — payload carries the doc text + label so first
    activation can run extraction lazily and re-activation is a flag flip
    against the rows already created.

    Ordering:
      - `catalog_order` is the demo author's intended position in the catalog
        UI. Stable across the lifetime of the row.
      - `activation_seq` is bumped on every activation (each toggle-on assigns
        `MAX(activation_seq) + 1`). NULL means never activated. Drives both
        the event-list display order AND the resolver's tie-break for
        LatestWrite-style policies — re-activating a previously-active event
        moves it to the top of the stack and can flip conflict outcomes.

    Why payload is JSON: keeps the catalog generic. A future `apply_rule`
    or `delete_document` event kind would store its own shape in the same
    column rather than growing per-kind tables.
    """

    __tablename__ = "event"
    __table_args__ = (UniqueConstraint("catalog_key", name="uq_event_catalog_key"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    catalog_key: str = Field(index=True)
    title: str
    description: str | None = None
    kind: str  # "ingest_document" — discriminator for payload shape
    payload_json: str  # JSON-serialized payload; shape depends on `kind`
    catalog_order: int = Field(index=True)
    activation_seq: int | None = Field(default=None, index=True)
    active: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_now_utc)


__all__ = [
    "AgentRow",
    "ConflictRow",
    "DocumentRow",
    "EntityRow",
    "EventRow",
    "ExtractionStepRow",
    "FieldWriteRow",
    "RuleRow",
    "RuleVersionRow",
    "SchemaTypeRow",
    "SkillActionExecutionRow",
]
