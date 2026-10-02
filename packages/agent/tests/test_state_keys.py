import base64
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from inari.security.secrets import MemorySecretStore
from inari.security.state_keys import (
    STATE_SIGNING_KEY_SECRET,
    AgentStateSigningKeyService,
    AgentStateSigningKeyUnavailable,
)


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_state_key_survives_restart_and_signs_canonical_attached_jws() -> None:
    store = MemorySecretStore()
    first = AgentStateSigningKeyService(store)
    public_jwk = first.public_jwk()
    restarted = AgentStateSigningKeyService(store)
    state = {"state_version": 3, "state": "outcome_unknown", "print_job_id": "job_1"}

    signed = restarted.sign(state)
    protected, payload, signature = signed.split(".")

    assert restarted.public_jwk() == public_jwk
    assert set(public_jwk) == {"kty", "crv", "alg", "use", "kid", "x"}
    assert json.loads(_decode(protected)) == {
        "alg": "EdDSA",
        "kid": public_jwk["kid"],
        "typ": "application/inari-agent-state+jws",
    }
    assert _decode(payload) == rfc8785.dumps(state)
    Ed25519PublicKey.from_public_bytes(_decode(public_jwk["x"])).verify(
        _decode(signature), f"{protected}.{payload}".encode("ascii")
    )


def test_concurrent_services_use_one_persisted_state_identity() -> None:
    store = MemorySecretStore()
    with ThreadPoolExecutor(max_workers=8) as executor:
        keys = list(
            executor.map(
                lambda _: AgentStateSigningKeyService(store).public_jwk(), range(32)
            )
        )
    assert all(key == keys[0] for key in keys)
    assert AgentStateSigningKeyService(store).public_jwk() == keys[0]


@pytest.mark.parametrize(
    "encoded", ["", "invalid", "A" * 43 + "=", "A" * 42 + "B", "x " * 16]
)
def test_corrupt_state_key_is_never_replaced(encoded: str) -> None:
    store = MemorySecretStore()
    store.set_secret(STATE_SIGNING_KEY_SECRET, encoded)
    with pytest.raises(AgentStateSigningKeyUnavailable):
        AgentStateSigningKeyService(store).public_jwk()
    assert store.get_secret(STATE_SIGNING_KEY_SECRET) == encoded


@pytest.mark.parametrize("failure", ["read", "write", "readback"])
def test_storage_failure_does_not_return_an_ephemeral_key(failure: str) -> None:
    class BrokenStore(MemorySecretStore):
        def get_secret(self, key: str) -> str | None:
            if failure == "read":
                raise RuntimeError("private diagnostic")
            return super().get_secret(key)

        def set_secret(self, key: str, value: str) -> None:
            if failure == "write":
                raise RuntimeError(value)

    with pytest.raises(AgentStateSigningKeyUnavailable) as raised:
        AgentStateSigningKeyService(BrokenStore()).public_jwk()
    assert str(raised.value) == "The Agent State signing key is unavailable."
    assert raised.value.__suppress_context__
