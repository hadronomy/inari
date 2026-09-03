from cryptography.hazmat.primitives import serialization

from inari.security.dispatch_keys import (
    DISPATCH_PRIVATE_KEY_SECRET,
    DispatchEncryptionKeyService,
)
from inari.security.secrets import MemorySecretStore


def test_dispatch_key_is_stable_and_keeps_private_material_protected() -> None:
    store = MemorySecretStore()
    first = DispatchEncryptionKeyService(store).get_or_create()
    second = DispatchEncryptionKeyService(store).get_or_create()

    assert second.key_id == first.key_id
    assert second.public_key_base64url == first.public_key_base64url
    assert second.private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ) == first.private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    assert store.get_secret(DISPATCH_PRIVATE_KEY_SECRET) != first.public_key_base64url


def test_dispatch_key_rejects_corrupt_stored_material() -> None:
    store = MemorySecretStore()
    store.set_secret(DISPATCH_PRIVATE_KEY_SECRET, "invalid")

    try:
        DispatchEncryptionKeyService(store).get_or_create()
    except ValueError as error:
        assert str(error) == "The managed dispatch private key is invalid."
    else:
        raise AssertionError("corrupt managed dispatch key was accepted")
