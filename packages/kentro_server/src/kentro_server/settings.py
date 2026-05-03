"""kentro-server settings — loaded from `.env` and the process environment.

Field names match env var names (case-insensitive). Missing keys default per the
field declaration. All settings live here in one place so the factories and the
CLI commands consume a single typed object.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LLM provider keys ---
    anthropic_api_key: str | None = None
    google_api_key: str | None = None

    # --- LLM tier model selection ---
    # Provider is auto-detected from the model name prefix:
    #   "claude-*"  → Anthropic  (requires ANTHROPIC_API_KEY)
    #   "gemini-*"  → Google     (requires GOOGLE_API_KEY)
    kentro_llm_fast_model: str = "claude-haiku-4-5"
    kentro_llm_smart_model: str = "claude-sonnet-4-6"

    # --- LLM cache toggle (for performance measurement and demo recording) ---
    # When True, completed prompts are stored on disk and replayed on identical inputs.
    # When False, every call goes to the provider — useful for measuring hot-path latency.
    kentro_llm_cache_enabled: bool = True

    # --- On-disk state root ---
    # Per-tenant subdirectories live under this path; the LLM cache also lives here.
    kentro_state_dir: Path = Path("./kentro_state")

    # --- Server bind ---
    kentro_host: str = "127.0.0.1"
    kentro_port: int = 8000

    @property
    def llm_cache_dir(self) -> Path:
        return self.kentro_state_dir / ".llm_cache"


__all__ = ["Settings"]
