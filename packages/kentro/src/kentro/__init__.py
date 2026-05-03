"""Kentro SDK — thin client for kentro-server.

Re-exports the user-facing surface so the canonical example works:

    import kentro

    class Customer(kentro.Entity):
        name: str
        deal_size: float | None = None

    with kentro.Client(base_url=..., api_key=...) as client:
        record = client.read("Customer", "Acme")
"""

from kentro.client import (
    AdminRequiredError,
    AuthError,
    Client,
    KentroError,
    NotFoundError,
    SchemaEvolutionError,
    ServerError,
)
from kentro.resolvers import (
    AutoResolver,
    LatestWriteResolver,
    PreferAgent,
    RawResolver,
    Resolver,
    SkillResolver,
)
from kentro.rules import RuleSetDiff, render_rule, ruleset_diff
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
    "AdminRequiredError",
    "Agent",
    "AuthError",
    "AutoResolver",
    "Client",
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
    "KentroError",
    "LatestWriteResolver",
    "LineageRecord",
    "NLIntent",
    "NLResponse",
    "NotFoundError",
    "PreferAgent",
    "RawResolver",
    "ReevaluationReport",
    "Resolver",
    "Rule",
    "RuleSet",
    "RuleSetDiff",
    "SchemaEvolutionError",
    "ServerError",
    "SkillResolver",
    "WriteResult",
    "WriteRule",
    "WriteStatus",
    "__version__",
    "entity_type_def_from",
    "render_rule",
    "ruleset_diff",
]
