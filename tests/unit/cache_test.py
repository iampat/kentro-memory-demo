"""Tests for `CachingLLMClient` — fingerprinting, hit/miss accounting, toggle."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from kentro_server.skills.cache import CachingLLMClient
from kentro_server.skills.llm_client import (
    ExtractedEntity,
    ExtractedField,
    ExtractionResult,
    LLMClient,
    LLMOfflineError,
    SkillResolverDecision,
)
from kentro_server.store.models import FieldWriteRow


@dataclass
class _CountingLLM(LLMClient):
    """Inner client used to verify the cache wrapper's behavior."""

    fast_model: str = "claude-haiku-4-5"
    smart_model: str = "claude-sonnet-4-6"
    skill_calls: int = 0
    extract_calls: int = 0
    skill_decision: SkillResolverDecision = field(
        default_factory=lambda: SkillResolverDecision(
            chosen_value_json="300000", reason="picked email",
        )
    )
    extraction_result: ExtractionResult = field(
        default_factory=lambda: ExtractionResult(
            entities=(ExtractedEntity(
                entity_type="Customer", key="Acme",
                fields=(ExtractedField(field_name="deal_size", value_json="250000"),),
            ),),
        )
    )

    def run_skill_resolver(self, *, prompt, candidates, model=None):
        self.skill_calls += 1
        return self.skill_decision

    def extract_entities(self, *, document_text, registered_entity_types, document_label=None, model=None):
        self.extract_calls += 1
        return self.extraction_result


def _write(value: str = "250000") -> FieldWriteRow:
    return FieldWriteRow(
        id=uuid4(),
        entity_id=uuid4(),
        field_name="deal_size",
        value_json=value,
        written_by_agent_id="ingestion_agent",
        written_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
        rule_version_at_write=1,
    )


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / ".llm_cache"


def test_first_call_is_a_miss_second_is_a_hit(cache_dir: Path) -> None:
    inner = _CountingLLM()
    cache = CachingLLMClient(inner=inner, cache_dir=cache_dir, enabled=True)

    cache.run_skill_resolver(prompt="P", candidates=[_write()], model="claude-haiku-4-5")
    if cache.stats.hits != 0 or cache.stats.inner_calls != 1:
        raise AssertionError(f"after first call expected 0/1, got {cache.stats.render()}")
    if inner.skill_calls != 1:
        raise AssertionError("inner should have been called once")

    # New CachingLLMClient instance — proves cache lives on disk, not in memory.
    cache2 = CachingLLMClient(inner=inner, cache_dir=cache_dir, enabled=True)
    cache2.run_skill_resolver(prompt="P", candidates=[_write()], model="claude-haiku-4-5")
    if cache2.stats.hits != 1 or cache2.stats.inner_calls != 0:
        raise AssertionError(f"second call (new wrapper) expected 1/0, got {cache2.stats.render()}")
    if inner.skill_calls != 1:
        raise AssertionError("inner must NOT have been called again on a hit")


def test_disabling_cache_bypasses_reads_and_writes(cache_dir: Path) -> None:
    inner = _CountingLLM()
    cache = CachingLLMClient(inner=inner, cache_dir=cache_dir, enabled=False)

    for _ in range(3):
        cache.run_skill_resolver(prompt="P", candidates=[_write()], model="claude-haiku-4-5")
    if cache.stats.hits != 0:
        raise AssertionError("disabled cache must never report hits")
    if cache.stats.inner_calls != 3:
        raise AssertionError(f"all 3 calls must reach inner, got {cache.stats.inner_calls}")
    # And nothing was written to disk
    if any(cache_dir.iterdir()):
        raise AssertionError("disabled cache must not write to disk")


def test_different_prompts_produce_different_cache_keys(cache_dir: Path) -> None:
    inner = _CountingLLM()
    cache = CachingLLMClient(inner=inner, cache_dir=cache_dir, enabled=True)

    cache.run_skill_resolver(prompt="written outweighs verbal", candidates=[_write()], model="claude-haiku-4-5")
    cache.run_skill_resolver(prompt="latest wins", candidates=[_write()], model="claude-haiku-4-5")
    if inner.skill_calls != 2:
        raise AssertionError("two different prompts must produce two inner calls")
    files = list(cache_dir.iterdir())
    if len(files) != 2:
        raise AssertionError(f"expected 2 cache files, got {len(files)}")


def test_extract_entities_is_cached_separately(cache_dir: Path) -> None:
    inner = _CountingLLM()
    cache = CachingLLMClient(inner=inner, cache_dir=cache_dir, enabled=True)

    cache.extract_entities(
        document_text="Acme call notes — renewal at $250K",
        registered_entity_types=["Customer"],
        document_label="acme_call.md",
        model="claude-sonnet-4-6",
    )
    cache.extract_entities(
        document_text="Acme call notes — renewal at $250K",
        registered_entity_types=["Customer"],
        document_label="acme_call.md",
        model="claude-sonnet-4-6",
    )
    if inner.extract_calls != 1:
        raise AssertionError(f"identical extract calls must hit cache, inner_calls={inner.extract_calls}")
    if cache.stats.hit_rate != 0.5:
        raise AssertionError(f"hit rate after 1 miss + 1 hit should be 0.5, got {cache.stats.hit_rate}")


def test_hit_rate_zero_when_no_calls(cache_dir: Path) -> None:
    cache = CachingLLMClient(inner=_CountingLLM(), cache_dir=cache_dir)
    if cache.stats.hit_rate != 0.0:
        raise AssertionError(f"hit_rate before any calls must be 0.0, got {cache.stats.hit_rate}")
    if cache.stats.total != 0:
        raise AssertionError("total must be 0 with no calls")


def test_offline_extract_raises(cache_dir: Path) -> None:
    """Sanity: OfflineLLMClient.extract_entities raises (not used in production)."""
    from kentro_server.skills.llm_client import OfflineLLMClient
    cache = CachingLLMClient(inner=OfflineLLMClient(), cache_dir=cache_dir)
    with pytest.raises(LLMOfflineError):
        cache.extract_entities(
            document_text="x", registered_entity_types=["Customer"], model="claude-sonnet-4-6",
        )
