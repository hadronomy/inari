from __future__ import annotations

import pytest
from keyring.errors import KeyringError

from inari.security.secrets import (
    MemorySecretStore,
    ProtectedSecretStore,
    ProtectedSecretStoreUnavailable,
)


class UnavailableSecretStore:
    def get_secret(self, key: str) -> str | None:
        del key
        raise KeyringError("credential store unavailable")

    def set_secret(self, key: str, value: str) -> None:
        del key, value
        raise KeyringError("credential store unavailable")

    def delete_secret(self, key: str) -> None:
        del key
        raise KeyringError("credential store unavailable")


def test_managed_secret_store_fails_closed() -> None:
    store = ProtectedSecretStore(primary=UnavailableSecretStore())

    with pytest.raises(ProtectedSecretStoreUnavailable):
        store.set_secret("enrollment", "secret")


def test_standalone_fallback_is_used_only_when_keyring_fails() -> None:
    fallback = MemorySecretStore()
    store = ProtectedSecretStore(
        primary=UnavailableSecretStore(),
        fallback=fallback,
    )

    store.set_secret("pairing", "secret")

    assert fallback.get_secret("pairing") == "secret"


def test_successful_keyring_write_leaves_no_plaintext_copy() -> None:
    primary = MemorySecretStore()
    fallback = MemorySecretStore()
    fallback.set_secret("identity", "stale-copy")
    store = ProtectedSecretStore(primary=primary, fallback=fallback)

    store.set_secret("identity", "protected")

    assert primary.get_secret("identity") == "protected"
    assert fallback.get_secret("identity") is None


def test_legacy_fallback_secret_migrates_to_the_keyring() -> None:
    primary = MemorySecretStore()
    fallback = MemorySecretStore()
    fallback.set_secret("identity", "legacy-secret")
    store = ProtectedSecretStore(primary=primary, fallback=fallback)

    assert store.get_secret("identity") == "legacy-secret"
    assert primary.get_secret("identity") == "legacy-secret"
    assert fallback.get_secret("identity") is None
