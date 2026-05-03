"""Factory: build the right LLMClient for the configured settings.

Detects the provider per tier from the model name prefix:
    "claude-*"  → Anthropic  (requires `ANTHROPIC_API_KEY`)
    "gemini-*"  → Google     (requires `GOOGLE_API_KEY`)

Mixed-provider tiers are supported via `RoutingLLMClient`. Misconfiguration raises
`LLMConfigError` at startup — never silently falls back to a different provider.

Always wraps the chosen client(s) in a `CachingLLMClient` honoring
`KENTRO_LLM_CACHE_ENABLED`.
"""

import logging
from typing import TYPE_CHECKING

from kentro_server.settings import Settings
from kentro_server.skills.cache import CachingLLMClient
from kentro_server.skills.llm_client import (
    ExtractionResult,
    LLMClient,
    LLMConfigError,
    SkillResolverDecision,
)

if TYPE_CHECKING:
    from kentro_server.store.models import FieldWriteRow

logger = logging.getLogger(__name__)


def detect_provider(model: str) -> str:
    """Map a model name prefix to its provider id."""
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gemini-"):
        return "google"
    raise LLMConfigError(f"unknown model {model!r}: must start with 'claude-' or 'gemini-'")


def make_llm_client(settings: Settings) -> CachingLLMClient:
    """Build the LLMClient for `settings`. Raises `LLMConfigError` on misconfiguration.

    Always returns a `CachingLLMClient`. Callers that only need the abstract surface
    can treat the return as `LLMClient`; tests that introspect `.inner` use the full
    type. Returning the concrete wrapper here also lets the type checker see the
    `.stats`, `.enabled`, and `.inner` attributes that consumers already rely on.
    """
    fast_provider = detect_provider(settings.kentro_llm_fast_model)
    smart_provider = detect_provider(settings.kentro_llm_smart_model)

    if fast_provider == smart_provider:
        inner = _build_single(
            provider=fast_provider,
            settings=settings,
            fast_model=settings.kentro_llm_fast_model,
            smart_model=settings.kentro_llm_smart_model,
        )
    else:
        fast_only = _build_single(
            provider=fast_provider,
            settings=settings,
            fast_model=settings.kentro_llm_fast_model,
            # smart model on a fast-only client is unused but the constructor wants something:
            smart_model=settings.kentro_llm_fast_model,
        )
        smart_only = _build_single(
            provider=smart_provider,
            settings=settings,
            fast_model=settings.kentro_llm_smart_model,
            smart_model=settings.kentro_llm_smart_model,
        )
        inner = RoutingLLMClient(fast=fast_only, smart=smart_only)

    cache = CachingLLMClient(
        inner=inner,
        cache_dir=settings.llm_cache_dir,
        enabled=settings.kentro_llm_cache_enabled,
    )
    logger.info(
        "LLMClient ready: fast=%s (%s), smart=%s (%s), cache_enabled=%s",
        settings.kentro_llm_fast_model,
        fast_provider,
        settings.kentro_llm_smart_model,
        smart_provider,
        settings.kentro_llm_cache_enabled,
    )
    return cache


def _build_single(
    *, provider: str, settings: Settings, fast_model: str, smart_model: str
) -> LLMClient:
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMConfigError(
                f"model {fast_model!r}/{smart_model!r} resolves to Anthropic, "
                "but ANTHROPIC_API_KEY is not set"
            )
        # Local import keeps the SDK pulled in only when it's actually selected.
        from kentro_server.skills.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(
            api_key=settings.anthropic_api_key,
            fast_model=fast_model,
            smart_model=smart_model,
        )
    if provider == "google":
        if not settings.google_api_key:
            raise LLMConfigError(
                f"model {fast_model!r}/{smart_model!r} resolves to Google, "
                "but GOOGLE_API_KEY is not set"
            )
        from kentro_server.skills.gemini_client import GeminiLLMClient

        return GeminiLLMClient(
            api_key=settings.google_api_key,
            fast_model=fast_model,
            smart_model=smart_model,
        )
    raise LLMConfigError(f"unknown provider {provider!r}")


class RoutingLLMClient(LLMClient):
    """Dispatch fast-tier calls to one client, smart-tier to another.

    Exposes `fast_model` / `smart_model` properties that delegate to the wrapped
    inner clients. The `CachingLLMClient`'s `_peek_attr` lookup uses these to
    derive a stable cache key, so a routed client caches identically to a single-
    provider client.
    """

    def __init__(self, *, fast: LLMClient, smart: LLMClient) -> None:
        self.fast = fast
        self.smart = smart

    @property
    def fast_model(self) -> str:
        # Best-effort: surface the inner client's selected model name. If the inner
        # is itself a router (shouldn't happen) we'd return the inner's `fast_model`.
        return getattr(self.fast, "fast_model", "<unknown-fast>")

    @property
    def smart_model(self) -> str:
        return getattr(self.smart, "smart_model", "<unknown-smart>")

    def run_skill_resolver(
        self,
        *,
        prompt: str,
        candidates: "list[FieldWriteRow]",
        model: str | None = None,
    ) -> SkillResolverDecision:
        return self.fast.run_skill_resolver(prompt=prompt, candidates=candidates, model=model)

    def extract_entities(
        self,
        *,
        document_text: str,
        registered_schemas: list,
        document_label: str | None = None,
        model: str | None = None,
    ) -> ExtractionResult:
        return self.smart.extract_entities(
            document_text=document_text,
            registered_schemas=registered_schemas,
            document_label=document_label,
            model=model,
        )


__all__ = [
    "RoutingLLMClient",
    "detect_provider",
    "make_llm_client",
]
