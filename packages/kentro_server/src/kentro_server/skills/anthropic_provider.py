"""AnthropicProvider — concrete `Provider` backed by `anthropic` + `instructor`.

This module knows nothing about skills, kentro types, or prompt formatting.
It accepts the rendered (system, user, model, response_model) tuple and
returns a validated Pydantic instance.
"""

import logging
from typing import TypeVar

import anthropic
import instructor
from pydantic import BaseModel

from kentro_server.skills.provider import Provider

logger = logging.getLogger(__name__)

_TModel = TypeVar("_TModel", bound=BaseModel)


class AnthropicProvider(Provider):
    """Anthropic-backed `Provider`.

    `temperature=0` is hard-coded: determinism is part of the cache contract.
    Allowing callers to override it would let two "identical" calls produce
    different answers, which would silently corrupt the cache.
    """

    def __init__(self, *, api_key: str) -> None:
        self._raw = anthropic.Anthropic(api_key=api_key)
        self._client = instructor.from_anthropic(self._raw)

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
        logger.debug(
            "anthropic.complete model=%s response_model=%s", model, response_model.__name__
        )
        return self._client.messages.create(
            model=model,
            temperature=0,
            max_tokens=max_tokens,
            max_retries=max_retries,
            system=system,
            messages=[{"role": "user", "content": user}],
            response_model=response_model,
        )


__all__ = ["AnthropicProvider"]
