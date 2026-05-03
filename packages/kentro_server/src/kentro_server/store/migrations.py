"""Alembic glue — migrate / check-drift APIs called by tenant_store + boot guard.

Why this module exists: kentro-server needs three things from Alembic:

1. **Per-tenant upgrade.** Called from `TenantStore.__init__`; replaces the old
   `SQLModel.metadata.create_all(engine)` so a fresh tenant DB lands at the
   current head, not at "whatever metadata says today".
2. **Drift detection.** Called from the FastAPI lifespan; walks every existing
   tenant DB and reports whether any of them is behind head. The lifespan uses
   this to print a clear "run `task migrate`" message and refuse to start
   instead of failing later in the request path.
3. **CLI entry-point.** `task migrate` and `task migrate:revision` invoke the
   stock `alembic` binary directly (with our `alembic.ini`); this module is the
   programmatic equivalent so the boot path doesn't shell out.

Single shared `versions/` directory under `migrations/` — every tenant has the
same schema by design; per-tenant migration scripts would be wrong.
"""

import argparse
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

logger = logging.getLogger(__name__)

# `alembic.ini` lives alongside this package. Resolved at import time so the
# path is stable regardless of cwd (server can be launched from anywhere).
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _PACKAGE_ROOT / "alembic.ini"


def _config_for(url: str) -> Config:
    """Build an Alembic Config pinned to one SQLite URL.

    We use `-x url=...` semantics (set via `cmd_opts`-equivalent attribute) so
    `env.py`'s single-URL branch fires. This avoids env.py's tenant-discovery
    walk when we already know exactly which DB to migrate.
    """
    cfg = Config(str(_ALEMBIC_INI))
    # Mimic `alembic -x url=<url>`. Alembic's CLI parses `-x` into the namespace
    # `cmd_opts.x`; we attach the same shape here so `env.py` sees it.
    cfg.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    return cfg


def upgrade_to_head(sqlite_path: Path) -> None:
    """Bring one tenant DB to the current head revision. Idempotent.

    Called from `TenantStore.__init__` so every tenant reaches the same schema
    version regardless of when its DB was first created.
    """
    url = f"sqlite:///{sqlite_path}"
    cfg = _config_for(url)
    command.upgrade(cfg, "head")


def current_head() -> str:
    """Return the head revision id from the migrations directory.

    Used by the boot-guard to compare against per-tenant `alembic_version`.
    Cached implicitly by ScriptDirectory — cheap to call repeatedly.
    """
    cfg = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:
        # No revisions defined yet — only happens during alembic init / first
        # `revision --autogenerate`. The boot guard treats this as "no schema
        # to enforce" and lets startup proceed.
        return ""
    return head


def current_revision_for(sqlite_path: Path) -> str | None:
    """Return the revision currently stamped on a tenant DB, or None if unstamped.

    Unstamped means the DB exists but `alembic_version` table is missing —
    indicates a pre-Alembic state that needs `task reset` (we don't try to
    auto-stamp because there's no way to know what "pre-Alembic schema" looked
    like for a given developer's local DB).
    """
    if not sqlite_path.exists():
        return None
    engine = create_engine(f"sqlite:///{sqlite_path}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            return ctx.get_current_revision()
    finally:
        engine.dispose()


def detect_drift(state_dir: Path) -> list[tuple[Path, str | None]]:
    """Return `[(path, current_rev), ...]` for tenant DBs not at head.

    Empty list means "all tenants at head, safe to start". A non-empty list
    is what the boot guard renders into a "tenants behind head: ..., run
    `task migrate`" error message.

    `current_rev = None` in the returned list means the DB exists but is
    unstamped — needs `task reset` (it predates Alembic adoption).
    """
    head = current_head()
    if not head:
        return []
    behind: list[tuple[Path, str | None]] = []
    if not state_dir.is_dir():
        return []
    for child in sorted(state_dir.iterdir()):
        sqlite_path = child / "state.sqlite"
        if not (child.is_dir() and sqlite_path.exists()):
            continue
        rev = current_revision_for(sqlite_path)
        if rev != head:
            behind.append((sqlite_path, rev))
    return behind
