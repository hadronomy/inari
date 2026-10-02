from __future__ import annotations

import pytest

from inari.security.secrets import MemorySecretStore
from inari.spool.keys import (
    SpoolRootKeyAlreadyExists,
    SpoolRootKeyService,
    SpoolRootKeyUnavailable,
)


class FailingSecretStore:
    def __init__(self, operation: str) -> None:
        self.operation = operation

    def get_secret(self, key: str) -> str | None:
        del key
        if self.operation == "get":
            raise RuntimeError("backend detail must not escape")
        return None

    def set_secret(self, key: str, value: str) -> None:
        del key, value
        if self.operation == "set":
            raise RuntimeError("backend detail must not escape")

    def delete_secret(self, key: str) -> None:
        del key
        if self.operation == "delete":
            raise RuntimeError("backend detail must not escape")


class StickySecretStore(MemorySecretStore):
    def delete_secret(self, key: str) -> None:
        del key


class TrackingSecretStore(MemorySecretStore):
    @property
    def secret_count(self) -> int:
        return len(self._values)


class CorruptingSecretStore(MemorySecretStore):
    def set_secret(self, key: str, value: str) -> None:
        super().set_secret(key, value[::-1])


def test_write_and_read_use_a_versioned_strict_base64url_secret() -> None:
    store = MemorySecretStore()
    root_keys = SpoolRootKeyService(store)
    key = bytes(range(32))

    root_keys.write(7, key)

    assert root_keys.read(7) == key
    assert store.get_secret("inari/device-spool/root-key/v7") == (
        "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    )


def test_invalid_secret_is_unavailable() -> None:
    store = MemorySecretStore()
    store.set_secret("inari/device-spool/root-key/v1", "not-a-key")

    with pytest.raises(SpoolRootKeyUnavailable) as error:
        SpoolRootKeyService(store).read(1)

    assert str(error.value) == "Protected spool key storage is unavailable."


@pytest.mark.parametrize(
    ("operation", "invoke"),
    [("get", "read"), ("set", "write"), ("delete", "delete")],
)
def test_backend_failures_are_fixed_content_free_errors(
    operation: str, invoke: str
) -> None:
    root_keys = SpoolRootKeyService(FailingSecretStore(operation))
    with pytest.raises(SpoolRootKeyUnavailable) as error:
        getattr(root_keys, invoke)(1, bytes(32)) if invoke == "write" else getattr(
            root_keys, invoke
        )(1)

    assert str(error.value) == "Protected spool key storage is unavailable."
    assert "backend detail" not in repr(error.value)


def test_self_test_cleans_up_probe_secret() -> None:
    store = TrackingSecretStore()
    root_keys = SpoolRootKeyService(store)

    root_keys.self_test()

    assert store.secret_count == 0


def test_self_test_fails_when_cleanup_does_not_remove_probe() -> None:
    with pytest.raises(SpoolRootKeyUnavailable):
        SpoolRootKeyService(StickySecretStore()).self_test()


def test_generate_current_is_explicit_and_persists_key() -> None:
    store = MemorySecretStore()

    key = SpoolRootKeyService(store).generate_current(3)

    assert len(key) == 32
    assert SpoolRootKeyService(store).read(3) == key


def test_write_does_not_overwrite_an_existing_version() -> None:
    store = MemorySecretStore()
    root_keys = SpoolRootKeyService(store)
    original = bytes(range(32))
    root_keys.write(4, original)

    with pytest.raises(SpoolRootKeyAlreadyExists):
        root_keys.write(4, bytes(reversed(range(32))))

    assert root_keys.read(4) == original


def test_generate_current_does_not_overwrite_an_existing_version() -> None:
    store = MemorySecretStore()
    root_keys = SpoolRootKeyService(store)
    original = bytes(range(32))
    root_keys.write(4, original)

    with pytest.raises(SpoolRootKeyAlreadyExists):
        root_keys.generate_current(4)

    assert root_keys.read(4) == original


def test_entropy_failure_is_a_fixed_unavailable_error(mocker) -> None:
    mocker.patch(
        "inari.spool.keys.token_bytes",
        side_effect=RuntimeError("entropy provider detail must not escape"),
    )

    with pytest.raises(SpoolRootKeyUnavailable) as error:
        SpoolRootKeyService(MemorySecretStore()).generate_current(1)

    assert str(error.value) == "Protected spool key storage is unavailable."
    assert "entropy provider detail" not in repr(error.value)


def test_write_rejects_a_read_back_mismatch() -> None:
    with pytest.raises(SpoolRootKeyUnavailable):
        SpoolRootKeyService(CorruptingSecretStore()).write(1, bytes(range(32)))


def test_invalid_key_and_version_are_rejected_before_storage() -> None:
    store = TrackingSecretStore()
    root_keys = SpoolRootKeyService(store)

    with pytest.raises(ValueError):
        root_keys.write(1, b"short")
    with pytest.raises(ValueError):
        root_keys.read(0)
    with pytest.raises(ValueError):
        root_keys.read(True)
    with pytest.raises(ValueError):
        root_keys.read(0x1_0000_0000)
    assert store.secret_count == 0
