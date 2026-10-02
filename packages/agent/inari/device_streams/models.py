from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_HOLDER_ID = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
_UCUM_UNIT = re.compile(r"[A-Za-z0-9%\[\]./'*^()+-]{1,32}\Z")
_SYMBOLGY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_MAX_SAFE_SEQUENCE = 9_007_199_254_740_991


def _identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a stable identifier")


def _utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _sequence(name: str, value: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_SAFE_SEQUENCE
    ):
        raise ValueError(f"{name} must be a JavaScript-safe non-negative integer")


class DeviceStreamKind(StrEnum):
    SCALE = "scale"
    SCANNER = "scanner"


class ScaleRangeState(StrEnum):
    VALID = "valid"
    UNDERLOAD = "underload"
    OVERLOAD = "overload"


class StreamMessageKind(StrEnum):
    READY = "ready"
    SCALE_READING = "scale_reading"
    BARCODE = "barcode"
    HEARTBEAT = "heartbeat"
    SCALE_LEASE_LOST = "scale_lease_lost"
    REPLAY_UNAVAILABLE = "replay_unavailable"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class StreamSelection:
    kind: DeviceStreamKind
    device_id: str
    binding_revision_id: str
    certification_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DeviceStreamKind):
            object.__setattr__(self, "kind", DeviceStreamKind(self.kind))
        _identifier("device_id", self.device_id)
        _identifier("binding_revision_id", self.binding_revision_id)
        if self.kind is DeviceStreamKind.SCALE:
            if self.certification_id is None:
                raise ValueError("a scale selection requires a certification identity")
            _identifier("certification_id", self.certification_id)
        elif self.certification_id is not None:
            raise ValueError("a scanner selection cannot contain scale certification")


@dataclass(frozen=True, slots=True)
class EventLeaseRequest:
    holder_id: str
    selections: tuple[StreamSelection, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.holder_id, str) or not _HOLDER_ID.fullmatch(
            self.holder_id
        ):
            raise ValueError("holder_id must be a random in-memory browser identity")
        if not isinstance(self.selections, tuple) or not self.selections:
            raise ValueError("an event lease requires at least one selection")
        if len(self.selections) > 2:
            raise ValueError("an event lease accepts one scale and one scanner")
        if not all(isinstance(value, StreamSelection) for value in self.selections):
            raise TypeError("selections must contain StreamSelection values")
        kinds = [value.kind for value in self.selections]
        devices = [value.device_id for value in self.selections]
        if len(kinds) != len(set(kinds)) or len(devices) != len(set(devices)):
            raise ValueError("an event lease cannot repeat a kind or Device")


