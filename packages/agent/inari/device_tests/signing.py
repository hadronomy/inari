from __future__ import annotations

from hashlib import sha256
from threading import Lock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..device_authority import canonical_digest, canonical_json_bytes
from ..device_authority.models import DeviceTestEvidence, SignedDeviceTestEvidence
from ..security.secrets import SecretStore


_KEY_SECRET = "device_test_evidence_ed25519_private_key_v1"


class DeviceTestSigningKey:
    """Keep physical Device Test results under a protected evidence key."""

    def __init__(self, secrets: SecretStore) -> None:
        self._secrets = secrets
        self._lock = Lock()
        self._private_key: Ed25519PrivateKey | None = None

    def _key(self) -> Ed25519PrivateKey:
        with self._lock:
            if self._private_key is None:
                encoded = self._secrets.get_secret(_KEY_SECRET)
                if encoded is None:
                    encoded = Ed25519PrivateKey.generate().private_bytes_raw().hex()
                    self._secrets.set_secret(_KEY_SECRET, encoded)
                    if self._secrets.get_secret(_KEY_SECRET) != encoded:
                        raise RuntimeError("The Device Test key was not stored.")
                if len(encoded) != 64 or encoded.lower() != encoded:
                    raise ValueError("The Device Test key is invalid.")
                self._private_key = Ed25519PrivateKey.from_private_bytes(
                    bytes.fromhex(encoded)
                )
            return self._private_key

    def public_key(self) -> bytes:
        return self._key().public_key().public_bytes_raw()

    def key_id(self) -> str:
        return f"device_test_evidence_{sha256(self.public_key()).hexdigest()}"

    def sign_evidence(self, evidence: DeviceTestEvidence) -> SignedDeviceTestEvidence:
        return SignedDeviceTestEvidence(
            evidence=evidence,
            digest=canonical_digest(evidence),
            signer_key_id=self.key_id(),
            signature=self._key().sign(canonical_json_bytes(evidence)),
        )

    def sign_result(self, result: dict[str, object]) -> dict[str, object]:
        return {
            "result": result,
            "digest": canonical_digest(result),
            "signer_key_id": self.key_id(),
            "signature": self._key().sign(canonical_json_bytes(result)).hex(),
        }
