"""Server-side request DTOs for the HTTP routes.

Response types are imported from the SDK (`kentro.types`) directly — they are
the wire-form contract. Request bodies are server-only because the SDK has no
opinion about how the request is *encoded*; many requests are simple enough
they don't warrant being SDK-public.

Per CLAUDE.md, every endpoint takes a typed Pydantic body, never a raw `dict`.
"""

from typing import Any

from kentro.types import EntityTypeDef, ResolverSpec, RuleSet
from pydantic import BaseModel, ConfigDict, Field


class IngestRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    content: str = Field(description="UTF-8 markdown body of the source document.")
    label: str | None = Field(
        default=None,
        description="Human-readable label (filename, subject line, etc.).",
    )
    source_class: str | None = Field(
        default=None,
        description=(
            "Optional source-class hint persisted on the document row. "
            "Common values: 'verbal' (calls, transcripts), 'written' (emails, "
            "tickets), 'system' (machine-generated). Consumed by "
            "SkillResolvers and the demo UI."
        ),
    )
    smart_model: str | None = Field(
        default=None,
        description="Override the configured smart-tier model for this call.",
    )


class ReadRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    resolver: ResolverSpec = Field(
        description="The resolver to apply when resolving live writes per field."
    )


class WriteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value_json: str = Field(
        description=(
            "The value, JSON-encoded. Use a JSON string for text/dates "
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


class NLParseRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    text: str = Field(description="Plain-English message describing rule changes.")


class ApplyRulesetRequest(BaseModel):
    """Wrapper so the body is `{ruleset, summary}` rather than a bare RuleSet —
    leaves room for additional metadata (dry_run, comment) without a breaking change."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    ruleset: RuleSet
    summary: str | None = None


class ApplyRulesetResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: int
    rules_applied: int


class SchemaRegisterRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type_defs: list[EntityTypeDef]


class SchemaListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    type_defs: list[EntityTypeDef]


class RememberRequest(BaseModel):
    """POST /memory/remember — the Note shortcut.

    Convenience wrapper around `(subject, predicate, object_json)` so callers
    don't have to juggle the four-field Note shape. The route writes one Note
    entity with `key = subject` and the three fields populated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    subject: str = Field(description="What the note is about (becomes the entity key).")
    predicate: str = Field(description="The relationship/property being asserted.")
    object_json: Any = Field(
        description="The value of the predicate, JSON-serializable (will be json.dumps'd)."
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_label: str | None = Field(
        default=None,
        description="Free-form provenance label (URL, document name, MCP tool call id, etc.).",
    )


__all__ = [
    "ApplyRulesetRequest",
    "ApplyRulesetResponse",
    "IngestRequest",
    "NLParseRequest",
    "ReadRequest",
    "RememberRequest",
    "SchemaListResponse",
    "SchemaRegisterRequest",
    "WriteRequest",
]
