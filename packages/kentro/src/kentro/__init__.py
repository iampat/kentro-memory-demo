"""Kentro SDK — thin client for kentro-server.

Re-exports the user-facing surface so the canonical example works:

    import kentro

    class Customer(kentro.Entity):
        name: str
        deal_size: float | None = None
"""

from kentro.resolvers import (
    AutoResolver,
    LatestWriteResolver,
    PreferAgent,
    RawResolver,
    Resolver,
    SkillResolver,
)
from kentro.schema import entity_type_def_from
from kentro.types import (
    Agent,
    Conflict,
    ConflictRule,
    Entity,
    EntityRecord,
    EntityTypeDef,
    EntityVisibilityRule,
    ExtractionStep,
    FieldDef,
    FieldReadRule,
    FieldStatus,
    FieldValue,
    FieldValueCandidate,
    IngestionResult,
    LineageRecord,
    NLIntent,
    NLResponse,
    ReevaluationReport,
    Rule,
    RuleSet,
    WriteResult,
    WriteRule,
    WriteStatus,
)

__version__ = "0.0.0"

__all__ = [
    "Agent",
    "AutoResolver",
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
    "LatestWriteResolver",
    "LineageRecord",
    "NLIntent",
    "NLResponse",
    "PreferAgent",
    "RawResolver",
    "ReevaluationReport",
    "Resolver",
    "Rule",
    "RuleSet",
    "SkillResolver",
    "WriteResult",
    "WriteRule",
    "WriteStatus",
    "__version__",
    "entity_type_def_from",
]
