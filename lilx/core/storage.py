"""Storage backends for small persistent documents (settings, download list, ...).

Every module that persists documents goes through :class:`StorageBackend`
instead of touching files directly. This is the seam for the planned
encrypted vault: an ``EncryptedVaultStorage`` implementing the same interface
can replace :class:`PlainFileStorage` without changing its callers.

Current state: **no encryption is implemented**. :class:`PlainFileStorage`
writes plain JSON files with owner-only permissions (0600). See
``docs/ARCHITECTURE.md`` for the vault / crypto-erasure plan.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_VALID_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class StorageError(Exception):
    """Raised when a document cannot be read or written."""


class StorageBackend(ABC):
    """Key/value storage of named documents."""

    #: True only for backends that encrypt data at rest.
    encrypted: bool = False

    @abstractmethod
    def read(self, name: str) -> bytes | None:
        """Return the document bytes, or ``None`` if it does not exist."""

    @abstractmethod
    def write(self, name: str, data: bytes) -> None:
        """Atomically replace the document."""

    @abstractmethod
    def delete(self, name: str) -> None:
        """Remove the document if it exists."""

    def read_json(self, name: str) -> Any | None:
        raw = self.read(name)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageError(f"document {name!r} is corrupted: {exc}") from exc

    def write_json(self, name: str, value: Any) -> None:
        self.write(name, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


class PlainFileStorage(StorageBackend):
    """Unencrypted files inside a directory, written atomically with mode 0600."""

    encrypted = False

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        if not _VALID_NAME.match(name) or name in {".", ".."}:
            raise StorageError(f"invalid document name: {name!r}")
        return self._root / name

    def read(self, name: str) -> bytes | None:
        path = self._path(name)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StorageError(f"cannot read {path}: {exc}") from exc

    def write(self, name: str, data: bytes) -> None:
        path = self._path(name)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{name}.", dir=self._root)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.chmod(0o600)
            os.replace(tmp, path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError(f"cannot write {path}: {exc}") from exc

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"cannot delete {name!r}: {exc}") from exc
