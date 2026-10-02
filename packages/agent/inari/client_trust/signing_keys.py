from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Final, Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


_SECRET_NAME: Final = "inari/client-trust/access-token-signing-key/v1"
_JWK_FIELDS: Final = frozenset({"kty", "crv", "x", "d"})


class SigningKeySecretStore(Protocol):
    def get_secret(self, key: str) -> str | None: ...

    def set_secret(self, key: str, value: str) -> None: ...


class ClientTrustSigningKeyUnavailable(RuntimeError):
    """The protected Client Trust signing key is missing or invalid."""

    def __init__(self) -> None:
        super().__init__("The Client Trust signing key is unavailable.")


class ClientTrustSigningKeyStore:
    """Keep the Client Grant signing identity in protected storage."""

    def __init__(self, secret_store: SigningKeySecretStore) -> None:
        self._secret_store = secret_store

    def get_or_create(self) -> Mapping[str, str]:
        encoded = self._read()
        if encoded is not None:
            return _decode(encoded)

        generated = _generate()
        self._write(_encode(generated))
        persisted = self._read()
        if persisted is None:
            raise ClientTrustSigningKeyUnavailable()
        value = _decode(persisted)
        if value != generated:
            raise ClientTrustSigningKeyUnavailable()
        return value

    def _read(self) -> str | None:
        try:
            return self._secret_store.get_secret(_SECRET_NAME)
        except Exception:
            raise ClientTrustSigningKeyUnavailable() from None

    def _write(self, value: str) -> None:
        try:
            self._secret_store.set_secret(_SECRET_NAME, value)
        except Exception:
            raise ClientTrustSigningKeyUnavailable() from None


def _generate() -> dict[str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": _base64url(
            public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ),
        "d": _base64url(
            private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ),
    }


def _encode(value: Mapping[str, str]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"))


def _decode(value: str) -> Mapping[str, str]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        raise ClientTrustSigningKeyUnavailable() from None
    if (
        not isinstance(decoded, dict)
        or set(decoded) != _JWK_FIELDS
        or decoded.get("kty") != "OKP"
        or decoded.get("crv") != "Ed25519"
        or not isinstance(decoded.get("x"), str)
        or not isinstance(decoded.get("d"), str)
    ):
        raise ClientTrustSigningKeyUnavailable()
    result = {name: decoded[name] for name in _JWK_FIELDS}
    try:
        private_bytes = _decode_base64url(result["d"])
        public_bytes = _decode_base64url(result["x"])
        private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    except (TypeError, ValueError):
        raise ClientTrustSigningKeyUnavailable() from None
    expected_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if len(private_bytes) != 32 or public_bytes != expected_public:
        raise ClientTrustSigningKeyUnavailable()
    return result


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    if not value or "=" in value:
        raise ValueError("invalid base64url")
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )
    if _base64url(decoded) != value:
        raise ValueError("noncanonical base64url")
    return decoded


__all__ = [
    "ClientTrustSigningKeyStore",
    "ClientTrustSigningKeyUnavailable",
]
