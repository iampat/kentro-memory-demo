"""Factory: build a `DefaultLLMClient` wired to cached `Provider`(s) for the configured settings.

Detects the provider per tier from the model-name prefix:
    "claude-*"  → AnthropicProvider  (requires `ANTHROPIC_API_KEY`)
    "gemini-*"  → GeminiProvider     (requires `GOOGLE_API_KEY`)

Mixed-provider tiers are supported by passing two different `Provider`s into
`DefaultLLMClient`. There is no `RoutingLLMClient` — routing falls out of
composition. Misconfiguration raises `LLMConfigError` at startup; never
silently falls back to a different provider.

Each `Provider` is wrapped in a `CachingProvider` honoring
`KENTRO_LLM_CACHE_ENABLED`. When fast and smart resolve to the same provider
type, both tiers share a single cached `Provider` so cache stats aggregate
naturally and the inner SDK client is built once.
"""

import logging

from kentro_server.settings import Settings
from kentro_server.skills.anthropic_provider import AnthropicProvider
from kentro_server.skills.cache import CacheStats, CachingProvider
from kentro_server.skills.gemini_provider import GeminiProvider
from kentro_server.skills.llm_client import (
    DefaultLLMClient,
    LLMClient,
    LLMConfigError,
)
from kentro_server.skills.provider import Provider

logger = logging.getLogger(__name__)


def detect_provider(model: str) -> str:
    """Map a model name prefix to its provider id."""
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gemini-"):
        return "google"
    raise LLMConfigError(f"unknown model {model!r}: must start with 'claude-' or 'gemini-'")


def make_llm_client(settings: Settings) -> DefaultLLMClient:
    """Build the `DefaultLLMClient` for `settings`. Raises `LLMConfigError` on misconfiguration.

    Always returns `DefaultLLMClient` directly (not the abstract `LLMClient`) so
    callers that need to introspect `.fast_provider` / `.smart_provider` (the
    `/llm/stats` endpoint, factory tests) get the concrete type without casts.
    """
    fast_provider_kind = detect_provider(settings.kentro_llm_fast_model)
    smart_provider_kind = detect_provider(settings.kentro_llm_smart_model)

    fast_provider = _build_cached_provider(provider_kind=fast_provider_kind, settings=settings)
    if fast_provider_kind == smart_provider_kind:
        # Same backend serves both tiers → single SDK client + single cache wrapper,
        # so both tiers' calls show up in one CacheStats.
        smart_provider: CachingProvider = fast_provider
    else:
        smart_provider = _build_cached_provider(
            provider_kind=smart_provider_kind, settings=settings
        )

    client = DefaultLLMClient(
        fast_provider=fast_provider,
        smart_provider=smart_provider,
        fast_model=settings.kentro_llm_fast_model,
        smart_model=settings.kentro_llm_smart_model,
    )
    logger.info(
        "DefaultLLMClient ready: fast=%s (%s), smart=%s (%s), cache_enabled=%s",
        settings.kentro_llm_fast_model,
        fast_provider_kind,
        settings.kentro_llm_smart_model,
        smart_provider_kind,
        settings.kentro_llm_cache_enabled,
    )
    return client


def _build_cached_provider(*, provider_kind: str, settings: Settings) -> CachingProvider:
    """Build a `Provider` for `provider_kind`, wrap it in `CachingProvider`."""
    inner = _build_provider(provider_kind=provider_kind, settings=settings)
    return CachingProvider(
        inner=inner,
        cache_dir=settings.llm_cache_dir,
        enabled=settings.kentro_llm_cache_enabled,
    )


def _build_provider(*, provider_kind: str, settings: Settings) -> Provider:
    """Construct the concrete `Provider` for a tier.

    Both `AnthropicProvider` and `GeminiProvider` are imported at module top
    rather than lazily — `instructor` is loaded by the SDK regardless, so there
    is no startup-time saving to gain by deferring. Per CLAUDE.md "no mid-code
    imports", top-level wins.
    """
    if provider_kind == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMConfigError(
                "kentro_llm_*_model resolves to Anthropic, but ANTHROPIC_API_KEY is not set"
            )
        return AnthropicProvider(api_key=settings.anthropic_api_key)
    if provider_kind == "google":
        if not settings.google_api_key:
            raise LLMConfigError(
                "kentro_llm_*_model resolves to Google, but GOOGLE_API_KEY is not set"
            )
        return GeminiProvider(api_key=settings.google_api_key)
    raise LLMConfigError(f"unknown provider {provider_kind!r}")


def cache_stats(client: LLMClient) -> CacheStats | None:
    """Aggregate `CacheStats` across the providers wired into `client`.

    Returns `None` if `client` is not a `DefaultLLMClient` (e.g. an
    `OfflineLLMClient` in tests) or if no provider is a `CachingProvider`.
    When fast and smart share the same `CachingProvider` instance (the common
    single-backend case), counters are reported once — not double-counted.
    """
    if not isinstance(client, DefaultLLMClient):
        return None
    seen: set[int] = set()
    total = CacheStats()
    found = False
    for provider in (client.fast_provider, client.smart_provider):
        if not isinstance(provider, CachingProvider):
            continue
        if id(provider) in seen:
            continue
        seen.add(id(provider))
        total.hits += provider.stats.hits
        total.inner_calls += provider.stats.inner_calls
        found = True
    return total if found else None


def cache_metadata(client: LLMClient) -> dict | None:
    """Return `{enabled, cache_dir}` for the (single) cache wrapper, if there is one.

    When the two tiers use different `CachingProvider`s (mixed-backend deployment)
    they always share `cache_dir` and `enabled` because both come from the same
    `Settings`, so reporting one tier's metadata is accurate for both.
    """
    if not isinstance(client, DefaultLLMClient):
        return None
    for provider in (client.fast_provider, client.smart_provider):
        if isinstance(provider, CachingProvider):
            return {"enabled": provider.enabled, "cache_dir": str(provider.cache_dir)}
    return None


__all__ = [
    "cache_metadata",
    "cache_stats",
    "detect_provider",
    "make_llm_client",
]
