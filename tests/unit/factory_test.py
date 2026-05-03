"""LLMClient factory tests — provider routing by model prefix.

These tests do NOT make any network calls. They verify that `make_llm_client`
selects the right Provider for the configured model names, wraps each in a
`CachingProvider`, and raises a clear `LLMConfigError` when keys are missing.

Mixed-tier deployments fall out of composition (two different Providers passed
to `DefaultLLMClient`) — there is no `RoutingLLMClient` class anymore.
"""

from pathlib import Path

import pytest
from kentro_server.settings import Settings
from kentro_server.skills.anthropic_provider import AnthropicProvider
from kentro_server.skills.cache import CachingProvider
from kentro_server.skills.factory import (
    cache_metadata,
    cache_stats,
    detect_provider,
    make_llm_client,
)
from kentro_server.skills.gemini_provider import GeminiProvider
from kentro_server.skills.llm_client import (
    DefaultLLMClient,
    LLMConfigError,
    OfflineLLMClient,
)


def _settings(tmp_path: Path, **overrides) -> Settings:
    """Build a Settings with explicit values, isolated from `.env` and `kentro.toml`."""
    base: dict = {
        "anthropic_api_key": "test-anthropic",
        "google_api_key": "test-google",
        "kentro_llm_fast_model": "claude-haiku-4-5",
        "kentro_llm_smart_model": "claude-sonnet-4-6",
        # Mirrors the production default (`kentro.toml`): cache ON.
        "kentro_llm_cache_enabled": True,
        "kentro_state_dir": tmp_path,
    }
    base.update(overrides)
    s = Settings()
    return s.model_copy(update=base)


# === detect_provider ===


def test_detect_provider_claude_models() -> None:
    if detect_provider("claude-haiku-4-5") != "anthropic":
        raise AssertionError("claude-* must route to anthropic")
    if detect_provider("claude-sonnet-4-6") != "anthropic":
        raise AssertionError("claude-* must route to anthropic")


def test_detect_provider_gemini_models() -> None:
    if detect_provider("gemini-3.1-flash-lite") != "google":
        raise AssertionError("gemini-* must route to google")
    if detect_provider("gemini-3.1-pro") != "google":
        raise AssertionError("gemini-* must route to google")


def test_detect_provider_unknown_prefix_raises() -> None:
    with pytest.raises(LLMConfigError, match="must start with"):
        detect_provider("gpt-5")


# === make_llm_client — single provider ===


def test_make_client_anthropic_only(tmp_path: Path) -> None:
    client = make_llm_client(_settings(tmp_path))
    if not isinstance(client, DefaultLLMClient):
        raise AssertionError(f"factory must return DefaultLLMClient, got {type(client)}")
    # Same backend → both tiers point at the same cached provider instance.
    if client.fast_provider is not client.smart_provider:
        raise AssertionError(
            "single-backend deployment should share one CachingProvider across tiers"
        )
    if not isinstance(client.fast_provider, CachingProvider):
        raise AssertionError("provider must be wrapped in CachingProvider")
    if not isinstance(client.fast_provider.inner, AnthropicProvider):
        raise AssertionError(
            f"inner provider must be AnthropicProvider, got {type(client.fast_provider.inner)}"
        )


def test_make_client_gemini_only(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        kentro_llm_fast_model="gemini-3.1-flash-lite",
        kentro_llm_smart_model="gemini-3.1-pro",
    )
    client = make_llm_client(s)
    if not isinstance(client, DefaultLLMClient):
        raise AssertionError(f"factory must return DefaultLLMClient, got {type(client)}")
    if client.fast_provider is not client.smart_provider:
        raise AssertionError("single-backend deployment should share one CachingProvider")
    if not isinstance(client.fast_provider, CachingProvider):
        raise AssertionError("provider must be wrapped in CachingProvider")
    if not isinstance(client.fast_provider.inner, GeminiProvider):
        raise AssertionError(
            f"inner provider must be GeminiProvider, got {type(client.fast_provider.inner)}"
        )


def test_make_client_mixed_providers_uses_two_caches(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        kentro_llm_fast_model="claude-haiku-4-5",
        kentro_llm_smart_model="gemini-3.1-pro",
    )
    client = make_llm_client(s)
    if client.fast_provider is client.smart_provider:
        raise AssertionError("mixed-backend deployment must use two distinct providers")
    if not isinstance(client.fast_provider, CachingProvider) or not isinstance(
        client.smart_provider, CachingProvider
    ):
        raise AssertionError("both tiers must be wrapped in CachingProvider")
    if not isinstance(client.fast_provider.inner, AnthropicProvider):
        raise AssertionError("fast tier inner must be Anthropic")
    if not isinstance(client.smart_provider.inner, GeminiProvider):
        raise AssertionError("smart tier inner must be Gemini")


