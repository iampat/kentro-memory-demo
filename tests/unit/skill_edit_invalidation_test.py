"""Editing a `SKILL.md` must invalidate cached LLM responses.

This test exercises the full layering — `DefaultLLMClient` → `CachingProvider`
→ a fake `Provider` — and verifies that mutating the on-disk skill markdown
between two otherwise-identical calls forces a fresh provider call. Without
this property, a non-programmer editing `skills/<name>/SKILL.md` would see
their change silently ignored for any input that already had a cached answer.

The test points `skill_loader._SKILLS_DIR` at a tmp directory, writes a fake
`skill_resolver/SKILL.md`, runs a `run_skill_resolver` call, edits the markdown,
and runs the same call again. Expectation: the inner provider is called twice.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from kentro_server.skills import skill_loader
from kentro_server.skills.cache import CachingProvider
from kentro_server.skills.llm_client import (
    DefaultLLMClient,
    SkillResolverDecision,
)
from kentro_server.skills.provider import Provider


@dataclass
class _SystemRecordingProvider(Provider):
    """Captures the rendered `system` text from each `complete()` call."""

    decision: SkillResolverDecision = field(
        default_factory=lambda: SkillResolverDecision(
            chosen_value_json=None,
            reason="recorded",
        )
    )
    systems: list[str] = field(default_factory=list)

    def complete(self, *, model, system, user, response_model, max_tokens=4096, max_retries=3):
        self.systems.append(system)
        return self.decision


@pytest.fixture
def fake_skills_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `load_skill_markdown` at a clean tmp dir for the duration of the test."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_loader, "_SKILLS_DIR", skills_dir)
    return skills_dir


def _write_skill(skills_dir: Path, name: str, content: str) -> None:
    skill = skills_dir / name
    skill.mkdir(exist_ok=True)
    (skill / "SKILL.md").write_text(content, encoding="utf-8")


def test_editing_skill_markdown_invalidates_cache(fake_skills_dir: Path, tmp_path: Path) -> None:
    """Same call args, mutated SKILL.md → must produce a cache miss on the second call."""
    _write_skill(fake_skills_dir, "skill_resolver", "POLICY V1: latest write wins.")

    inner = _SystemRecordingProvider()
    cache = CachingProvider(inner=inner, cache_dir=tmp_path / "cache", enabled=True)
    client = DefaultLLMClient(
        fast_provider=cache,
        smart_provider=cache,
        fast_model="claude-haiku-4-5",
        smart_model="claude-sonnet-4-6",
    )

    client.run_skill_resolver(prompt="P", candidates=[])
    if cache.stats.inner_calls != 1:
        raise AssertionError(f"first call must be a miss, got {cache.stats.render()}")

    # Repeat with no changes — should be a cache hit.
    client.run_skill_resolver(prompt="P", candidates=[])
    if cache.stats.hits != 1 or cache.stats.inner_calls != 1:
        raise AssertionError(f"identical second call must be a hit, got {cache.stats.render()}")

    # The compliance officer edits the policy on camera, in the demo. Next call
    # must reach the inner provider with the new system text.
    _write_skill(fake_skills_dir, "skill_resolver", "POLICY V2: written outweighs verbal.")

    client.run_skill_resolver(prompt="P", candidates=[])
    if cache.stats.inner_calls != 2:
        raise AssertionError(
            "editing SKILL.md MUST invalidate the cache; "
            f"got stats={cache.stats.render()} (still 1 inner call → stale answer returned)"
        )

    # Sanity: the provider received the V2 text on the third call, not V1 again.
    if "POLICY V2" not in inner.systems[-1]:
        raise AssertionError(
            f"third call should have used POLICY V2, system text was: {inner.systems[-1]!r}"
        )