@dataclass(frozen=True, slots=True)
class EventLease:
    lease_id: str
    subscription_id: str
    holder_id: str
    generation: int
    scope_digest: str
    agent_id: str
    agent_boot_id: str
    client_grant_id: str
    selections: tuple[StreamSelection, ...]
    issued_at: datetime
    expires_at: datetime
    renew_after_ms: int = 3_000
    heartbeat_interval_ms: int = 15_000

    def __post_init__(self) -> None:
        for name in (
            "lease_id",
            "subscription_id",
            "scope_digest",
            "agent_id",
            "agent_boot_id",
            "client_grant_id",
        ):
            _identifier(name, getattr(self, name))
        if not _HOLDER_ID.fullmatch(self.holder_id):
            raise ValueError("holder_id is invalid")
        _sequence("generation", self.generation)
        issued_at = _utc("issued_at", self.issued_at)
        expires_at = _utc("expires_at", self.expires_at)
        if expires_at <= issued_at:
            raise ValueError("expires_at must follow issued_at")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class ScaleLease:
    scale_lease_id: str
    event_lease_id: str
    device_id: str
    binding_revision_id: str
    generation: int
    issued_at: datetime
    expires_at: datetime
    renew_after_ms: int = 1_000

    def __post_init__(self) -> None:
        for name in (
            "scale_lease_id",
            "event_lease_id",
            "device_id",
            "binding_revision_id",
        ):
            _identifier(name, getattr(self, name))
        _sequence("generation", self.generation)
        issued_at = _utc("issued_at", self.issued_at)
        expires_at = _utc("expires_at", self.expires_at)
        if expires_at <= issued_at:
            raise ValueError("expires_at must follow issued_at")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class ScaleSample:
    device_id: str
    binding_revision_id: str
    observed_at: datetime
    monotonic_ms: int
    value_mantissa: int
    decimal_exponent: int
    unit: str
    resolution_mantissa: int
    stable: bool
    range_state: ScaleRangeState

    def __post_init__(self) -> None:
        _identifier("device_id", self.device_id)
        _identifier("binding_revision_id", self.binding_revision_id)
        object.__setattr__(self, "observed_at", _utc("observed_at", self.observed_at))
        _sequence("monotonic_ms", self.monotonic_ms)
        if (
            isinstance(self.value_mantissa, bool)
            or not isinstance(self.value_mantissa, int)
            or not -(2**63) <= self.value_mantissa < 2**63
        ):
            raise ValueError("value_mantissa must be a signed 64-bit integer")
        if (
            isinstance(self.decimal_exponent, bool)
            or not isinstance(self.decimal_exponent, int)
            or not -12 <= self.decimal_exponent <= 12
        ):
            raise ValueError("decimal_exponent must be from -12 through 12")
        if not isinstance(self.unit, str) or not _UCUM_UNIT.fullmatch(self.unit):
            raise ValueError("unit must be one bounded UCUM unit")
        if (
            isinstance(self.resolution_mantissa, bool)
            or not isinstance(self.resolution_mantissa, int)
            or not 1 <= self.resolution_mantissa < 2**63
        ):
            raise ValueError("resolution_mantissa must be positive")
        if not isinstance(self.stable, bool):
            raise TypeError("stable must be a boolean")
        if not isinstance(self.range_state, ScaleRangeState):
            object.__setattr__(self, "range_state", ScaleRangeState(self.range_state))


@dataclass(frozen=True, slots=True)
class BarcodeSample:
    device_id: str
    binding_revision_id: str
    observed_at: datetime
    monotonic_ms: int
    symbology: str
    value: str

    def __post_init__(self) -> None:
        _identifier("device_id", self.device_id)
        _identifier("binding_revision_id", self.binding_revision_id)
        object.__setattr__(self, "observed_at", _utc("observed_at", self.observed_at))
        _sequence("monotonic_ms", self.monotonic_ms)
        if not isinstance(self.symbology, str) or not _SYMBOLGY.fullmatch(
            self.symbology
        ):
            raise ValueError("symbology must be a bounded identifier")
        if (
            not isinstance(self.value, str)
            or not self.value
            or len(self.value.encode("utf-8")) > 4_096
            or "\x00" in self.value
        ):
            raise ValueError("barcode value must be from 1 through 4096 bytes")


@dataclass(frozen=True, slots=True)
class SignedStreamMessage:
    kind: StreamMessageKind
    stream_sequence: int
    generation: int
    occurred_at: datetime
    payload: Mapping[str, object]
    signer_key_id: str
    signature: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, StreamMessageKind):
            object.__setattr__(self, "kind", StreamMessageKind(self.kind))
        _sequence("stream_sequence", self.stream_sequence)
        _sequence("generation", self.generation)
        object.__setattr__(self, "occurred_at", _utc("occurred_at", self.occurred_at))
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        _identifier("signer_key_id", self.signer_key_id)
        if not isinstance(self.signature, str) or not self.signature:
            raise ValueError("signature is required")

    def unsigned_document(self) -> dict[str, object]:
        return {
            "contract_major": 1,
            "kind": self.kind.value,
            "stream_sequence": self.stream_sequence,
            "generation": self.generation,
            "occurred_at": self.occurred_at.isoformat().replace("+00:00", "Z"),
            "payload": dict(self.payload),
            "signer_key_id": self.signer_key_id,
        }

    def document(self) -> dict[str, object]:
        return {**self.unsigned_document(), "signature": self.signature}


__all__ = [
    "BarcodeSample",
    "DeviceStreamKind",
    "EventLease",
    "EventLeaseRequest",
    "ScaleLease",
    "ScaleRangeState",
    "ScaleSample",
    "SignedStreamMessage",
    "StreamMessageKind",
    "StreamSelection",
]
