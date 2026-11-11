"""Credential storage through the freedesktop Secret Service (KWallet, GNOME Keyring, KeePassXC).

There is deliberately no plaintext fallback: if no secure backend is available the
caller is told so and the key is kept in memory for the session only.
"""
from __future__ import annotations

import keyring
import keyring.errors
from keyring.backend import KeyringBackend

SERVICE = "prometheus"
ANTHROPIC_KEY = "anthropic_api_key"


class SecretStoreError(RuntimeError):
    pass


def default_collection_exists() -> bool | None:
    """Whether the Secret Service already has a default collection (wallet).

    Reading from a service without one makes it pop up a "create wallet" prompt and
    block until answered, so reads are skipped in that case. None means "could not tell".
    """
    try:
        import secretstorage
        from secretstorage.exceptions import SecretStorageException
    except ImportError:
        return None
    try:
        conn = secretstorage.dbus_init()
    except Exception:
        return None
    try:
        secretstorage.Collection(conn, "/org/freedesktop/secrets/aliases/default")
        return True
    except SecretStorageException:
        return False
    finally:
        conn.close()


class SecretStore:
    def __init__(self, backend: KeyringBackend | None = None):
        self._backend = backend
        self._session: dict[str, str] = {}

    def _kr(self) -> KeyringBackend:
        return self._backend or keyring.get_keyring()

    def availability(self) -> tuple[bool, str]:
        """(usable, human description of the backend or of why it is unusable)."""
        kr = self._kr()
        name = f"{type(kr).__module__}.{type(kr).__name__}"
        if getattr(kr, "priority", 0) <= 0 or "fail" in name.lower() or "null" in name.lower():
            return False, ("No Secret Service provider is running. Start KWallet, GNOME Keyring "
                           "or KeePassXC's Secret Service integration.")
        if "plaintext" in name.lower():
            return False, "Only a plaintext keyring backend is available; refusing to use it."
        members = getattr(kr, "backends", None) or [kr]
        desc = ", ".join(sorted({type(b).__module__.rsplit(".", 1)[-1] for b in members}))
        if self._backend is None and default_collection_exists() is False:
            desc += " (no wallet yet: your desktop will ask you to create one when a key is first saved)"
        return True, desc

    def get(self, name: str) -> str | None:
        if name in self._session:
            return self._session[name]
        if not self.availability()[0]:
            return None
        if self._backend is None and default_collection_exists() is False:
            return None     # nothing can be stored yet, and asking would raise a blocking prompt
        try:
            return self._kr().get_password(SERVICE, name)
        except keyring.errors.KeyringError as exc:
            raise SecretStoreError(f"Keyring read failed: {exc}") from exc

    def set(self, name: str, value: str) -> bool:
        """Store a secret. Returns True if persisted, False if held for this session only."""
        ok, _ = self.availability()
        if not ok:
            self._session[name] = value
            return False
        try:
            self._kr().set_password(SERVICE, name, value)
        except keyring.errors.KeyringError as exc:
            raise SecretStoreError(f"Keyring write failed: {exc}") from exc
        self._session.pop(name, None)
        return True

    def delete(self, name: str) -> None:
        self._session.pop(name, None)
        if not self.availability()[0]:
            return
        try:
            self._kr().delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass
        except keyring.errors.KeyringError as exc:
            raise SecretStoreError(f"Keyring delete failed: {exc}") from exc
