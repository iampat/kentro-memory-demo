"""LLMClient factory tests — provider routing by model prefix.

These tests do NOT make any network calls. They verify that `make_llm_client`
selects the right backend class for the configured model names and raises a clear
`LLMConfigError` when keys are missing.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from kentro.types import EntityTypeDef, FieldDef
from kentro_server.settings import Settings
from kentro_server.skills.anthropic_client import AnthropicLLMClient
from kentro_server.skills.cache import CachingLLMClient
from kentro_server.skills.factory import (
    RoutingLLMClient,
    detect_provider,
    make_llm_client,
)
from kentro_server.skills.gemini_client import GeminiLLMClient
from kentro_server.skills.llm_client import (
    ExtractedEntity,
    ExtractedField,
    ExtractionResult,
    LLMClient,
    LLMConfigError,
    SkillResolverDecision,
)


def _settings(tmp_path: Path, **overrides) -> Settings:
    """Build a Settings with explicit values, isolated from `.env` and `kentro.toml`.

    `_env_file=None` is documented in pydantic-settings (per-instance override of the
    env_file source). We also have to drop the TOML source explicitly: ty's strict
    parameter check doesn't see `_env_file`, so we pass the override through model_validate
    after the explicit construction.
    """
    base: dict = {
        "anthropic_api_key": "test-anthropic",
        "google_api_key": "test-google",
        "kentro_llm_fast_model": "claude-haiku-4-5",
        "kentro_llm_smart_model": "claude-sonnet-4-6",
        "kentro_llm_cache_enabled": True,
        "kentro_state_dir": tmp_path,
    }
    base.update(overrides)
    # Construct via init (pydantic-settings sources still apply) then override with
    # the explicit dict via model_copy so test values always win.
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
    if not isinstance(client.inner, RoutingLLMClient):  # type: ignore[attr-defined]
        raise AssertionError(
            f"mixed providers must use RoutingLLMClient, got {type(client.inner)}"
        )
    if not isinstance(client.inner.fast, AnthropicLLMClient):  # type: ignore[attr-defined]
        raise AssertionError("fast tier must be Anthropic")
    if not isinstance(client.inner.smart, GeminiLLMClient):  # type: ignore[attr-defined]
        raise AssertionError("smart tier must be Gemini")


# === RoutingLLMClient actually dispatches ===


@dataclass
class _RecordingFakeLLM(LLMClient):
    """Records the kwargs it received so tests can verify forwarding."""

    name: str = "fake"
    fast_model: str = "claude-fake-fast"
    smart_model: str = "gemini-fake-smart"
    skill_calls: list = field(default_factory=list)
    extract_calls: list = field(default_factory=list)

    def run_skill_resolver(self, *, prompt, candidates, model=None):
        self.skill_calls.append({"prompt": prompt, "candidates": candidates, "model": model})
        return SkillResolverDecision(chosen_value_json=None, reason=f"{self.name} declined")

    def extract_entities(
        self, *, document_text, registered_schemas, document_label=None, model=None
    ):
        self.extract_calls.append(
            {
                "document_text": document_text,
                "registered_schemas": registered_schemas,
                "document_label": document_label,
                "model": model,
            }
        )
        return ExtractionResult(
            entities=(
                ExtractedEntity(
                    entity_type="Customer",
                    key="X",
                    fields=(ExtractedField(field_name="name", value_json='"X"'),),
                ),
            ),
        )


def test_routing_client_forwards_skill_resolver_to_fast() -> None:
    fast = _RecordingFakeLLM(name="fast", fast_model="claude-haiku-4-5")
    smart = _RecordingFakeLLM(name="smart", smart_model="gemini-3.1-pro")
    router = RoutingLLMClient(fast=fast, smart=smart)

    router.run_skill_resolver(prompt="P", candidates=[], model=None)
    if len(fast.skill_calls) != 1 or smart.skill_calls:
        raise AssertionError("skill resolver must dispatch to FAST tier only")


def test_routing_client_forwards_extract_to_smart_with_correct_kwargs() -> None:
    """Regression: previously _RoutingLLMClient.extract_entities used the wrong kwarg
    name (`registered_entity_types`) and crashed with TypeError on every ingest in
    mixed-provider mode. This test would have caught it."""
    fast = _RecordingFakeLLM(name="fast")
    smart = _RecordingFakeLLM(name="smart", smart_model="gemini-3.1-pro")
    router = RoutingLLMClient(fast=fast, smart=smart)
    schemas = [EntityTypeDef(name="Customer", fields=(FieldDef(name="name", type_str="str"),))]

    result = router.extract_entities(
        document_text="hello",
        registered_schemas=schemas,
        document_label="doc.md",
        model="gemini-3.1-pro",
    )

    if not result.entities:
        raise AssertionError("extract should return entities from the smart fake")
    if len(smart.extract_calls) != 1 or fast.extract_calls:
        raise AssertionError("extract must dispatch to SMART tier only")
    forwarded = smart.extract_calls[0]
    if forwarded["registered_schemas"] is not schemas:
        raise AssertionError(
            f"schemas must be forwarded by reference, got {forwarded['registered_schemas']!r}"
        )
    if forwarded["model"] != "gemini-3.1-pro":
        raise AssertionError(f"model not forwarded, got {forwarded['model']!r}")


def test_routing_client_exposes_inner_model_names_for_caching() -> None:
    """RoutingLLMClient must expose `fast_model` / `smart_model` so the cache
    wrapper can build a stable cache key without falling back to '<unknown>'."""
    fast = _RecordingFakeLLM(name="fast", fast_model="claude-haiku-4-5")
    smart = _RecordingFakeLLM(name="smart", smart_model="gemini-3.1-pro")
    router = RoutingLLMClient(fast=fast, smart=smart)

    if router.fast_model != "claude-haiku-4-5":
        raise AssertionError(f"fast_model not surfaced: {router.fast_model!r}")
    if router.smart_model != "gemini-3.1-pro":
        raise AssertionError(f"smart_model not surfaced: {router.smart_model!r}")


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
