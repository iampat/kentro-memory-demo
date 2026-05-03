"""Tests for `CachingProvider` — fingerprinting, hit/miss accounting, toggle.

The cache lives below the skill layer: it wraps any `Provider` and fingerprints
the *rendered* request `(model, system, user, response_class)`. Because the
rendered system prompt includes any `SKILL.md` text, prompt edits invalidate
the cache by construction (see `test_skill_edit_invalidates_cache`).
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from kentro_server.skills.cache import CachingProvider
from kentro_server.skills.llm_client import (
    LLMOfflineError,
    SkillResolverDecision,
)
from kentro_server.skills.provider import OfflineProvider, Provider


@dataclass
class _CountingProvider(Provider):
    """Inner provider used to verify the cache wrapper's behavior.

    Returns a fixed `SkillResolverDecision` for any `complete(...)` call (the
    cache doesn't care about the response *type*, just that it round-trips
    through Pydantic). Counts how many times it was actually called.
    """

    decision: SkillResolverDecision = field(
        default_factory=lambda: SkillResolverDecision(
            chosen_value_json="300000",
            reason="picked email",
        )
    )
    calls: int = 0

    def complete(self, *, model, system, user, response_model, max_tokens=4096, max_retries=3):
        self.calls += 1
        return self.decision


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / ".llm_cache"


def test_first_call_is_a_miss_second_is_a_hit(cache_dir: Path) -> None:
    inner = _CountingProvider()
    cache = CachingProvider(inner=inner, cache_dir=cache_dir, enabled=True)

    cache.complete(
        model="claude-haiku-4-5",
        system="SYS",
        user="USR",
        response_model=SkillResolverDecision,
    )
    if cache.stats.hits != 0 or cache.stats.inner_calls != 1:
        raise AssertionError(f"after first call expected 0/1, got {cache.stats.render()}")
    if inner.calls != 1:
        raise AssertionError("inner should have been called once")

    # New CachingProvider instance — proves the cache lives on disk, not in memory.
    cache2 = CachingProvider(inner=inner, cache_dir=cache_dir, enabled=True)
    cache2.complete(
        model="claude-haiku-4-5",
        system="SYS",
        user="USR",
        response_model=SkillResolverDecision,
    )
    if cache2.stats.hits != 1 or cache2.stats.inner_calls != 0:
        raise AssertionError(
            f"second call (new wrapper) expected 1/0, got {cache2.stats.render()}"
        )
    if inner.calls != 1:
        raise AssertionError("inner must NOT have been called again on a hit")


def test_disabling_cache_bypasses_reads_and_writes(cache_dir: Path) -> None:
    inner = _CountingProvider()
    cache = CachingProvider(inner=inner, cache_dir=cache_dir, enabled=False)

    for _ in range(3):
        cache.complete(
            model="claude-haiku-4-5",
            system="SYS",
            user="USR",
            response_model=SkillResolverDecision,
        )
    if cache.stats.hits != 0:
        raise AssertionError("disabled cache must never report hits")
    if cache.stats.inner_calls != 3:
        raise AssertionError(f"all 3 calls must reach inner, got {cache.stats.inner_calls}")
    # And nothing was written to disk
    if any(cache_dir.iterdir()):
        raise AssertionError("disabled cache must not write to disk")


def test_different_user_payloads_produce_different_cache_keys(cache_dir: Path) -> None:
    inner = _CountingProvider()
    cache = CachingProvider(inner=inner, cache_dir=cache_dir, enabled=True)

    cache.complete(
        model="claude-haiku-4-5",
        system="SYS",
        user="A",
        response_model=SkillResolverDecision,
    )
    cache.complete(
        model="claude-haiku-4-5",
        system="SYS",
        user="B",
        response_model=SkillResolverDecision,
    )
    if inner.calls != 2:
        raise AssertionError("two different user payloads must produce two inner calls")
    files = list(cache_dir.iterdir())
    if len(files) != 2:
        raise AssertionError(f"expected 2 cache files, got {len(files)}")


def test_different_system_prompts_produce_different_cache_keys(cache_dir: Path) -> None:
    """Editing a SKILL.md changes `system` → must produce a cache miss.

    This is the contract that lets a non-programmer edit `skills/<name>/SKILL.md`
    and see the change take effect on the very next call.
    """
    inner = _CountingProvider()
    cache = CachingProvider(inner=inner, cache_dir=cache_dir, enabled=True)

    cache.complete(
        model="claude-haiku-4-5",
        system="SKILL VERSION 1",
        user="USR",
        response_model=SkillResolverDecision,
    )
    cache.complete(
        model="claude-haiku-4-5",
        system="SKILL VERSION 2 — operator edited the markdown",
        user="USR",
        response_model=SkillResolverDecision,
    )
    if inner.calls != 2:
        raise AssertionError(
            "editing the system prompt MUST invalidate the cache "
            f"(otherwise SKILL.md edits are silently ignored). got inner.calls={inner.calls}"
        )


def test_different_models_produce_different_cache_keys(cache_dir: Path) -> None:
    inner = _CountingProvider()
    cache = CachingProvider(inner=inner, cache_dir=cache_dir, enabled=True)

    cache.complete(
        model="claude-haiku-4-5",
        system="SYS",
        user="USR",
        response_model=SkillResolverDecision,
    )
    cache.complete(
        model="claude-sonnet-4-6",
        system="SYS",
        user="USR",
        response_model=SkillResolverDecision,
    )
    if inner.calls != 2:
        raise AssertionError("different models must produce two inner calls")


def test_hit_rate_zero_when_no_calls(cache_dir: Path) -> None:
    cache = CachingProvider(inner=_CountingProvider(), cache_dir=cache_dir)
    if cache.stats.hit_rate != 0.0:
        raise AssertionError(f"hit_rate before any calls must be 0.0, got {cache.stats.hit_rate}")
    if cache.stats.total != 0:
        raise AssertionError("total must be 0 with no calls")


def test_offline_provider_raises_through_cache(cache_dir: Path) -> None:
    """Sanity: cached `OfflineProvider.complete` propagates `LLMOfflineError`."""
    cache = CachingProvider(inner=OfflineProvider(), cache_dir=cache_dir, enabled=True)
    with pytest.raises(LLMOfflineError):
        cache.complete(
            model="claude-haiku-4-5",
            system="SYS",
            user="USR",
            response_model=SkillResolverDecision,
        )
