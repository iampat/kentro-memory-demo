"""LLM-backed skills.

Public surface:
- `LLMClient` — abstract interface every backend implements.
- `OfflineLLMClient` — test stand-in (never used in production).
- `make_llm_client(settings)` — factory that picks the right backend per tier.
- `CachingLLMClient` — disk-cache wrapper applied by the factory.
- `SkillResolverDecision`, `ExtractionResult`, `ExtractedEntity`, `ExtractedField` — output schemas.
"""

from kentro_server.skills.cache import CacheStats, CachingLLMClient
from kentro_server.skills.factory import detect_provider, make_llm_client
from kentro_server.skills.llm_client import (
    ExtractedEntity,
    ExtractedField,
    ExtractionResult,
    LLMClient,
    LLMConfigError,
    LLMOfflineError,
    OfflineLLMClient,
    SkillResolverDecision,
)

__all__ = [
    "CacheStats",
    "CachingLLMClient",
    "ExtractedEntity",
    "ExtractedField",
    "ExtractionResult",
    "LLMClient",
    "LLMConfigError",
    "LLMOfflineError",
    "OfflineLLMClient",
    "SkillResolverDecision",
    "detect_provider",
    "make_llm_client",
]
