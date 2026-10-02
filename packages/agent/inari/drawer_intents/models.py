from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from ..device_authority import AdmissionPermit


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_MAX_ACTION_SEQUENCE = 9_007_199_254_740_991


class DrawerReason(StrEnum):
    PAYMENT = "payment"
    MANUAL_OPEN = "manual_open"


class DrawerIntentState(StrEnum):
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    OUTCOME_UNKNOWN = "outcome_unknown"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DrawerIntentRequest:
    intent_id: str
    device_id: str
    binding_revision_id: str
    pos_session_id: str
    action_sequence: int
    reason: DrawerReason

    def __post_init__(self) -> None:
        for name in (
            "intent_id",
            "device_id",
            "binding_revision_id",
            "pos_session_id",
        ):
            if not _IDENTIFIER.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} must be a stable identifier")
        if (
            isinstance(self.action_sequence, bool)
            or not isinstance(self.action_sequence, int)
            or self.action_sequence < 1
            or self.action_sequence > _MAX_ACTION_SEQUENCE
        ):
            raise ValueError("action_sequence must be a positive safe integer")
        object.__setattr__(self, "reason", DrawerReason(self.reason))


@dataclass(frozen=True, slots=True)
class DrawerIntentRecord:
    record_id: str
    intent_id: str
    database: str
    organization_id: str
    site_id: str
    pos_configuration_id: str
    paired_client_id: str
    actor_id: str
    device_id: str
    binding_revision_id: str
    pos_session_id: str
    action_sequence: int
    reason: DrawerReason
    fingerprint: bytes
    state: DrawerIntentState
    state_version: int
    accepted_at: datetime
    expires_at: datetime
    started_at: datetime | None = None
    terminal_at: datetime | None = None
    error_code: str | None = None
    message_key: str | None = None
    printer_name: str | None = None
    transport: str | None = None

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.record_id):
            raise ValueError("record_id must be a stable identifier")
        object.__setattr__(self, "reason", DrawerReason(self.reason))
        object.__setattr__(self, "state", DrawerIntentState(self.state))
        for name in ("accepted_at", "expires_at", "started_at", "terminal_at"):
            value = getattr(self, name)
            if value is not None:
                if value.tzinfo is None:
                    raise ValueError(f"{name} must be timezone-aware")
                object.__setattr__(self, name, value.astimezone(UTC))
        if self.expires_at <= self.accepted_at:
            raise ValueError("expires_at must follow accepted_at")
        if self.state_version < 1:
            raise ValueError("state_version must be positive")
        if not isinstance(self.fingerprint, bytes) or len(self.fingerprint) != 32:
            raise ValueError("fingerprint must contain 32 bytes")

    @property
    def retryable(self) -> bool:
        return self.started_at is None and self.state in {
            DrawerIntentState.ACCEPTED,
            DrawerIntentState.FAILED,
        }


@dataclass(frozen=True, slots=True)
class DrawerIntentAccepted:
    record: DrawerIntentRecord
    replayed: bool
    permit: AdmissionPermit | None


@dataclass(frozen=True, slots=True)
class DrawerIntentPage:
    intents: tuple[DrawerIntentRecord, ...]
    missing_intent_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DrawerLedgerAdmission:
    record: DrawerIntentRecord
    created: bool


__all__ = [
    "DrawerIntentAccepted",
    "DrawerIntentPage",
    "DrawerIntentRecord",
    "DrawerIntentRequest",
    "DrawerIntentState",
    "DrawerLedgerAdmission",
    "DrawerReason",
]
