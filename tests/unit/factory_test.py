"""LLMClient factory tests — provider routing by model prefix.

These tests do NOT make any network calls. They verify that `make_llm_client`
selects the right backend class for the configured model names and raises a clear
`LLMConfigError` when keys are missing.
"""

from pathlib import Path

import pytest

from kentro_server.settings import Settings
from kentro_server.skills.anthropic_client import AnthropicLLMClient
from kentro_server.skills.cache import CachingLLMClient
from kentro_server.skills.factory import (
    _RoutingLLMClient,
    detect_provider,
    make_llm_client,
)
from kentro_server.skills.gemini_client import GeminiLLMClient
from kentro_server.skills.llm_client import LLMConfigError


def _settings(tmp_path: Path, **overrides) -> Settings:
    base: dict = {
        "anthropic_api_key": "test-anthropic",
        "google_api_key": "test-google",
        "kentro_llm_fast_model": "claude-haiku-4-5",
        "kentro_llm_smart_model": "claude-sonnet-4-6",
        "kentro_llm_cache_enabled": True,
        "kentro_state_dir": tmp_path,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


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
    if not isinstance(client, CachingLLMClient):
        raise AssertionError("factory must wrap in CachingLLMClient")
    if not isinstance(client.inner, AnthropicLLMClient):
        raise AssertionError(f"inner must be AnthropicLLMClient, got {type(client.inner)}")


def test_make_client_gemini_only(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        kentro_llm_fast_model="gemini-3.1-flash-lite",
        kentro_llm_smart_model="gemini-3.1-pro",
    )
    client = make_llm_client(s)
    if not isinstance(client, CachingLLMClient):
        raise AssertionError("factory must wrap in CachingLLMClient")
    if not isinstance(client.inner, GeminiLLMClient):
        raise AssertionError(f"inner must be GeminiLLMClient, got {type(client.inner)}")


def test_make_client_mixed_providers_uses_routing(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        kentro_llm_fast_model="claude-haiku-4-5",
        kentro_llm_smart_model="gemini-3.1-pro",
    )
    client = make_llm_client(s)
    if not isinstance(client.inner, _RoutingLLMClient):  # type: ignore[attr-defined]
        raise AssertionError(f"mixed providers must use _RoutingLLMClient, got {type(client.inner)}")
    if not isinstance(client.inner.fast, AnthropicLLMClient):  # type: ignore[attr-defined]
        raise AssertionError("fast tier must be Anthropic")
    if not isinstance(client.inner.smart, GeminiLLMClient):  # type: ignore[attr-defined]
        raise AssertionError("smart tier must be Gemini")


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
    client = make_llm_client(_settings(tmp_path, kentro_llm_cache_enabled=False))
    if not isinstance(client, CachingLLMClient):
        raise AssertionError("client must still be the cache wrapper")
    if client.enabled:
        raise AssertionError("cache must be disabled when KENTRO_LLM_CACHE_ENABLED=False")
