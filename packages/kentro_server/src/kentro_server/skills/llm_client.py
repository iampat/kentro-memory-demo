"""LLMClient — high-level skill API; the layer above `Provider`.

Per `CLAUDE.md` "Dependency injection & composition over inheritance":

    Provider               (low-level: complete(model, system, user, response_model))
       ↑
    CachingProvider        (middleware: fingerprints the rendered request)
       ↑
    DefaultLLMClient       (composition: loads SKILL.md, formats user, calls Provider)
       │
       └─ takes `fast_provider`, `smart_provider` via constructor (DI). Mixed-tier
          deployments pass two different providers. Single-tier pass the same
          provider twice.

Per `implementation-handoff.md` §1.4 ("LLM-call discipline"):
- Structured Pydantic output, always (via `instructor`, inside Provider).
- Validation retries (up to 3x) on parse failure (Provider's `max_retries`).
- Determinism: `temperature=0` (hard-coded inside each Provider).
- No prompt-injection paths (user content stays in the user message slot).

`OfflineLLMClient` is a separate ABC implementation that bypasses Providers
entirely — used in tests/CI where SkillResolver should gracefully return
UNRESOLVED instead of raising. Production wiring goes through `DefaultLLMClient`
backed by real Providers.
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from kentro_server.skills.skill_loader import load_skill_markdown

if TYPE_CHECKING:
    from kentro_server.skills.provider import Provider
    from kentro_server.store.models import FieldWriteRow

logger = logging.getLogger(__name__)


class LLMConfigError(RuntimeError):
    """Raised at startup when the LLM configuration is incomplete or inconsistent."""


class LLMOfflineError(RuntimeError):
    """Raised when an offline-only stub is asked to do work that needs a real LLM."""


# === Structured outputs ===


class SkillResolverDecision(BaseModel):
    """Output of a SkillResolver LLM call.

    TODO(workflow-aware-skills, planned for pre-Step-10): add an optional
    `actions: tuple[SkillAction, ...] = ()` field so a Skill can emit
    workflow steps alongside its winner pick — e.g.
        {type: "write_entity", entity_type: "Ticket", fields: {...}}
        {type: "notify",       channel: "#deals-review", message: "..."}
    The orchestrator in `core/resolve.py` will execute each action through
    the same ACL gate as a regular write (Skills cannot bypass governance).
    This is the "memory is the workflow trigger" demo beat — it's how the
    Scene 4 SkillResolver also creates Ticket #142 + fires the toast.
    Tracked in IMPLEMENTATION_PLAN.md "Deferred to the very end" and
    cross-referenced from demo.md / implementation-handoff.md / memory.md.
    Must land BEFORE Step 10 begins; UI's <TicketBadge> + <EscalationToast>
    components depend on this server-side surface.
    """

    model_config = ConfigDict(frozen=True)

    chosen_value_json: str | None = Field(
        description="The exact value_json of the candidate to use, or null if the skill cannot decide.",
    )
    reason: str = Field(
        description="When chosen_value_json is set: the reasoning. When null: why the skill cannot decide.",
    )


class ExtractedField(BaseModel):
    """One field extracted from a source document."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    value_json: str = Field(
        description=(
            "The extracted value, JSON-encoded. Use a JSON string for text/dates "
            '(e.g. "Acme"), JSON numbers for numerics (250000), JSON booleans, '
            "or JSON arrays/objects."
        ),
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional 0-1 confidence; omit when uncertain.",
    )


class ExtractedEntity(BaseModel):
    """One entity instance extracted from a source document."""

    model_config = ConfigDict(frozen=True)

    entity_type: str = Field(description="Must match a registered entity type name.")
    key: str = Field(description="Canonical key for strict-key resolution (e.g., 'Acme').")
    fields: tuple[ExtractedField, ...] = ()


class ExtractionResult(BaseModel):
    """Output of an extract_entities LLM call."""

    model_config = ConfigDict(frozen=True)

    entities: tuple[ExtractedEntity, ...] = ()
    notes: str | None = Field(
        default=None,
        description="Free-form notes from the extractor (parse failures, ambiguities).",
    )


# === NL → RuleSet structured outputs ===
#
# Multi-step parse: the user's plain-English message is first split into a list of
# atomic intents (`identify_nl_intents`), then each intent is compiled separately
# into a single Rule variant (`parse_nl_rule`). Both calls go through `instructor`,
# which requires a Pydantic *model* as the response schema — `instructor` cannot
# bind a bare list as the top-level type. Hence the `NLIntentList` wrapper.
#
# `ParsedRule.rule_json` is the JSON-serialized Rule (discriminated-union variant),
# or `None` if the LLM could not compile the intent into a valid rule. The
# orchestrator validates the JSON against `kentro.types.Rule` and routes failures
# into `NLResponse.notes` rather than discarding them.
#
# `NLIntentList.notes` carries any explanation the intent-splitter LLM emits for
# input it could not classify into the four kinds — surfaced in the final
# `NLResponse.notes` so the user sees *why* their phrasing was dropped, instead
# of having it silently swallowed.


