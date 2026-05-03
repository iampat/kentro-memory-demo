"""GeminiProvider — concrete `Provider` backed by `google.genai` + `instructor`.

Symmetric to `AnthropicProvider`. Knows nothing about skills or kentro types;
the `LLMClient` layer above is responsible for prompt construction.
"""

import logging
from typing import TypeVar, cast

import instructor
from google import genai
from pydantic import BaseModel

from kentro_server.skills.provider import Provider

logger = logging.getLogger(__name__)

_TModel = TypeVar("_TModel", bound=BaseModel)


class GeminiProvider(Provider):
    """Google-backed `Provider`."""

    def __init__(self, *, api_key: str) -> None:
        self._raw = genai.Client(api_key=api_key)
        self._client = instructor.from_genai(self._raw)

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_model: type[_TModel],
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> _TModel:
        logger.debug("gemini.complete model=%s response_model=%s", model, response_model.__name__)
        # instructor.from_genai's `chat.completions.create` returns the validated
        # `response_model` instance at runtime when the underlying client is sync
        # (which `genai.Client(api_key=...)` is). ty's stubs incorrectly type it as
        # a coroutine because instructor uses sync/async overloads, so we cast.
        return cast(
            _TModel,
            self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_model=response_model,
                max_retries=max_retries,
            ),
        )


__all__ = ["GeminiProvider"]
