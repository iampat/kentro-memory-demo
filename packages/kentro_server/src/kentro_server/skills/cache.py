"""CachingLLMClient — disk-backed structured-output cache.

Wraps any inner `LLMClient`. Per-call key is `sha256` of a canonical JSON of:
- the model name actually used,
- the structured-output response model class qualname,
- the system + user payload.

Cache files live at `<cache_dir>/<sha256>.json`, content `{"response": {...}}`.
The cache is content-addressed; collisions across tenants only happen if inputs
are byte-identical, which by construction means the same answer.

Per-process counters track `hits` and `inner_calls`; `hit_rate = hits / total`.
A toggle (`enabled`) lets perf measurement bypass the cache without removing files.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kentro_server.skills.llm_client import (
    ExtractionResult,
    LLMClient,
    SkillResolverDecision,
)

if TYPE_CHECKING:
    from kentro_server.store.models import FieldWriteRow

logger = logging.getLogger(__name__)


@dataclass
class CacheStats:
    hits: int = 0
    inner_calls: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.inner_calls

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total > 0 else 0.0

    def render(self) -> str:
        return f"hits={self.hits} inner_calls={self.inner_calls} hit_rate={self.hit_rate:.1%}"


class CachingLLMClient(LLMClient):
    """Disk cache wrapper. Provider-agnostic.

    Construct via `make_llm_client(settings)` rather than directly so caching is
    consistently configured across the process.
    """

    def __init__(
        self,
        *,
        inner: LLMClient,
        cache_dir: Path,
        enabled: bool = True,
    ) -> None:
        self.inner = inner
        self.cache_dir = cache_dir
        self.enabled = enabled
        self.stats = CacheStats()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # --- High-level skills ---

    def run_skill_resolver(
        self,
        *,
        prompt: str,
        candidates: "list[FieldWriteRow]",
        model: str | None = None,
    ) -> SkillResolverDecision:
        # Resolve the actual model used so cache keys are stable across env changes.
        actual_model = model or _peek_attr(self.inner, "fast_model")
        cache_key = self._fingerprint(
            method="run_skill_resolver",
            model=actual_model,
            response_class="SkillResolverDecision",
            payload={
                "prompt": prompt,
                "candidates": [
                    {
                        "value_json": c.value_json,
                        "agent_id": c.written_by_agent_id,
                        "written_at": c.written_at.isoformat(),
                        "source_document_id": str(c.source_document_id) if c.source_document_id else None,
                    }
                    for c in candidates
                ],
            },
        )
        cached = self._read(cache_key, SkillResolverDecision)
        if cached is not None:
            self.stats.hits += 1
            self._log_event("hit", cache_key, actual_model)
            return cached
        result = self.inner.run_skill_resolver(prompt=prompt, candidates=candidates, model=model)
        self.stats.inner_calls += 1
        self._log_event("miss", cache_key, actual_model)
        self._write(cache_key, result)
        return result

    def extract_entities(
        self,
        *,
        document_text: str,
        registered_schemas: list,
        document_label: str | None = None,
        model: str | None = None,
    ) -> ExtractionResult:
        actual_model = model or _peek_attr(self.inner, "smart_model")
        # Fingerprint the full schemas, not just names — different field declarations
        # produce different extractor prompts and so different responses.
        schema_payload = [
            td.model_dump(mode="json") if hasattr(td, "model_dump") else td
            for td in registered_schemas
        ]
        cache_key = self._fingerprint(
            method="extract_entities",
            model=actual_model,
            response_class="ExtractionResult",
            payload={
                "document_text": document_text,
                "registered_schemas": schema_payload,
                "document_label": document_label,
            },
        )
        cached = self._read(cache_key, ExtractionResult)
        if cached is not None:
            self.stats.hits += 1
            self._log_event("hit", cache_key, actual_model)
            return cached
        result = self.inner.extract_entities(
            document_text=document_text,
            registered_schemas=registered_schemas,
            document_label=document_label,
            model=model,
        )
        self.stats.inner_calls += 1
        self._log_event("miss", cache_key, actual_model)
        self._write(cache_key, result)
        return result

    # --- Cache I/O ---

    def _fingerprint(self, *, method: str, model: str, response_class: str, payload: dict) -> str:
        canonical = json.dumps(
            {"method": method, "model": model, "response_class": response_class, "payload": payload},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read(self, key: str, model_cls):
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("llm-cache: corrupt entry at %s, ignoring", path)
            return None
        return model_cls.model_validate(data["response"])

    def _write(self, key: str, response) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        try:
            path.write_text(json.dumps({"response": response.model_dump(mode="json")}, indent=2))
        except OSError:
            logger.warning("llm-cache: failed to write %s", path, exc_info=True)

    def _log_event(self, kind: str, key: str, model: str) -> None:
        logger.info(
            "[llm] cache %s key=%s model=%s %s",
            kind, key[:12], model, self.stats.render(),
        )


def _peek_attr(obj: object, name: str) -> str:
    """Try to read an attribute (used to resolve the inner client's tier model name)."""
    return str(getattr(obj, name, "<unknown>"))


__all__ = ["CacheStats", "CachingLLMClient"]