class NLIntentItem(BaseModel):
    """One atomic intent, mirroring `kentro.types.NLIntent` on the wire side."""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(
        description=(
            "One of: field_read, entity_visibility, write_permission, conflict_resolver."
        ),
    )
    description: str = Field(description="The atomic intent in plain English.")


class NLIntentList(BaseModel):
    """Wrapper model so `instructor` can return a list of intents.

    The `notes` field matches the `nl_intents/SKILL.md` contract — when the
    splitter LLM cannot classify a piece of the user's message, it should emit
    a brief explanation here. The orchestrator merges this into `NLResponse.notes`.
    """

    model_config = ConfigDict(frozen=True)

    intents: tuple[NLIntentItem, ...] = ()
    notes: str | None = Field(
        default=None,
        description="Explanation of input fragments the splitter could not classify.",
    )


class ParsedRule(BaseModel):
    """Output of compiling a single NL intent into a Rule.

    `rule_json` is the JSON serialization of one `kentro.types.Rule` variant, or
    `None` when the intent could not be compiled. `reason` is always populated:
    on success it explains the choice; on failure it explains why the intent
    was skipped.
    """

    model_config = ConfigDict(frozen=True)

    rule_json: str | None = Field(
        default=None,
        description="JSON for a single Rule variant, or null when not compilable.",
    )
    reason: str = Field(description="Always present — explanation or skip-reason.")


# === High-level skill API ===


class LLMClient(ABC):
    """Skill-aware façade. Production impl is `DefaultLLMClient`."""

    @abstractmethod
    def run_skill_resolver(
        self,
        *,
        prompt: str,
        candidates: "list[FieldWriteRow]",
        model: str | None = None,
    ) -> SkillResolverDecision: ...

    @abstractmethod
    def extract_entities(
        self,
        *,
        document_text: str,
        registered_schemas: "list",  # list[EntityTypeDef]
        document_label: str | None = None,
        model: str | None = None,
    ) -> ExtractionResult: ...

    @abstractmethod
    def identify_nl_intents(
        self,
        *,
        text: str,
        model: str | None = None,
    ) -> NLIntentList: ...

    @abstractmethod
    def parse_nl_rule(
        self,
        *,
        intent_description: str,
        intent_kind: str,
        registered_schemas: "list",  # list[EntityTypeDef]
        known_agent_ids: tuple[str, ...],
        model: str | None = None,
    ) -> ParsedRule: ...


class DefaultLLMClient(LLMClient):
    """Compose two `Provider`s + the configured tier model names.

    `fast_provider` and `smart_provider` may be the same instance (single-tier
    deployment) or two different instances (mixed-tier). Either way, neither
    Provider knows about skills — this class loads `SKILL.md` text, formats
    the user payload, and hands the rendered request down.
    """

    def __init__(
        self,
        *,
        fast_provider: "Provider",
        smart_provider: "Provider",
        fast_model: str,
        smart_model: str,
    ) -> None:
        self.fast_provider = fast_provider
        self.smart_provider = smart_provider
        self.fast_model = fast_model
        self.smart_model = smart_model

    def run_skill_resolver(
        self,
        *,
        prompt: str,
        candidates: "list[FieldWriteRow]",
        model: str | None = None,
    ) -> SkillResolverDecision:
        return self.fast_provider.complete(
            model=model or self.fast_model,
            system=load_skill_markdown("skill_resolver"),
            user=_format_skill_user(prompt, candidates),
            response_model=SkillResolverDecision,
        )

    def extract_entities(
        self,
        *,
        document_text: str,
        registered_schemas: list,
        document_label: str | None = None,
        model: str | None = None,
    ) -> ExtractionResult:
        return self.smart_provider.complete(
            model=model or self.smart_model,
            system=load_skill_markdown("extract_entities"),
            user=_format_extract_user(document_text, registered_schemas, document_label),
            response_model=ExtractionResult,
        )

    def identify_nl_intents(
        self,
        *,
        text: str,
        model: str | None = None,
    ) -> NLIntentList:
        return self.fast_provider.complete(
            model=model or self.fast_model,
            system=load_skill_markdown("nl_intents"),
            user=f"USER MESSAGE:\n{text}",
            response_model=NLIntentList,
        )

    def parse_nl_rule(
        self,
        *,
        intent_description: str,
        intent_kind: str,
        registered_schemas: list,
        known_agent_ids: tuple[str, ...],
        model: str | None = None,
    ) -> ParsedRule:
        agents_block = ", ".join(known_agent_ids) if known_agent_ids else "(none)"
        user = (
            f"INTENT KIND: {intent_kind}\n"
            f"INTENT: {intent_description}\n\n"
            f"REGISTERED SCHEMA:\n{_render_schema_block(registered_schemas)}\n\n"
            f"KNOWN AGENT IDS: {agents_block}"
        )
        return self.fast_provider.complete(
            model=model or self.fast_model,
            system=load_skill_markdown("nl_to_rule"),
            user=user,
            response_model=ParsedRule,
        )


