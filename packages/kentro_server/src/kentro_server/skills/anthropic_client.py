"""Anthropic-backed LLMClient — uses `instructor` for typed Pydantic output.

Both `run_skill_resolver` (fast tier) and `extract_entities` (smart tier) build
system+user messages and round-trip through the same internal `_complete` helper.
Per the handoff §1.4: structured output, temperature=0, retries on parse failure,
user content stays in the user message slot (no prompt injection).
"""

import json
import logging
from typing import TYPE_CHECKING, TypeVar

import anthropic
import instructor
from pydantic import BaseModel

from kentro_server.skills.llm_client import (
    ExtractionResult,
    LLMClient,
    SkillResolverDecision,
)
from kentro_server.skills.skill_loader import load_skill_markdown

if TYPE_CHECKING:
    from kentro_server.store.models import FieldWriteRow

logger = logging.getLogger(__name__)

_TModel = TypeVar("_TModel", bound=BaseModel)


class AnthropicLLMClient(LLMClient):
    """Real Anthropic-backed LLMClient.

    `fast_model` and `smart_model` are model IDs without dated suffixes
    (e.g., "claude-haiku-4-5", "claude-sonnet-4-6"). Calls supply `temperature=0` and
    rely on `instructor` for structured Pydantic output with parse-failure retries.
    """

    def __init__(
        self,
        *,
        api_key: str,
        fast_model: str,
        smart_model: str,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> None:
        self.fast_model = fast_model
        self.smart_model = smart_model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self._raw = anthropic.Anthropic(api_key=api_key)
        self._client = instructor.from_anthropic(self._raw)

    # --- High-level skills ---

    def run_skill_resolver(
        self,
        *,
        prompt: str,
        candidates: "list[FieldWriteRow]",
        model: str | None = None,
    ) -> SkillResolverDecision:
        user = self._format_skill_user(prompt, candidates)
        return self._complete(
            model=model or self.fast_model,
            system=load_skill_markdown("skill_resolver"),
            user=user,
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
        user = self._format_extract_user(document_text, registered_schemas, document_label)
        return self._complete(
            model=model or self.smart_model,
            system=load_skill_markdown("extract_entities"),
            user=user,
            response_model=ExtractionResult,
        )

    # --- Internals ---

    def _complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_model: type[_TModel],
    ) -> _TModel:
        logger.debug(
            "anthropic.complete model=%s response_model=%s", model, response_model.__name__
        )
        return self._client.messages.create(
            model=model,
            temperature=0,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            system=system,
            messages=[{"role": "user", "content": user}],
            response_model=response_model,
        )

    @staticmethod
    def _format_skill_user(policy: str, candidates: "list[FieldWriteRow]") -> str:
        rendered_candidates = []
        for c in candidates:
            rendered_candidates.append(
                {
                    "agent_id": c.written_by_agent_id,
                    "written_at": c.written_at.isoformat(),
                    "source_document_id": str(c.source_document_id)
                    if c.source_document_id
                    else None,
                    "value_json": c.value_json,
                }
            )
        return f"POLICY:\n{policy}\n\nCANDIDATES:\n{json.dumps(rendered_candidates, indent=2)}"

    @staticmethod
    def _format_extract_user(
        document_text: str,
        registered_schemas: list,
        document_label: str | None,
    ) -> str:
        schema_block = _render_schema_block(registered_schemas)
        header = f"DOCUMENT LABEL: {document_label}\n" if document_label else ""
        return f"REGISTERED SCHEMA:\n{schema_block}\n\n{header}DOCUMENT:\n{document_text}"


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


__all__ = ["AnthropicLLMClient"]
