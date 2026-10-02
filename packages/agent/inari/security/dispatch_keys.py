from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
)

from .secrets import SecretStore


DISPATCH_PRIVATE_KEY_SECRET = "managed_dispatch_x25519_private_key_v1"


@dataclass(frozen=True, slots=True)
class DispatchEncryptionKeyPair:
    key_id: str
    private_key: X25519PrivateKey
    public_key_base64url: str

    def public_descriptor(self) -> dict[str, str]:
        return {
            "key_id": self.key_id,
            "kem": "dhkem_x25519_hkdf_sha256",
            "public_key_base64url": self.public_key_base64url,
        }


class DispatchEncryptionKeyService:
    def __init__(self, secret_store: SecretStore) -> None:
        self._secret_store = secret_store
        self._cached: DispatchEncryptionKeyPair | None = None

    def get_or_create(self) -> DispatchEncryptionKeyPair:
        if self._cached is not None:
            return self._cached
        encoded = self._secret_store.get_secret(DISPATCH_PRIVATE_KEY_SECRET)
        if encoded is None:
            private_key = X25519PrivateKey.generate()
            private_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            self._secret_store.set_secret(
                DISPATCH_PRIVATE_KEY_SECRET,
                _base64url(private_bytes),
            )
        else:
            private_bytes = _decode_base64url(encoded)
            if len(private_bytes) != 32:
                raise ValueError("The managed dispatch private key is invalid.")
            private_key = X25519PrivateKey.from_private_bytes(private_bytes)

        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._cached = DispatchEncryptionKeyPair(
            key_id=f"dispatch_{sha256(public_bytes).hexdigest()[:16]}",
            private_key=private_key,
            public_key_base64url=_base64url(public_bytes),
        )
        return self._cached


def _base64url(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("The managed dispatch private key is invalid.") from exc