class OfflineLLMClient(LLMClient):
    """Test/CI stand-in. Never used in production — `make_llm_client` raises instead.

    `run_skill_resolver` returns UNRESOLVED with an explanatory reason (this is
    how Step 5 `resolve()` exercises the AutoResolver → SkillResolver dispatch
    path without a real LLM). The other three methods raise `LLMOfflineError` —
    they have no graceful-degradation path, so callers must mock with a fake
    that returns canned data.
    """

    _UNAVAILABLE_REASON = "LLM client offline (no backend configured)"

    def run_skill_resolver(self, *, prompt, candidates, model=None) -> SkillResolverDecision:
        logger.info(
            "OfflineLLMClient.run_skill_resolver — %d candidates → UNRESOLVED",
            len(candidates),
        )
        return SkillResolverDecision(
            chosen_value_json=None,
            reason=self._UNAVAILABLE_REASON,
        )

    def extract_entities(
        self, *, document_text, registered_schemas, document_label=None, model=None
    ) -> ExtractionResult:
        raise LLMOfflineError(
            "OfflineLLMClient.extract_entities called — extraction requires a real LLM backend"
        )

    def identify_nl_intents(self, *, text, model=None) -> NLIntentList:
        raise LLMOfflineError(
            "OfflineLLMClient.identify_nl_intents called — NL parsing requires a real LLM backend"
        )

    def parse_nl_rule(
        self, *, intent_description, intent_kind, registered_schemas, known_agent_ids, model=None
    ) -> ParsedRule:
        raise LLMOfflineError(
            "OfflineLLMClient.parse_nl_rule called — NL parsing requires a real LLM backend"
        )


# === Prompt formatters ===
#
# Provider-agnostic helpers — used by `DefaultLLMClient` only. Kept as
# module-level functions (not methods) so the same renderer is bit-for-bit
# identical regardless of which Provider serves the request, which keeps the
# cache key stable.


def _format_skill_user(policy: str, candidates: "list[FieldWriteRow]") -> str:
    rendered_candidates = []
    for c in candidates:
        rendered_candidates.append(
            {
                "agent_id": c.written_by_agent_id,
                "written_at": c.written_at.isoformat(),
                "source_document_id": str(c.source_document_id) if c.source_document_id else None,
                "value_json": c.value_json,
            }
        )
    return f"POLICY:\n{policy}\n\nCANDIDATES:\n{json.dumps(rendered_candidates, indent=2)}"


def _format_extract_user(
    document_text: str,
    registered_schemas: list,
    document_label: str | None,
) -> str:
    header = f"DOCUMENT LABEL: {document_label}\n" if document_label else ""
    return (
        f"REGISTERED SCHEMA:\n{_render_schema_block(registered_schemas)}\n\n"
        f"{header}DOCUMENT:\n{document_text}"
    )


def _render_schema_block(registered_schemas: list) -> str:
    """Pretty-print `EntityTypeDef`s into a block the LLM can match field names against."""
    chunks: list[str] = []
    for td in registered_schemas:
        chunks.append(f"- {td.name}:")
        for f in td.fields:
            if f.deprecated:
                # Don't even mention deprecated fields to the extractor.
                continue
            default = f" (default: {f.default_json})" if f.default_json else ""
            chunks.append(f"    * {f.name}: {f.type_str}{default}")
    return "\n".join(chunks)


__all__ = [
    "DefaultLLMClient",
    "ExtractedEntity",
    "ExtractedField",
    "ExtractionResult",
    "LLMClient",
    "LLMConfigError",
    "LLMOfflineError",
    "OfflineLLMClient",
    "SkillResolverDecision",
    "NLIntentItem",
    "NLIntentList",
    "ParsedRule",
]
