"""Kentro SDK Pydantic v2 types — every public DTO crossing the API boundary.

Mirrored verbatim in `packages/kentro_server/src/kentro_server/api/types.py`.
Parity is enforced by `tests/unit/types_parity_test.py`. Drift is intentional only when
the test is updated in the same change.
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


class PreferAgentResolverSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["prefer_agent"] = "prefer_agent"
    agent_id: str


class SkillResolverSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["skill"] = "skill"
    prompt: str
    model: str | None = None


class AutoResolverSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["auto"] = "auto"


ResolverSpec = Annotated[
    RawResolverSpec
    | LatestWriteResolverSpec
    | PreferAgentResolverSpec
    | SkillResolverSpec
    | AutoResolverSpec,
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
    field_name: str | None = None
    allowed: bool
    requires_approval: bool = False


class ConflictRule(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["conflict"] = "conflict"
    entity_type: str
    field_name: str
    resolver: ResolverSpec


Rule = Annotated[
    FieldReadRule | EntityVisibilityRule | WriteRule | ConflictRule,
    Field(discriminator="type"),
]


class RuleSet(BaseModel):
    model_config = ConfigDict(frozen=True)
    rules: tuple[Rule, ...] = ()
    version: int = 0


# === Lineage ===


class LineageRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_document_id: UUID | None = None
    written_at: datetime
    written_by_agent_id: str
    rule_version: int
    extraction_step_id: UUID | None = None


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


class NLResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    parsed_ruleset: RuleSet
    summary: str | None = None


# === Schema registration (sent by admin.schema.register) ===


class FieldDef(BaseModel):
    """One declared field on an `Entity` subclass.

    `type_str` is the Python annotation as a string (e.g. `"str"`, `"float | None"`,
    `"list[str]"`). v0 stores it for documentation + future validation; the server does
    not currently validate extracted values against the type.
    """

    model_config = ConfigDict(frozen=True)
    name: str
    type_str: str
    required: bool = True
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


__all__ = [
    "Agent",
    "AutoResolverSpec",
    "Conflict",
    "ConflictRule",
    "Entity",
    "EntityRecord",
    "EntityTypeDef",
    "EntityVisibilityRule",
    "ExtractionStep",
    "FieldDef",
    "FieldReadRule",
    "FieldStatus",
    "FieldValue",
    "FieldValueCandidate",
    "IngestionResult",
    "LatestWriteResolverSpec",
    "LineageRecord",
    "NLResponse",
    "PreferAgentResolverSpec",
    "ReevaluationReport",
    "ResolverSpec",
    "RawResolverSpec",
    "Rule",
    "RuleSet",
    "SkillResolverSpec",
    "WriteResult",
    "WriteRule",
    "WriteStatus",
]
