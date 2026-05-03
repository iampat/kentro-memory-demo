"""Provider — the dumb, narrow seam through which every LLM HTTP call goes.

A `Provider` knows how to take a fully-formed structured-output request
(`model`, `system`, `user`, `response_model`) and return a validated Pydantic
instance. It does not know about skills, prompt-building, or kentro's domain
types. That separation is deliberate: `CachingProvider` (in `cache.py`) wraps
any `Provider` and fingerprints exactly the request the LLM sees, so the cache
key is correct by construction — no hidden inputs.

Per `CLAUDE.md` "Dependency injection & composition over inheritance", the
high-level `LLMClient` API in `llm_client.py` composes Providers via DI
rather than inheriting per-provider subclasses.

Concrete providers live alongside this module:
- `AnthropicProvider` — `anthropic_provider.py`
- `GeminiProvider` — `gemini_provider.py`
- `OfflineProvider` — defined here; raises on every call.
"""

import logging
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from kentro_server.skills.llm_client import LLMOfflineError

logger = logging.getLogger(__name__)

_TModel = TypeVar("_TModel", bound=BaseModel)


class Provider(ABC):
    """Low-level structured-output LLM seam.

    Implementations must:
    - Set temperature=0 (determinism is part of the cache contract).
    - Use `instructor` (or equivalent) to validate the response against
      `response_model` and retry on parse failure.
    - Keep `user` content in the user message slot (no prompt injection paths).
    """

    @abstractmethod
    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_model: type[_TModel],
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> _TModel: ...


class OfflineProvider(Provider):
    """Test/CI stand-in. Raises on every `complete()` — never used in production.

    Use this when you want the upstream `DefaultLLMClient` to behave as if no
    backend is configured. Tests that need *some* response (e.g. SkillResolver
    returning UNRESOLVED gracefully) should use `OfflineLLMClient` from
    `llm_client.py` instead — it sits one level higher and can return canned
    decisions without going through a Provider.
    """

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
        raise LLMOfflineError(
            f"OfflineProvider.complete called for model={model!r} "
            f"response_model={response_model.__name__} — no real LLM backend configured"
        )


__all__ = ["OfflineProvider", "Provider"]
