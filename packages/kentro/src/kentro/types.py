"""Kentro SDK Pydantic v2 types — every public DTO crossing the API boundary.

This module is the single source of truth for the wire format. `kentro_server`
depends on the `kentro` SDK package and imports these types directly. (Earlier
iterations duplicated the types into the server with a parity test; that was
retired in Step 7.)
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# === Status enums ===


class FieldStatus(StrEnum):
    """Status of a field read. SDK consumers MUST handle all four."""

    KNOWN = "known"
    UNKNOWN = "unknown"
    HIDDEN = "hidden"
    UNRESOLVED = "unresolved"


class WriteStatus(StrEnum):
    """Outcome of a write attempt (`AgentClient.write` and `write_natural`)."""

    APPLIED = "applied"
    CONFLICT_RECORDED = "conflict_recorded"
    PERMISSION_DENIED = "permission_denied"
    NO_ACTIONABLE_CONTENT = "no_actionable_content"
    AMBIGUOUS = "ambiguous"


# === Resolver specs (declarative — actual resolver behavior lives in resolvers.py) ===


class RawResolverSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["raw"] = "raw"


class LatestWriteResolverSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["latest_write"] = "latest_write"


class SkillResolverSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["skill"] = "skill"
    prompt: str
    model: str | None = None
    # When False (default), the LLM must return one of the existing candidates'
    # value_json byte-for-byte — the "winner" is one of the actual rows and
    # full lineage to that row is preserved. When True, the LLM may produce a
    # new value derived from the candidates (e.g. a summary, a normalised
    # form, an aggregate); there is no winner row, and lineage attributes the
    # synthesised value to ALL contributing candidates.
    synthesize: bool = False


class AutoResolverSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["auto"] = "auto"


ResolverSpec = Annotated[
    RawResolverSpec | LatestWriteResolverSpec | SkillResolverSpec | AutoResolverSpec,
    Field(discriminator="type"),
]


# === Rules (discriminated union — narrow with isinstance) ===


class FieldReadRule(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["field_read"] = "field_read"
    agent_id: str
    entity_type: str
    field_name: str
    allowed: bool


class EntityVisibilityRule(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["entity_visibility"] = "entity_visibility"
    agent_id: str
    entity_type: str
    entity_key: str | None = None
    allowed: bool


class WriteRule(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["write"] = "write"
    agent_id: str
    entity_type: str
    field_name: str
    allowed: bool
    requires_approval: bool = False


# `ConflictRule` was retired in PR 35: conflict resolution is governed by
# `ResolverPolicy` (a sibling shape, not a Rule variant). The Rule union now
# only carries true ACL rules: who can read, who can see, who can write.
Rule = Annotated[
    FieldReadRule | EntityVisibilityRule | WriteRule,
    Field(discriminator="type"),
]


class RuleSet(BaseModel):
    model_config = ConfigDict(frozen=True)
    rules: tuple[Rule, ...] = ()
    version: int = 0


# === Resolvers (separate from RuleSet — different shape, different question) ===
#
# A `ResolverPolicy` answers "when two writes for `entity_type.field_name`
# collide, which one wins?" Stored independently from the ACL ruleset so
# the two concerns can be reasoned about separately. Edited from the
# LineageDrawer's resolver chip in the UI.


class ResolverPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    entity_type: str
    field_name: str
    resolver: ResolverSpec


class ResolverPolicySet(BaseModel):
    model_config = ConfigDict(frozen=True)
    policies: tuple[ResolverPolicy, ...] = ()
    version: int = 0


# === Lineage ===


class LineageRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_document_id: UUID | None = None
    written_at: datetime
    written_by_agent_id: str
    rule_version: int
    extraction_step_id: UUID | None = None
    # Decoded value the SOURCE wrote at this lineage point. Multiple sources
    # may corroborate the same resolved value — but each one wrote its own
    # candidate value, and a faithful lineage must surface that. Optional so
    # historical records that pre-date this field stay readable.
    value: Any | None = None


# === Field value carriers (read-time) ===


class FieldValueCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: Any
    confidence: float | None = None
    lineage: tuple[LineageRecord, ...] = ()


class FieldValue(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: FieldStatus
    value: Any | None = None
    confidence: float | None = None
    lineage: tuple[LineageRecord, ...] = ()
    candidates: tuple[FieldValueCandidate, ...] = ()
    reason: str | None = None


class Conflict(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    entity_type: str
    entity_key: str
    field_name: str
    candidates: tuple[FieldValueCandidate, ...]
    detected_at: datetime


# === Records ===


class EntityRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    entity_type: str
    key: str
    fields: dict[str, FieldValue] = {}


class Agent(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    display_name: str | None = None


# === Operation results ===


class WriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: WriteStatus
    entity_type: str
    entity_key: str
    field_name: str | None = None
    conflict_id: UUID | None = None
    reason: str | None = None


class ExtractionStep(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    name: str
    model: str
    input_excerpt: str
    output_summary: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class IngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_document_id: UUID
    entities: tuple[EntityRecord, ...] = ()
    extraction_steps: tuple[ExtractionStep, ...] = ()


class ReevaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_document_id: UUID
    affected_fields: tuple[tuple[str, str, str], ...] = ()
    re_resolutions: int = 0


class NLIntent(BaseModel):
    """One atomic rule-change intent identified from a user's plain-English message.

    The first step of NL-to-RuleSet parsing splits the user message into a list of
    these; the second step compiles each one into a `Rule` variant.
    """

    model_config = ConfigDict(frozen=True)
    kind: Literal["field_read", "entity_visibility", "write_permission"]
    description: str


class NLResponse(BaseModel):
    """Structured result of NL → RuleSet parsing.

    Produced by the `parse_nl_to_ruleset(...)` orchestrator in `kentro_server`.
    The HTTP route that surfaces it (planned: `POST /rules/parse`) lands in
    Phase D; until then this type is the *contract* between the in-process
    parser and its callers (CLI, demo notebooks, the future route handler).

    Multi-step parsing produces this shape:
      - `parsed_ruleset` carries the rules that compiled successfully.
      - `intents` is the full list the LLM identified (including those that did
        not compile).
      - `notes` is a free-text summary of skipped intents and parse difficulties.
    """

    model_config = ConfigDict(frozen=True)
    parsed_ruleset: RuleSet
    intents: tuple[NLIntent, ...] = ()
    notes: str | None = None
    summary: str | None = None


# === Schema registration (sent by admin.schema.register) ===


class FieldDef(BaseModel):
    """One declared field on an `Entity` subclass.

    `type_str` is the Python annotation as a string (e.g. `"str"`, `"float | None"`,
    `"list[str]"`). v0 stores it for documentation + future validation; the server
    does not currently validate extracted values against the type.

    Per the schema-evolution rules (Step 7), every field is optional — `required` is
    not modeled. An entity can exist as a bare `EntityRow` with zero known fields;
    reads return `FieldValue(status=UNKNOWN)` for fields nobody has written yet.

    `deprecated=True` marks a field that will no longer accept new writes and is
    excluded from the extractor prompt. Existing writes for the field stay readable.
    """

    model_config = ConfigDict(frozen=True)
    name: str
    type_str: str
    deprecated: bool = False
    default_json: str | None = None


class EntityTypeDef(BaseModel):
    """Wire-form description of a registered entity type."""

    model_config = ConfigDict(frozen=True)
    name: str
    fields: tuple[FieldDef, ...] = ()


# === User-facing entity schema base ===


class Entity(BaseModel):
    """Base class for user-declared entity schemas.

    Subclassed in user code:

        class Customer(kentro.Entity):
            name: str
            deal_size: float | None = None
    """

    model_config = ConfigDict(frozen=False)


# === List-endpoint response shapes (added 2026-05-03 for the demo UI) ===
#
# These are the wire-form responses for `GET /entities/{type}` and
# `GET /documents` — added so the prototype UI can render its left-pane
# entity/document lists from real server data without hitting per-key
# read endpoints in a loop.


class EntitySummary(BaseModel):
    """One row in `GET /entities/{type}` — `(type, key, created_at, field_count)`.

    `field_count` is the count of distinct (live) field writes on this entity;
    used by the demo UI to show "[5 fields]" badges without having to read each
    entity. Filtered by ACL: an entity hidden from the caller via
    `EntityVisibilityRule` doesn't appear in the list.
    """

    model_config = ConfigDict(frozen=True)
    type: str
    key: str
    field_count: int = 0


class EntityListResponse(BaseModel):
    """Response shape for `GET /entities/{type}`."""

    model_config = ConfigDict(frozen=True)
    entity_type: str
    entities: tuple[EntitySummary, ...] = ()


class DocumentSummary(BaseModel):
    """One row in `GET /documents` — surfacing label, source_class, ingest time."""

    model_config = ConfigDict(frozen=True)
    id: str  # UUID as string for JSON friendliness
    label: str | None = None
    source_class: str | None = None
    content_hash: str
    created_at: str  # ISO8601 datetime
    blob_key: str
    field_write_count: int = 0


class DocumentListResponse(BaseModel):
    """Response shape for `GET /documents`."""

    model_config = ConfigDict(frozen=True)
    documents: tuple[DocumentSummary, ...] = ()


class DocumentContentResponse(BaseModel):
    """Response shape for `GET /documents/{id}/content`.

    Carries the raw markdown (or other text) blob for a single source document
    so the demo UI can show "what was actually extracted from". Tenant-scoped
    via the bearer; not ACL-filtered (the source text is the input to
    extraction, not a derived field value).
    """

    model_config = ConfigDict(frozen=True)
    id: str
    label: str | None = None
    source_class: str | None = None
    content: str


# === Extraction-step view (added 2026-05-03 for the demo UI's ingestion panel) ===
#
# The demo UI's bottom-left "Ingestion pipeline" panel renders a per-document
# trace of every LLM call that produced its lineage. A document can have one
# extraction step (current ingestor.py shape) or multiple (future multi-pass
# extraction). Each step is keyed back to the document via the FieldWriteRow
# bridge: rows whose `source_document_id == doc.id` carry their step id.


class ExtractionStepView(BaseModel):
    """One LLM extraction step that contributed at least one field write to a
    given source document.

    Fields beyond the existing `ExtractionStep` SDK type:
    - `id` — UUID as string (JSON friendliness).
    - `created_at` — ISO8601 datetime string.
    - `produced_writes` — count of distinct (entity_type, entity_key, field_name)
      tuples this step wrote, surfaced so the UI can show "extracted N facts"
      without per-field follow-up queries.

    `tokens_in` / `tokens_out` are 0 in the current ingestor (the LLM client's
    structured-output path doesn't surface token counts yet); kept in the wire
    shape for forward-compat with the eventual instrumented path.
    """

    model_config = ConfigDict(frozen=True)
    id: str
    document_id: str
    name: str
    model: str
    input_excerpt: str
    output_summary: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    created_at: str
    produced_writes: int = 0


class ExtractionStepListResponse(BaseModel):
    """Response shape for `GET /documents/{document_id}/extraction-steps`."""

    model_config = ConfigDict(frozen=True)
    document_id: str
    steps: tuple[ExtractionStepView, ...] = ()


# === Viz endpoints (added 2026-05-03 for the demo UI's right column) ===
#
# `GET /viz/access-matrix?entity_type=X` and `GET /viz/graph` both return JSON
# shaped to drive a specific UI panel. Both are tenant-scoped via the bearer.


class AccessMatrixCellView(BaseModel):
    """One cell of `GET /viz/access-matrix` — flat shape for the table renderer."""

    model_config = ConfigDict(frozen=True)
    agent_id: str
    entity_type: str
    field_name: str
    read: bool
    write: bool
    visible: bool


class AccessMatrixView(BaseModel):
    """Response shape for `GET /viz/access-matrix?entity_type=X`.

    `agents` and `fields` are the row/column labels the renderer uses; `cells`
    is a flat list keyed on (agent_id, entity_type, field_name) — easier for
    the JS consumer to lookup than a nested matrix.
    """

    model_config = ConfigDict(frozen=True)
    entity_type: str
    fields: tuple[str, ...]
    agents: tuple[str, ...]
    cells: tuple[AccessMatrixCellView, ...]


class GraphNode(BaseModel):
    """One node in `GET /viz/graph` — either a document or an entity."""

    model_config = ConfigDict(frozen=True)
    id: str
    kind: Literal["document", "entity"]
    label: str
    sub: str | None = None


class GraphEdge(BaseModel):
    """One edge in `GET /viz/graph` — directed `document → entity` per FieldWriteRow."""

    model_config = ConfigDict(frozen=True)
    source: str
    target: str
    field_name: str
    agent_id: str


class GraphView(BaseModel):
    """Response shape for `GET /viz/graph` — entity-document bipartite graph."""

    model_config = ConfigDict(frozen=True)
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()


__all__ = [
    "AccessMatrixCellView",
    "AccessMatrixView",
    "Agent",
    "AutoResolverSpec",
    "Conflict",
    "DocumentContentResponse",
    "DocumentListResponse",
    "DocumentSummary",
    "Entity",
    "EntityListResponse",
    "EntityRecord",
    "EntitySummary",
    "EntityTypeDef",
    "EntityVisibilityRule",
    "ExtractionStep",
    "ExtractionStepListResponse",
    "ExtractionStepView",
    "FieldDef",
    "FieldReadRule",
    "FieldStatus",
    "FieldValue",
    "FieldValueCandidate",
    "GraphEdge",
    "GraphNode",
    "GraphView",
    "IngestionResult",
    "LatestWriteResolverSpec",
    "LineageRecord",
    "NLIntent",
    "NLResponse",
    "ReevaluationReport",
    "ResolverPolicy",
    "ResolverPolicySet",
    "ResolverSpec",
    "RawResolverSpec",
    "Rule",
    "RuleSet",
    "SkillResolverSpec",
    "WriteResult",
    "WriteRule",
    "WriteStatus",
]
