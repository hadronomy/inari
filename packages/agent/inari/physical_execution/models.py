from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from ..print_jobs import OutputEvidence, PrintJobState
from ..spool import ArtifactKind


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("The execution time must include a timezone.")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ExecutionOwner:
    owner_id: str
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.owner_id, str) or not self.owner_id:
            raise ValueError("The execution owner identifier is invalid.")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 1
        ):
            raise ValueError("The execution owner generation must be positive.")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    storage_ref: str
    kind: ArtifactKind
    format_version: int
    nonce: bytes
    plaintext_size_bytes: int
    ciphertext_size_bytes: int
    plaintext_sha256: bytes


@dataclass(frozen=True, slots=True)
class WrappedJobKey:
    root_version: int
    format_version: int
    wrap_nonce: bytes
    wrapped_key: bytes


@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    attempt_id: str
    lease_id: str
    execution_id: str
    owner: ExecutionOwner
    attempt_number: int
    state_version: int
    lease_expires_at: datetime
    job_id: str
    intent_id: str
    admission_id: str
    device_id: str
    driver_key: str
    device_name: str
    capability_id: str
    operation: str
    media_type: str
    normalized_options_digest: bytes
    binding_revision_id: str
    scope_kind: str
    managed_work_id: str | None
    grant_id: str | None
    grant_pairing_id: str | None
    grant_generation: int | None
    grant_authorization_digest: bytes | None
    expires_at: datetime
    original: ArtifactRef
    key: WrappedJobKey

    def __post_init__(self) -> None:
        object.__setattr__(self, "lease_expires_at", _utc(self.lease_expires_at))
        object.__setattr__(self, "expires_at", _utc(self.expires_at))


@dataclass(frozen=True, slots=True)
class PreparedDeviceWork:
    device_id: str
    driver_key: str
    device_name: str
    operation: str
    media_type: str
    content: bytes
    content_sha256: bytes
    deadline: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or not self.content:
            raise ValueError("Prepared Device Work requires content.")
        object.__setattr__(self, "deadline", _utc(self.deadline))


@dataclass(frozen=True, slots=True)
class IoPermit:
    attempt_id: str
    lease_id: str
    execution_id: str
    job_id: str
    device_id: str
    marker_id: str
    marker_sequence: int
    committed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "committed_at", _utc(self.committed_at))


class DriverOutcome(StrEnum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DriverExecutionResult:
    outcome: DriverOutcome
    evidence: OutputEvidence | None = None
    platform_job_id: str | None = None
    error_code: str | None = None
    message_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", DriverOutcome(self.outcome))
        if self.evidence is not None:
            object.__setattr__(self, "evidence", OutputEvidence(self.evidence))


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    job_id: str
    state: PrintJobState
    state_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", PrintJobState(self.state))


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    returned_to_accepted: int
    expired: int
    outcome_unknown: int


class PhysicalExecutionError(RuntimeError):
    pass


class LeaseLost(PhysicalExecutionError):
    pass


class PreIoFailure(PhysicalExecutionError):
    def __init__(
        self,
        error_code: str = "service_unavailable",
        message_key: str = "print.service_unavailable",
    ) -> None:
        super().__init__(message_key)
        self.error_code = error_code
        self.message_key = message_key


class ExecutionRejected(PreIoFailure):
    pass


class PreparationFailed(PreIoFailure):
    pass


__all__ = [
    "ArtifactRef",
    "DriverExecutionResult",
    "DriverOutcome",
    "ExecutionClaim",
    "ExecutionOwner",
    "ExecutionRejected",
    "ExecutionReceipt",
    "IoPermit",
    "LeaseLost",
    "PhysicalExecutionError",
    "PreIoFailure",
    "PreparationFailed",
    "PreparedDeviceWork",
    "RecoveryReport",
    "WrappedJobKey",
]
