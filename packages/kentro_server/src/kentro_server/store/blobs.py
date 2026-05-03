"""Blob storage abstraction.

Filesystem implementation for dev / Colab / GCP-VM-with-disk. The S3 path is left as
a stubbed protocol so Step 12's hosted-demo work can drop in a managed-disk-backed
implementation without changing call sites.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class BlobStore(ABC):
    """Opaque byte-content storage keyed by string keys.

    Keys are relative paths within the store (e.g. `"<uuid>.md"`). Callers — the
    `Document` row in particular — are responsible for choosing and remembering the
    key; the store does not assign or alter keys.
    """

    @abstractmethod
    def put(self, key: str, content: bytes) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class FilesystemBlobStore(BlobStore):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Defense-in-depth: refuse keys that escape the root via "../"
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError(f"blob key escapes store root: {key!r}")
        return path

    def put(self, key: str, content: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError:
            logger.warning("blob delete: key %r not found, no-op", key)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()
