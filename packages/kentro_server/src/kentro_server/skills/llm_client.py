"""LLMClient — the single seam through which every kentro-server LLM call goes.

Per `implementation-handoff.md` §1.4 ("LLM-call discipline"):
- Structured Pydantic output, always (via `instructor`).
- Validation retries (up to 3x) on parse failure.
- Determinism: temperature=0.
- No prompt-injection paths (user content stays in the user message slot).

Two concrete clients (`AnthropicLLMClient`, `GeminiLLMClient`) live in their own
modules; the factory in `skills/factory.py` selects per tier based on the configured
model name's prefix. The `CachingLLMClient` in `skills/cache.py` wraps either.

Tests use `OfflineLLMClient` as a deterministic stand-in. It is **never used in
production** — `make_llm_client` raises `LLMConfigError` if no usable backend is
configured.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from kentro_server.store.models import FieldWriteRow

logger = logging.getLogger(__name__)


class LLMConfigError(RuntimeError):
    """Raised at startup when the LLM configuration is incomplete or inconsistent."""


class LLMOfflineError(RuntimeError):
    """Raised when an offline-only stub is asked to do work that needs a real LLM."""


# === Structured outputs ===

class SkillResolverDecision(BaseModel):
    """Output of a SkillResolver LLM call."""

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
        default=None, ge=0.0, le=1.0,
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


# === Client protocol ===

class LLMClient(ABC):
    """Provider-agnostic structured-output LLM seam."""

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
        registered_schemas: "list",  # list[EntityTypeDef] — Any to avoid an SDK->server cycle
        document_label: str | None = None,
        model: str | None = None,
    ) -> ExtractionResult: ...


class OfflineLLMClient(LLMClient):
    """Test/CI stand-in. Never used in production — `make_llm_client` raises instead.

    `run_skill_resolver` returns UNRESOLVED with an explanatory reason (this is the
    same behavior Step 5 relied on, so existing tests keep working). `extract_entities`
    raises `LLMOfflineError` — extraction has no graceful-degradation path, callers
    should mock with a fake that returns canned data.
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

    def extract_entities(self, *, document_text, registered_schemas, document_label=None, model=None):
        raise LLMOfflineError(
            "OfflineLLMClient.extract_entities called — extraction requires a real LLM backend"
        )


__all__ = [
    "ExtractedEntity",
    "ExtractedField",
    "ExtractionResult",
    "LLMClient",
    "LLMConfigError",
    "LLMOfflineError",
    "OfflineLLMClient",
    "SkillResolverDecision",
]
