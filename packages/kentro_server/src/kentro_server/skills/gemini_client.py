"""Gemini-backed LLMClient — symmetric with the Anthropic client, also via `instructor`.

Currently a thin wrapper; not exercised by tests because no GOOGLE_API_KEY is
configured in the dev environment yet. The factory still selects this class when
the configured tier model name starts with `gemini-`, so routing is verifiable
even without a working call path.
"""

import json
import logging
from typing import TYPE_CHECKING, TypeVar

import instructor
from google import genai
from pydantic import BaseModel

from kentro_server.skills.llm_client import (
    ExtractionResult,
    LLMClient,
    SkillResolverDecision,
)
from kentro_server.skills.anthropic_client import _EXTRACT_SYSTEM, _SKILL_SYSTEM

if TYPE_CHECKING:
    from kentro_server.store.models import FieldWriteRow

logger = logging.getLogger(__name__)

_TModel = TypeVar("_TModel", bound=BaseModel)


class GeminiLLMClient(LLMClient):
    """Google-backed LLMClient.

    Mirrors `AnthropicLLMClient`; the extraction prompts are deliberately reused so
    the two providers extract on the same contract.
    """

    def __init__(
        self,
        *,
        api_key: str,
        fast_model: str,
        smart_model: str,
        max_retries: int = 3,
    ) -> None:
        self.fast_model = fast_model
        self.smart_model = smart_model
        self.max_retries = max_retries
        self._raw = genai.Client(api_key=api_key)
        self._client = instructor.from_genai(self._raw)

    def run_skill_resolver(
        self,
        *,
        prompt: str,
        candidates: "list[FieldWriteRow]",
        model: str | None = None,
    ) -> SkillResolverDecision:
        user = _format_skill_user(prompt, candidates)
        return self._complete(
            model=model or self.fast_model,
            system=_SKILL_SYSTEM,
            user=user,
            response_model=SkillResolverDecision,
        )

    def extract_entities(
        self,
        *,
        document_text: str,
        registered_entity_types: list[str],
        document_label: str | None = None,
        model: str | None = None,
    ) -> ExtractionResult:
        user = _format_extract_user(document_text, registered_entity_types, document_label)
        return self._complete(
            model=model or self.smart_model,
            system=_EXTRACT_SYSTEM,
            user=user,
            response_model=ExtractionResult,
        )

    def _complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_model: type[_TModel],
    ) -> _TModel:
        logger.debug("gemini.complete model=%s response_model=%s", model, response_model.__name__)
        return self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_model=response_model,
            max_retries=self.max_retries,
        )


def _format_skill_user(policy: str, candidates: "list[FieldWriteRow]") -> str:
    rendered_candidates = []
    for c in candidates:
        rendered_candidates.append({
            "agent_id": c.written_by_agent_id,
            "written_at": c.written_at.isoformat(),
            "source_document_id": str(c.source_document_id) if c.source_document_id else None,
            "value_json": c.value_json,
        })
    return f"POLICY:\n{policy}\n\nCANDIDATES:\n{json.dumps(rendered_candidates, indent=2)}"


def _format_extract_user(
    document_text: str,
    registered_entity_types: list[str],
    document_label: str | None,
) -> str:
    header = f"DOCUMENT LABEL: {document_label}\n" if document_label else ""
    return (
        f"REGISTERED ENTITY TYPES: {', '.join(registered_entity_types)}\n\n"
        f"{header}DOCUMENT:\n{document_text}"
    )


__all__ = ["GeminiLLMClient"]
