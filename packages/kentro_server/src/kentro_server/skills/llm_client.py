"""LLMClient — the single seam through which every kentro-server LLM call goes.

Per `implementation-handoff.md` §1.4 ("LLM-call discipline"):
- Structured Pydantic output, always.
- Validation retries (up to 3x) on parse failure.
- Determinism: temperature=0, fixed seed where supported.
- Cost / token logging per call.
- No prompt-injection paths (user content stays in the user message slot).

Step 5 lands the abstract base + an `OfflineLLMClient` that returns UNRESOLVED for
every skill call. Step 6 adds Gemini and Anthropic backends.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from kentro_server.store.models import FieldWriteRow

logger = logging.getLogger(__name__)


class SkillResolverDecision(BaseModel):
    """Structured output returned by `LLMClient.run_skill_resolver`.

    The LLM is asked to choose one candidate's `value_json` (or signal that it cannot
    decide). The Pydantic shape forces the LLM into a discrete answer.
    """

    model_config = ConfigDict(frozen=True)

    chosen_value_json: str | None
    """The selected candidate's `value_json`, or `None` if the LLM cannot decide."""

    reason: str
    """Always present. When `chosen_value_json` is set, this is the LLM's reasoning;
    when `None`, this is the unresolved-reason returned to the caller."""


class LLMClient(ABC):
    """Abstract LLM seam used by SkillResolver and (later) NL → RuleSet parsing."""

    @abstractmethod
    def run_skill_resolver(
        self,
        *,
        prompt: str,
        candidates: "list[FieldWriteRow]",
        model: str | None,
    ) -> SkillResolverDecision: ...


class OfflineLLMClient(LLMClient):
    """v0 default — returns UNRESOLVED for every skill call.

    Active when `KENTRO_LLM_OFFLINE=1` or no real backend has been configured. Step 6
    swaps in Gemini/Anthropic backends behind the same interface.
    """

    _UNAVAILABLE_REASON = "LLM client offline (no backend configured or KENTRO_LLM_OFFLINE=1)"

    def run_skill_resolver(
        self,
        *,
        prompt: str,
        candidates: "list[FieldWriteRow]",
        model: str | None,
    ) -> SkillResolverDecision:
        logger.info(
            "OfflineLLMClient.run_skill_resolver — %d candidates, prompt=%r, model=%r → UNRESOLVED",
            len(candidates), prompt[:80], model,
        )
        return SkillResolverDecision(
            chosen_value_json=None,
            reason=self._UNAVAILABLE_REASON,
        )


__all__ = [
    "LLMClient",
    "OfflineLLMClient",
    "SkillResolverDecision",
]
