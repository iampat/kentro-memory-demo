"""CachingProvider — disk-backed structured-output cache, sitting under the prompt layer.

Wraps any inner `Provider`. The cache key is `sha256` of a canonical JSON of:
- the model name,
- the response Pydantic class qualname,
- the system prompt (rendered, including any skill markdown),
- the user payload (rendered),
- the structural knobs `max_tokens` and `max_retries`.

Because this layer sits **under** prompt-building (`DefaultLLMClient` formats
the prompt then calls `Provider.complete(...)`), the cache key naturally
includes everything sent to the LLM. Editing a `SKILL.md` changes the system
prompt → changes the key → forces a fresh inner call. There is no separate
"method → skill file" lookup, and no hidden input the cache could miss.

Cache files live at `<cache_dir>/<sha256>.json`, content `{"response": {...}}`.
The cache is content-addressed; cross-tenant collisions only happen if inputs
are byte-identical, which by construction means the same answer.

Per-process counters track `hits` and `inner_calls`; `hit_rate = hits / total`.
A toggle (`enabled`) lets perf measurement bypass the cache without removing files.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from kentro_server.skills.provider import Provider

logger = logging.getLogger(__name__)

_TModel = TypeVar("_TModel", bound=BaseModel)


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


class CachingProvider(Provider):
    """Disk cache wrapper around any `Provider`.

    Construct via `make_llm_client(settings)` rather than directly so caching is
    consistently configured across the process. Tests construct directly with a
    fake `Provider` to exercise the cache surface.
    """

    def __init__(
        self,
        *,
        inner: Provider,
        cache_dir: Path,
        enabled: bool = True,
    ) -> None:
        self.inner = inner
        self.cache_dir = cache_dir
        self.enabled = enabled
        self.stats = CacheStats()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_model: type[_TModel],
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> _TModel:
        cache_key = self._fingerprint(
            model=model,
            system=system,
            user=user,
            response_class=response_model.__name__,
            max_tokens=max_tokens,
            max_retries=max_retries,
        )
        cached = self._read(cache_key, response_model)
        if cached is not None:
            self.stats.hits += 1
            self._log_event("hit", cache_key, model)
            return cached
        result = self.inner.complete(
            model=model,
            system=system,
            user=user,
            response_model=response_model,
            max_tokens=max_tokens,
            max_retries=max_retries,
        )
        self.stats.inner_calls += 1
        self._log_event("miss", cache_key, model)
        self._write(cache_key, result)
        return result

    # --- Cache I/O ---

    def _fingerprint(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_class: str,
        max_tokens: int,
        max_retries: int,
    ) -> str:
        canonical = json.dumps(
            {
                "method": "complete",
                "model": model,
                "response_class": response_class,
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "max_retries": max_retries,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read(self, key: str, model_cls: type[_TModel]) -> _TModel | None:
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

    def _write(self, key: str, response: BaseModel) -> None:
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
            kind,
            key[:12],
            model,
            self.stats.render(),
        )


__all__ = ["CacheStats", "CachingProvider"]
