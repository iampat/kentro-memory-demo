"""Per-tenant on-disk store.

Layout:
    <state_root>/
        <tenant_id>/
            state.sqlite          # SQLModel tables for this tenant
            docs/                  # source markdown blobs
            witchcraft/            # Witchcraft index files (mydb.sqlite, etc.)
                mydb.sqlite        # Witchcraft state
                assets -> ...      # symlink to global xtr.gguf (created in Step 6)

Each tenant gets its own SQLite engine; total isolation simplifies the "reset tenant"
demo command (`kentro-server reset-tenant <id>` — just rm -rf the dir).
"""

import logging
import shutil
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

# Importing models here registers them with SQLModel.metadata so create_all picks them up.
from kentro_server.store import models  # noqa: F401
from kentro_server.store.blobs import FilesystemBlobStore

logger = logging.getLogger(__name__)


class TenantStore:
    """The per-tenant SQLite engine + blob root + Witchcraft directory."""

    def __init__(self, root_dir: Path, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.root_dir = root_dir
        self.tenant_dir = root_dir / tenant_id
        self.docs_dir = self.tenant_dir / "docs"
        self.witchcraft_dir = self.tenant_dir / "witchcraft"
        self._sqlite_path = self.tenant_dir / "state.sqlite"

        self.tenant_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(exist_ok=True)
        self.witchcraft_dir.mkdir(exist_ok=True)

        self._engine = create_engine(
            f"sqlite:///{self._sqlite_path}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self._engine)

        self.blobs = FilesystemBlobStore(self.docs_dir)

    @property
    def engine(self) -> Engine:
        return self._engine

    def session(self) -> Session:
        return Session(self._engine)

    def dispose(self) -> None:
        self._engine.dispose()


class StoreRegistry:
    """Lazily-instantiated registry of per-tenant stores."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, TenantStore] = {}

    def get(self, tenant_id: str) -> TenantStore:
        store = self._stores.get(tenant_id)
        if store is None:
            store = TenantStore(self.root_dir, tenant_id)
            self._stores[tenant_id] = store
        return store

    def reset(self, tenant_id: str) -> None:
        """Wipe a tenant's state from disk and drop the cached engine.

        Used by `kentro-server reset-tenant <id>` between demo takes.
        """
        store = self._stores.pop(tenant_id, None)
        if store is not None:
            store.dispose()
        tenant_dir = self.root_dir / tenant_id
        if tenant_dir.exists():
            shutil.rmtree(tenant_dir)
            logger.info("reset tenant %s: removed %s", tenant_id, tenant_dir)

    def known_tenants(self) -> list[str]:
        """Tenant IDs with on-disk state (whether or not currently loaded)."""
        return sorted(p.name for p in self.root_dir.iterdir() if p.is_dir())