def test_make_client_records_tier_model_names(tmp_path: Path) -> None:
    """`DefaultLLMClient` exposes `fast_model` / `smart_model` so callers (the cache
    key would no longer rely on these — they're surfaced for debugging / `/llm/stats`)."""
    client = make_llm_client(
        _settings(
            tmp_path,
            kentro_llm_fast_model="claude-haiku-4-5",
            kentro_llm_smart_model="claude-sonnet-4-6",
        )
    )
    if client.fast_model != "claude-haiku-4-5":
        raise AssertionError(f"fast_model not surfaced: {client.fast_model!r}")
    if client.smart_model != "claude-sonnet-4-6":
        raise AssertionError(f"smart_model not surfaced: {client.smart_model!r}")


# === Misconfiguration ===


def test_anthropic_model_without_anthropic_key_raises(tmp_path: Path) -> None:
    s = _settings(tmp_path, anthropic_api_key=None)
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        make_llm_client(s)


def test_gemini_model_without_google_key_raises(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        kentro_llm_fast_model="gemini-3.1-flash-lite",
        kentro_llm_smart_model="gemini-3.1-pro",
        google_api_key=None,
    )
    with pytest.raises(LLMConfigError, match="GOOGLE_API_KEY"):
        make_llm_client(s)


def test_mixed_providers_with_one_missing_key_raises(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        kentro_llm_fast_model="claude-haiku-4-5",
        kentro_llm_smart_model="gemini-3.1-pro",
        google_api_key=None,
    )
    with pytest.raises(LLMConfigError, match="GOOGLE_API_KEY"):
        make_llm_client(s)


# === Cache toggle propagates ===


def test_cache_can_be_disabled(tmp_path: Path) -> None:
    """The factory's `CachingProvider`s honor `kentro_llm_cache_enabled=False`."""
    client = make_llm_client(_settings(tmp_path, kentro_llm_cache_enabled=False))
    if not isinstance(client.fast_provider, CachingProvider):
        raise AssertionError("provider must still be wrapped in CachingProvider")
    if client.fast_provider.enabled:
        raise AssertionError("cache must be disabled when KENTRO_LLM_CACHE_ENABLED=False")


def test_cache_can_be_enabled(tmp_path: Path) -> None:
    """The factory's `CachingProvider`s honor `kentro_llm_cache_enabled=True`."""
    client = make_llm_client(_settings(tmp_path, kentro_llm_cache_enabled=True))
    if not isinstance(client.fast_provider, CachingProvider):
        raise AssertionError("provider must be wrapped in CachingProvider")
    if not client.fast_provider.enabled:
        raise AssertionError("cache must be enabled when KENTRO_LLM_CACHE_ENABLED=True")


# === cache_stats / cache_metadata aggregator helpers ===


def test_cache_stats_returns_none_for_offline_client() -> None:
    """The aggregator helpers handle non-DefaultLLMClient inputs gracefully."""
    if cache_stats(OfflineLLMClient()) is not None:
        raise AssertionError("cache_stats must return None for OfflineLLMClient")
    if cache_metadata(OfflineLLMClient()) is not None:
        raise AssertionError("cache_metadata must return None for OfflineLLMClient")


def test_cache_stats_aggregates_single_backend(tmp_path: Path) -> None:
    """When fast and smart share one CachingProvider, stats are reported once."""
    client = make_llm_client(_settings(tmp_path))
    # Forge some hits/misses on the shared provider directly, then aggregate.
    cp = client.fast_provider
    if not isinstance(cp, CachingProvider):
        raise AssertionError("fast provider should be CachingProvider")
    cp.stats.hits = 7
    cp.stats.inner_calls = 3
    aggregated = cache_stats(client)
    if aggregated is None:
        raise AssertionError("aggregator must find the cache")
    if aggregated.hits != 7 or aggregated.inner_calls != 3:
        raise AssertionError(
            f"single-backend stats must NOT be double-counted, got {aggregated.render()}"
        )


def test_cache_stats_aggregates_mixed_backend(tmp_path: Path) -> None:
    """Mixed-backend → two CachingProviders → counters add up."""
    client = make_llm_client(
        _settings(
            tmp_path,
            kentro_llm_fast_model="claude-haiku-4-5",
            kentro_llm_smart_model="gemini-3.1-pro",
        )
    )
    fast_cp, smart_cp = client.fast_provider, client.smart_provider
    if not isinstance(fast_cp, CachingProvider) or not isinstance(smart_cp, CachingProvider):
        raise AssertionError("both tiers must be CachingProvider")
    fast_cp.stats.hits, fast_cp.stats.inner_calls = 4, 1
    smart_cp.stats.hits, smart_cp.stats.inner_calls = 2, 6
    aggregated = cache_stats(client)
    if aggregated is None:
        raise AssertionError("aggregator must find caches")
    if aggregated.hits != 6 or aggregated.inner_calls != 7:
        raise AssertionError(
            f"mixed-backend stats must sum across providers, got {aggregated.render()}"
        )
