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

if TYPE_CHECKING:
    from kentro_server.store.models import FieldWriteRow

logger = logging.getLogger(__name__)

_TModel = TypeVar("_TModel", bound=BaseModel)

_SKILL_SYSTEM = """\
You are a conflict-resolution skill for a memory system.

You will be given:
- A POLICY describing how to choose among candidate values.
- A list of CANDIDATE writes for one field, each with its source agent, written-at \
timestamp, source document id, and value (JSON-encoded).

Your job: pick exactly one candidate's value_json verbatim, or signal that you cannot decide.

Rules:
- Return the chosen candidate's value_json EXACTLY (byte-for-byte). Do not paraphrase \
or normalize it.
- If the policy does not produce a unique winner — including the case where you simply \
cannot tell — return chosen_value_json=null and explain why in `reason`.
- Always populate `reason` with a concise (one sentence) explanation.
"""

_EXTRACT_SYSTEM = """\
You are an entity extractor for a memory system.

You will be given:
- A list of REGISTERED ENTITY TYPES the system knows about.
- The text of one source DOCUMENT.

Your job: extract every entity instance that matches a registered type, and for each \
instance produce its canonical KEY (a stable short identifier — for a company, the \
company name; for a person, their name) plus the FIELDS you can confidently fill in \
from the document.

Rules:
- Use ONLY the registered entity types — never invent a new type.
- Encode every value as valid JSON: strings as JSON strings ("Acme"), numbers as JSON \
numbers (250000), money as numbers in the smallest sensible unit (250000 not "$250K").
- Skip any field you are not confident about. Better empty than wrong.
- If the document mentions an entity but you have no canonical key, skip it.
- Use `notes` for parse difficulties (ambiguous mentions, multiple plausible keys).
"""


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
        user = self._format_extract_user(document_text, registered_entity_types, document_label)
        return self._complete(
            model=model or self.smart_model,
            system=_EXTRACT_SYSTEM,
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
        logger.debug("anthropic.complete model=%s response_model=%s", model, response_model.__name__)
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
            rendered_candidates.append({
                "agent_id": c.written_by_agent_id,
                "written_at": c.written_at.isoformat(),
                "source_document_id": str(c.source_document_id) if c.source_document_id else None,
                "value_json": c.value_json,
            })
        return (
            f"POLICY:\n{policy}\n\n"
            f"CANDIDATES:\n{json.dumps(rendered_candidates, indent=2)}"
        )

    @staticmethod
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


__all__ = ["AnthropicLLMClient"]
