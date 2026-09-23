"""Interface for storing secrets (passwords, vault keys) in the OS keychain.

Status: **not implemented**. lilx 0.1 does not save passwords at all, so no
secret is ever written to disk. Future implementations:

* macOS  – Keychain Services (Security.framework)
* Linux  – Secret Service API over D-Bus (GNOME Keyring, KWallet)
* Windows – Credential Manager / DPAPI

Rule for every implementation: secrets never touch lilx files in plain text.
The encrypted vault key (see ``docs/ARCHITECTURE.md``) will also live here,
which is what makes crypto-erasure possible: deleting the key from the
keychain makes the vault unrecoverable.
"""

from __future__ import annotations

from typing import Protocol


class SecretStore(Protocol):
    def get(self, service: str, account: str) -> bytes | None: ...

    def set(self, service: str, account: str, secret: bytes) -> None: ...

    def delete(self, service: str, account: str) -> None: ...


def system_secret_store() -> SecretStore | None:
    """Return the OS secret store, or ``None`` when no backend is available (always, for now)."""
    return None
