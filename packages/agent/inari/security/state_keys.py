from __future__ import annotations

import base64
from hashlib import sha256
from threading import Lock
from typing import Mapping

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .secrets import SecretStore


STATE_SIGNING_KEY_SECRET = "agent_state_ed25519_private_key_v1"
STATE_JWS_TYPE = "application/inari-agent-state+jws"
_KEY_LOCK = Lock()


class AgentStateSigningKeyUnavailable(RuntimeError):
    """The protected Agent State signing key cannot be loaded or stored."""

    def __init__(self) -> None:
        super().__init__("The Agent State signing key is unavailable.")


class AgentStateSigningKeyService:
    """Keep Agent State signatures separate from transport and Client Grant keys."""

    def __init__(self, secret_store: SecretStore) -> None:
        self._secret_store = secret_store
        self._private_key: Ed25519PrivateKey | None = None

    def public_jwk(self) -> dict[str, str]:
        public_bytes = self._key().public_key().public_bytes_raw()
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "alg": "EdDSA",
            "use": "sig",
            "kid": f"agent_state_{sha256(public_bytes).hexdigest()}",
            "x": _base64url(public_bytes),
        }

    def sign(self, state: Mapping[str, object]) -> str:
        protected = _base64url(
            rfc8785.dumps(
                {
                    "alg": "EdDSA",
                    "kid": self.public_jwk()["kid"],
                    "typ": STATE_JWS_TYPE,
                }
            )
        )
        payload = _base64url(rfc8785.dumps(dict(state)))
        signing_input = f"{protected}.{payload}".encode("ascii")
        signature = _base64url(self._key().sign(signing_input))
        return f"{protected}.{payload}.{signature}"

    def _key(self) -> Ed25519PrivateKey:
        with _KEY_LOCK:
            if self._private_key is not None:
                return self._private_key
            try:
                encoded = self._secret_store.get_secret(STATE_SIGNING_KEY_SECRET)
                if encoded is None:
                    generated = Ed25519PrivateKey.generate()
                    encoded = _base64url(generated.private_bytes_raw())
                    self._secret_store.set_secret(STATE_SIGNING_KEY_SECRET, encoded)
                    if (
                        self._secret_store.get_secret(STATE_SIGNING_KEY_SECRET)
                        != encoded
                    ):
                        raise AgentStateSigningKeyUnavailable()
                private_bytes = base64.b64decode(
                    encoded + "=" * (-len(encoded) % 4),
                    altchars=b"-_",
                    validate=True,
                )
                if len(private_bytes) != 32 or _base64url(private_bytes) != encoded:
                    raise AgentStateSigningKeyUnavailable()
                self._private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
            except Exception:
                raise AgentStateSigningKeyUnavailable() from None
            return self._private_key


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
