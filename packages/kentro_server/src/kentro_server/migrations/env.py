"""Alembic environment for kentro-server.

We have **one shared schema across every tenant** — every tenant DB has identical
SQLModel tables. So the migrations directory is shared (single `versions/` for
all tenants) and `env.py` simply iterates every tenant DB under `state_dir` and
runs the same upgrade/downgrade against each.

Two modes:

1. **CLI iteration mode (default).** `alembic upgrade head` with no overrides
   reads `KENTRO_STATE_DIR` (default `kentro_state`), discovers every
   `<state_dir>/<tenant>/state.sqlite`, and runs migrations against each.
   `task migrate` wraps this.

2. **Single-URL mode.** Pass `-x url=sqlite:///path/to/state.sqlite` to point
   at one DB. Used by the boot guard (`is_drift()`) to check a single tenant
   without hitting the registry.

For autogenerate to work, `target_metadata` must be `SQLModel.metadata` AFTER
all model modules have been imported (so the tables register with the metadata
object). Importing `kentro_server.store.models` is the side-effect we rely on.
"""

import logging
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Side-effect import: registers every table on `SQLModel.metadata`.
from kentro_server.store import models  # noqa: F401

logger = logging.getLogger("alembic.env.kentro")

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def _discover_tenant_db_urls() -> list[str]:
    """Find every `<state_dir>/<tenant>/state.sqlite` and return SQLite URLs.

    Empty result is legal — first-boot before any tenant DB exists. Caller
    treats this as "nothing to migrate" rather than an error.
    """
    state_dir_str = os.environ.get("KENTRO_STATE_DIR", "kentro_state")
    state_dir = Path(state_dir_str).resolve()
    if not state_dir.is_dir():
        return []
    urls: list[str] = []
    for child in sorted(state_dir.iterdir()):
        sqlite_path = child / "state.sqlite"
        if child.is_dir() and sqlite_path.exists():
            urls.append(f"sqlite:///{sqlite_path}")
    return urls


def _run_migrations_for_url(url: str) -> None:
    """Run `context.run_migrations()` against a single SQLite URL."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()
    logger.info("migrations applied to %s", url)


def run_migrations_offline() -> None:
    """Offline mode: emit SQL to stdout. Useful for `alembic upgrade --sql`."""
    url = (context.get_x_argument(as_dictionary=True).get("url")) or config.get_main_option(
        "sqlalchemy.url"
    )
    if not url:
        urls = _discover_tenant_db_urls()
        if not urls:
            logger.warning("no tenant DBs found under KENTRO_STATE_DIR; nothing to emit")
            return
        url = urls[0]  # offline mode is for inspection — emit against the first one
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online mode: real connection(s).

    If `-x url=...` is passed, run against that one URL.
    Otherwise discover every tenant DB and run each in turn.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    explicit_url = x_args.get("url")

    if explicit_url:
        _run_migrations_for_url(explicit_url)
        return

    urls = _discover_tenant_db_urls()
    if not urls:
        logger.info("no tenant DBs found under KENTRO_STATE_DIR; nothing to migrate")
        return

    for url in urls:
        _run_migrations_for_url(url)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
