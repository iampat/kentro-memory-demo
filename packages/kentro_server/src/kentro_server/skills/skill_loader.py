"""Read a skill's `SKILL.md` from disk on every call.

Per the Step-7 design decision: skills are authored in markdown files that live
under `skills/<name>/SKILL.md`. A non-programmer can edit a skill's behavior by
editing the markdown — no Python required. Whoever drops a markdown file in the
right shape registers a new skill.

Always-re-read keeps the implementation trivially simple and supports the demo
beat where a compliance officer edits the SkillResolver policy on camera and
the very next read picks it up. Per-call disk I/O is microseconds; we don't need
caching for v0.
"""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent


def load_skill_markdown(skill_name: str) -> str:
    """Return the raw markdown of `skills/<skill_name>/SKILL.md`.

    Raises `FileNotFoundError` with a clear message if the skill is missing.
    """
    path = _SKILLS_DIR / skill_name / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(
            f"skill {skill_name!r} not found at {path} — "
            "skills are markdown files at `skills/<name>/SKILL.md`"
        )
    return path.read_text(encoding="utf-8")


__all__ = ["load_skill_markdown"]
