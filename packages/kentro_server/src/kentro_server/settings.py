"""kentro-server settings.

Field declarations live here; **default values for non-secret settings live in
`kentro.toml`** at the repo root (loaded via `TomlConfigSettingsSource`). Secrets
(`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) come from `.env` or process env only —
never from `kentro.toml`.

Resolution order (highest priority first):
  1. Constructor kwargs in code (tests use this).
  2. Environment variables (process env).
  3. `.env` file (gitignored).
  4. `kentro.toml` (committed config).
  5. Field defaults declared in this class (last-resort fallbacks).

To keep the layering predictable, do NOT put API keys in `kentro.toml`. The
`SettingsConfigDict(extra="ignore")` makes unknown keys in any source non-fatal.
"""

from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_DEFAULT_TOML = _REPO_ROOT / "kentro.toml"
_DEFAULT_ENV = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_DEFAULT_ENV),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        toml_file=str(_DEFAULT_TOML),
    )

    # --- LLM provider keys (secrets — .env or env var only, NEVER in kentro.toml) ---
    anthropic_api_key: str | None = None
    google_api_key: str | None = None

    # --- LLM tier model selection (default: kentro.toml) ---
    kentro_llm_fast_model: str = "claude-haiku-4-5"
    kentro_llm_smart_model: str = "claude-sonnet-4-6"

    # --- LLM cache toggle (default: kentro.toml) ---
    kentro_llm_cache_enabled: bool = True

    # --- On-disk state root (default: kentro.toml) ---
    kentro_state_dir: Path = Path("./kentro_state")

    # --- Tenants config file (default: kentro.toml) ---
    kentro_tenants_json: Path = Path("./tenants.json")

    # --- Server bind (default: kentro.toml) ---
    kentro_host: str = "127.0.0.1"
    kentro_port: int = 8000

    # --- Demo-key opt-in (codex 2026-05-03 critical finding fix) ---
    # The committed `tenants.json` ships with publicly-documented placeholder keys
    # (e.g. `local-ingestion-do-not-share`). The lifespan refuses to boot when any
    # of these keys is in tenants.json UNLESS `kentro_allow_demo_keys=True` is set
    # explicitly — usually via `KENTRO_ALLOW_DEMO_KEYS=true` in the environment.
    #
    # The default is `False` (safe) so a misconfigured deployment that forgets the
    # opt-out fails closed instead of accepting README-known bearer tokens. The
    # `task dev` Taskfile entry sets `KENTRO_ALLOW_DEMO_KEYS=true` for local dev.
    kentro_allow_demo_keys: bool = False

    @property
    def llm_cache_dir(self) -> Path:
        return self.kentro_state_dir / ".llm_cache"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Layer the settings sources. TOML sits below env/.env so secrets stay out of it."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


__all__ = ["Settings"]
