from __future__ import annotations

import pytest

from inari.client_trust import (
    ClientTrustSigningKeyStore,
    ClientTrustSigningKeyUnavailable,
)
from inari.security.secrets import MemorySecretStore


def test_signing_key_is_generated_once_and_persisted() -> None:
    secrets = MemorySecretStore()
    first = ClientTrustSigningKeyStore(secrets).get_or_create()
    second = ClientTrustSigningKeyStore(secrets).get_or_create()

    assert first == second
    assert set(first) == {"kty", "crv", "x", "d"}
    assert first["kty"] == "OKP"
    assert first["crv"] == "Ed25519"


def test_signing_key_rejects_corrupt_or_mismatched_material() -> None:
    secrets = MemorySecretStore()
    secrets.set_secret(
        "inari/client-trust/access-token-signing-key/v1",
        '{"crv":"Ed25519","d":"bad","kty":"OKP","x":"bad"}',
    )

    with pytest.raises(ClientTrustSigningKeyUnavailable):
        ClientTrustSigningKeyStore(secrets).get_or_create()
