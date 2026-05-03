"""LLM-backed skills.

Public surface (composition layering — see `CLAUDE.md`):

- `Provider` / `OfflineProvider` / `AnthropicProvider` / `GeminiProvider` —
  low-level structured-output backends.
- `CachingProvider` — wraps any `Provider` with a disk-backed cache keyed on
  the rendered request (model + system + user + response_class).
- `LLMClient` — abstract skill-aware façade.
- `DefaultLLMClient` — production composition: takes Providers + tier model
  names via DI, loads `SKILL.md`, formats prompts.
- `OfflineLLMClient` — test stand-in (gracefully UNRESOLVED for SkillResolver,
  raises elsewhere).
- `make_llm_client(settings)` — factory that wires Providers + cache + DefaultLLMClient.
- `cache_stats(client)` / `cache_metadata(client)` — aggregate `/llm/stats` view.
- Output schemas: `SkillResolverDecision`, `ExtractionResult`, `ExtractedEntity`,
  `ExtractedField`.
"""

from kentro_server.skills.cache import CacheStats, CachingProvider
from kentro_server.skills.factory import (
    cache_metadata,
    cache_stats,
    detect_provider,
    make_llm_client,
)
from kentro_server.skills.llm_client import (
    DefaultLLMClient,
    ExtractedEntity,
    ExtractedField,
    ExtractionResult,
    LLMClient,
    LLMConfigError,
    LLMOfflineError,
    OfflineLLMClient,
    SkillResolverDecision,
)
from kentro_server.skills.provider import OfflineProvider, Provider

__all__ = [
    "CacheStats",
    "CachingProvider",
    "DefaultLLMClient",
    "ExtractedEntity",
    "ExtractedField",
    "ExtractionResult",
    "LLMClient",
    "LLMConfigError",
    "LLMOfflineError",
    "OfflineLLMClient",
    "OfflineProvider",
    "Provider",
    "SkillResolverDecision",
    "cache_metadata",
    "cache_stats",
    "detect_provider",
    "make_llm_client",
]
