from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from threading import Lock
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..runtime.models import DeviceConnectionState, DeviceRecord
from ..runtime.repositories import DeviceRepository
from ..security.secrets import SecretStore
from .authority import canonical_digest, canonical_json_bytes
from .models import (
    DeviceObservation,
    SignedDeviceObservation,
    SignerPurpose,
)
from .sqlite import SqliteDeviceAuthorityReader


_KEY_SECRET = "device_observation_ed25519_private_key_v1"
_UNKNOWN = "unavailable"
_FACT_NAMES = (
    "platform_backend_id",
    "connection",
    "media_profile",
    "operating_system",
)
_FIRMWARE_FACT_NAMES = ("firmware_version", "firmware_build")


class DeviceObservationSigningKey:
    """Keep Device observation signatures under one protected, purpose-bound key."""

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
                        raise RuntimeError("The Device observation key was not stored.")
                if len(encoded) != 64 or encoded.lower() != encoded:
                    raise ValueError("The Device observation key is invalid.")
                self._private_key = Ed25519PrivateKey.from_private_bytes(
                    bytes.fromhex(encoded)
                )
            return self._private_key

    def public_key(self) -> bytes:
        return self._key().public_key().public_bytes_raw()

    def key_id(self) -> str:
        return f"device_observation_{sha256(self.public_key()).hexdigest()}"

    def sign(self, observation: DeviceObservation) -> SignedDeviceObservation:
        return SignedDeviceObservation(
            observation=observation,
            digest=canonical_digest(observation),
            signer_key_id=self.key_id(),
            signature=self._key().sign(canonical_json_bytes(observation)),
        )


class LiveDeviceObservationReader:
    """Sign only facts from a fresh Driver discovery record and active authority."""

    def __init__(
        self,
        *,
        devices: DeviceRepository,
        authority: SqliteDeviceAuthorityReader,
        signing_key: DeviceObservationSigningKey,
    ) -> None:
        self._devices = devices
        self._authority = authority
        self._signing_key = signing_key

    def read_current(
        self, device_id: str, driver_profile_digest: str
    ) -> SignedDeviceObservation | None:
        device = self._devices.get(device_id)
        if device is None:
            return None
        signer = self._authority.read_signer(
            self._signing_key.key_id(), SignerPurpose.DEVICE_OBSERVATION
        )
        if signer is None or signer.public_key != self._signing_key.public_key():
            return None
        try:
            observation = _observation(device, driver_profile_digest)
        except ValueError:
            return None
        return self._signing_key.sign(observation)


def _observation(device: DeviceRecord, profile_digest: str) -> DeviceObservation:
    supplied = device.metadata.get("authority_observation")
    facts = supplied if isinstance(supplied, dict) else {}
    # The spooler cannot observe firmware. Its explicit unavailable value must
    # still match a separately signed matrix row before Device Work is admitted.
    valid = all(_fact(facts, name) != _UNKNOWN for name in _FACT_NAMES) and all(
        facts.get(name) == _UNKNOWN or _fact(facts, name) != _UNKNOWN
        for name in _FIRMWARE_FACT_NAMES
    )
    ready = (
        device.connection_state is DeviceConnectionState.ONLINE
        and device.observed_at <= datetime.now(UTC)
        and valid
        and facts.get("ready") is True
    )
    if device.connection_state is DeviceConnectionState.OFFLINE:
        reason = "device_offline"
    elif not valid:
        reason = "driver_facts_unavailable"
    elif not ready:
        reason = "driver_not_ready"
    else:
        reason = None
    return DeviceObservation(
        observation_id=uuid4().hex,
        device_id=device.id,
        device_identity_digest=sha256(
            device.identity.stable_key().encode()
        ).hexdigest(),
        driver_id=device.driver_key,
        driver_profile_digest=profile_digest,
        platform_backend_id=_fact(facts, "platform_backend_id"),
        connection=_fact(facts, "connection"),
        media_profile=_fact(facts, "media_profile"),
        firmware_version=_fact(facts, "firmware_version"),
        firmware_build=_fact(facts, "firmware_build"),
        operating_system=_fact(facts, "operating_system"),
        ready=ready,
        state="ready" if ready else "not_ready",
        reason=reason,
        observed_at=device.observed_at,
    )


def _fact(facts: dict, name: str) -> str:
    value = facts.get(name)
    if isinstance(value, str) and value.strip() and len(value) <= 256:
        return value
    return _UNKNOWN
