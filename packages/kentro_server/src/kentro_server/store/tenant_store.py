"""Per-tenant on-disk store + `TenantRegistry` driven from `tenants.json`.

Layout:
    <state_root>/
        tenants.json              # source of truth; loaded at startup
        <tenant_id>/
            state.sqlite          # SQLModel tables for this tenant
            docs/                 # source markdown blobs
            witchcraft/           # Witchcraft index files (mydb.sqlite, etc.)
        .llm_cache/               # global LLM cache

Tenant IDs are validated against `TENANT_ID_REGEX` and the resolved tenant directory
must stay under `state_root` — this prevents `tenant_id="../etc"` from escaping.

On first start, if `tenants.json` is missing, a single-tenant default is written so a
fresh clone "just works" for local dev.
"""

import json
import logging
import re
import shutil
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

# Importing models here registers them with SQLModel.metadata so create_all picks them up.
from kentro_server.store import models  # noqa: F401
from kentro_server.store.blobs import FilesystemBlobStore
from kentro_server.store.tenant_config import AgentConfig, TenantConfig, TenantsConfig

logger = logging.getLogger(__name__)

TENANT_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]+$")
DEFAULT_LOCAL_TENANT = TenantConfig(
    id="local",
    display_name="Local Dev",
    agents=(
        # ingestion_agent is admin so the demo's seed flow can register schemas
        # and apply rules. sales / customer_service are non-admin so the
        # Sales-vs-CS access boundary is real (they cannot re-grant themselves).
        AgentConfig(
            id="ingestion_agent",
            api_key="local-ingestion-do-not-share",
            is_admin=True,
        ),
        AgentConfig(id="sales", api_key="local-sales-do-not-share"),
        AgentConfig(id="customer_service", api_key="local-cs-do-not-share"),
    ),
)


def _validate_tenant_id(tenant_id: str, root_dir: Path) -> Path:
    """Reject malformed or path-escaping tenant IDs. Returns the resolved tenant dir."""
    if not TENANT_ID_REGEX.fullmatch(tenant_id):
        raise ValueError(f"invalid tenant_id {tenant_id!r}: must match {TENANT_ID_REGEX.pattern}")
    root_resolved = root_dir.resolve()
    tenant_dir = (root_dir / tenant_id).resolve()
    if not tenant_dir.is_relative_to(root_resolved):
        raise ValueError(f"tenant_id {tenant_id!r} resolves outside state root")
    return tenant_dir


class TenantStore:
    """The per-tenant SQLite engine + blob root + Witchcraft directory."""

    def __init__(self, root_dir: Path, tenant_id: str) -> None:
        self.tenant_dir = _validate_tenant_id(tenant_id, root_dir)
        self.tenant_id = tenant_id
        self.root_dir = root_dir
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


class TenantRegistry:
    """Eagerly-constructed registry of per-tenant stores driven by `tenants.json`.

    Auth surface: `by_api_key(key)` resolves the bearer key to a `(TenantStore, agent_id)`
    pair in one step. The same key authenticates both the tenant and the agent identity.
    """

    def __init__(self, root_dir: Path, config: TenantsConfig) -> None:
        self.root_dir = root_dir
        self.config = config
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, TenantStore] = {}
        self._key_to_pair: dict[str, tuple[str, str]] = {}
        self._admin_keys: set[str] = set()
        for tcfg in config.tenants:
            # Construct eagerly so misconfigured IDs fail fast, not on first request.
            self._stores[tcfg.id] = TenantStore(root_dir, tcfg.id)
            for acfg in tcfg.agents:
                self._key_to_pair[acfg.api_key] = (tcfg.id, acfg.id)
                if acfg.is_admin:
                    self._admin_keys.add(acfg.api_key)

    @classmethod
    def from_paths(cls, *, state_dir: Path, config_path: Path) -> "TenantRegistry":
        """Load tenants from `config_path`, auto-creating a default if absent.

        `config_path` is intentionally separate from `state_dir`: the tenants file is
        a config artifact (tracked in git), while per-tenant subdirectories under
        `state_dir` are runtime state (gitignored).
        """
        if not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            default = TenantsConfig(tenants=(DEFAULT_LOCAL_TENANT,))
            config_path.write_text(default.model_dump_json(indent=2) + "\n")
            logger.info(
                "created default tenants.json at %s with one 'local' tenant + 3 agents",
                config_path,
            )
            config = default
        else:
            data = json.loads(config_path.read_text())
            config = TenantsConfig.model_validate(data)
        return cls(state_dir, config)

    def get(self, tenant_id: str) -> TenantStore:
        store = self._stores.get(tenant_id)
        if store is None:
            raise KeyError(f"unknown tenant_id {tenant_id!r}; configured: {sorted(self._stores)}")
        return store

    def by_api_key(self, api_key: str) -> tuple[TenantStore, str, bool]:
        """Resolve the bearer key to (store, agent_id, is_admin). Raises KeyError on miss."""
        pair = self._key_to_pair.get(api_key)
        if pair is None:
            raise KeyError("unknown api_key")
        tenant_id, agent_id = pair
        return self.get(tenant_id), agent_id, api_key in self._admin_keys

    def agents_for(self, tenant_id: str) -> tuple[AgentConfig, ...]:
        for t in self.config.tenants:
            if t.id == tenant_id:
                return t.agents
        raise KeyError(f"unknown tenant_id {tenant_id!r}")

    def known_tenants(self) -> list[str]:
        return sorted(self._stores)

    def reset(self, tenant_id: str) -> None:
        """Wipe a tenant's state from disk; reconstruct so the tenant remains usable."""
        # Dispose cached engine first so the rmtree doesn't trip a held SQLite handle.
        store = self._stores.pop(tenant_id, None)
        if store is not None:
            store.dispose()
        # Rebuild path under the validated root rather than using the cached store's
        # path, so a stale cache entry can't trick us into removing the wrong dir.
        tenant_dir = _validate_tenant_id(tenant_id, self.root_dir)
        if tenant_dir.exists():
            shutil.rmtree(tenant_dir)
            logger.info("reset tenant %s: removed %s", tenant_id, tenant_dir)
        # If the tenant is in config, bring it back online empty.
        if tenant_id in {t.id for t in self.config.tenants}:
            self._stores[tenant_id] = TenantStore(self.root_dir, tenant_id)

    def dispose_all(self) -> None:
        for store in list(self._stores.values()):
            store.dispose()
        self._stores.clear()
