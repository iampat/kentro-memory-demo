"""Kentro SDK — thin client for kentro-server.

Re-exports the user-facing surface so the canonical example works:

    import kentro

    class Customer(kentro.Entity):
        name: str
        deal_size: float | None = None

    with kentro.Client(base_url=..., api_key=...) as client:
        record = client.read("Customer", "Acme")
"""

from kentro.acl import (
    AclDecision,
    evaluate_entity_visibility,
    evaluate_field_read,
    evaluate_write,
)
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
from kentro.rules import (
    RuleSetDiff,
    render_rule,
    render_rule_as_rego,
    render_rule_as_rego_body,
    rule_package_for,
    ruleset_diff,
)
from kentro.schema import entity_type_def_from
from kentro.types import (
    Agent,
    Conflict,
    DocumentListResponse,
    DocumentSummary,
    Entity,
    EntityListResponse,
    EntityRecord,
    EntitySummary,
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
    ResolverPolicy,
    ResolverPolicySet,
    Rule,
    RuleSet,
    WriteResult,
    WriteRule,
    WriteStatus,
)
from kentro.viz import (
    AccessMatrix,
    AccessMatrixCell,
    ConflictsView,
    LineageView,
    RuleDiffView,
    access_matrix,
    conflicts_from_records,
    lineage,
    rule_diff,
)

__version__ = "0.0.0"

__all__ = [
    "AccessMatrix",
    "AccessMatrixCell",
    "AclDecision",
    "AdminRequiredError",
    "Agent",
    "AuthError",
    "AutoResolver",
    "Client",
    "Conflict",
    "ConflictsView",
    "Entity",
    "DocumentListResponse",
    "DocumentSummary",
    "EntityListResponse",
    "EntityRecord",
    "EntitySummary",
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
    "LineageView",
    "NLIntent",
    "NLResponse",
    "NotFoundError",
    "PreferAgent",
    "RawResolver",
    "ReevaluationReport",
    "Resolver",
    "ResolverPolicy",
    "ResolverPolicySet",
    "Rule",
    "RuleDiffView",
    "RuleSet",
    "RuleSetDiff",
    "SchemaEvolutionError",
    "ServerError",
    "SkillResolver",
    "WriteResult",
    "WriteRule",
    "WriteStatus",
    "__version__",
    "access_matrix",
    "conflicts_from_records",
    "entity_type_def_from",
    "evaluate_entity_visibility",
    "evaluate_field_read",
    "evaluate_write",
    "lineage",
    "render_rule",
    "render_rule_as_rego",
    "render_rule_as_rego_body",
    "rule_package_for",
    "rule_diff",
    "ruleset_diff",
]
