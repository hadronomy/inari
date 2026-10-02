from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class ProblemCode(StrEnum):
    REQUEST_MALFORMED = "request_malformed"
    TRUST_REQUIRED = "trust_required"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_NOT_FOUND = "resource_not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    BINDING_REQUIRED = "binding_required"
    REQUEST_CONFLICT = "request_conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    CONTRACT_MISMATCH = "contract_mismatch"
    CAPABILITY_CHANGED = "capability_changed"
    DEVICE_UNAVAILABLE = "device_unavailable"
    PAPER_OUT = "paper_out"
    COVER_OPEN = "cover_open"
    QUEUE_FULL = "queue_full"
    SPOOL_QUOTA_EXCEEDED = "spool_quota_exceeded"
    SPOOL_STORAGE_LOW = "spool_storage_low"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    MEDIA_TYPE_UNSUPPORTED = "media_type_unsupported"
    PAYLOAD_INVALID = "payload_invalid"
    LABEL_DATA_INVALID = "label_data_invalid"
    DOCUMENT_POLICY_REJECTED = "document_policy_rejected"
    DOCUMENT_PROCESSING_FAILED = "document_processing_failed"
    CERTIFICATION_REQUIRED = "certification_required"
    EXPIRED = "expired"
    RECOVERY_FENCE = "recovery_fence"
    RECOVERY_UNCERTAIN = "recovery_uncertain"
    OUTCOME_UNKNOWN = "outcome_unknown"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL_ERROR = "internal_error"


class FieldViolationCode(StrEnum):
    REQUIRED = "required"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"
    OUT_OF_RANGE = "out_of_range"
    TOO_LARGE = "too_large"
    CONFLICT = "conflict"


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_MESSAGE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_JSON_POINTER = re.compile(r"^(?:|/(?:[^~\x00-\x1f\x7f]|~[01])*)$")
_MAX_POINTER_LENGTH = 512
_MAX_FIELD_VIOLATIONS = 32
_MAX_RETRY_AFTER_SECONDS = 86_400


@dataclass(frozen=True, slots=True)
class FieldViolation:
    """A safe validation failure tied to an RFC 6901 JSON pointer."""

    pointer: str
    code: FieldViolationCode | str
    message_key: str

    def __post_init__(self) -> None:
        if len(self.pointer) > _MAX_POINTER_LENGTH:
            raise ValueError("field violation pointer is too long")
        if not _JSON_POINTER.fullmatch(self.pointer):
            raise ValueError("field violation pointer must be an RFC 6901 pointer")
        try:
            code = FieldViolationCode(self.code)
        except ValueError as exc:
            raise ValueError("field violation code is not supported") from exc
        if not _MESSAGE_KEY.fullmatch(self.message_key):
            raise ValueError("field violation message key is not valid")
        object.__setattr__(self, "code", code)

    def to_dict(self) -> dict[str, str]:
        return {
            "pointer": self.pointer,
            "code": self.code,
            "message_key": self.message_key,
        }


@dataclass(frozen=True, slots=True)
class ProblemDetails:
    """The bounded, content-free details allowed in a public problem."""

    device_id: str | None = None
    job_id: str | None = None
    managed_work_id: str | None = None
    retry_after_seconds: int | None = None
    field_violations: tuple[FieldViolation, ...] = ()

    def __post_init__(self) -> None:
        for name in ("device_id", "job_id", "managed_work_id"):
            value = getattr(self, name)
            if value is not None and not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} is not a safe identifier")
        if self.retry_after_seconds is not None:
            if isinstance(self.retry_after_seconds, bool) or not isinstance(
                self.retry_after_seconds, int
            ):
                raise TypeError("retry_after_seconds must be an integer")
            if not 0 <= self.retry_after_seconds <= _MAX_RETRY_AFTER_SECONDS:
                raise ValueError("retry_after_seconds must be from 0 through 86400")
        if not isinstance(self.field_violations, tuple):
            raise TypeError("field_violations must be a tuple")
        if len(self.field_violations) > _MAX_FIELD_VIOLATIONS:
            raise ValueError("field_violations accepts at most 32 items")
        if not all(
            isinstance(violation, FieldViolation) for violation in self.field_violations
        ):
            raise TypeError("field_violations must contain only FieldViolation values")

    def to_dict(self) -> dict[str, object]:
        values: dict[str, object] = {}
        for name in ("device_id", "job_id", "managed_work_id"):
            value = getattr(self, name)
            if value is not None:
                values[name] = value
        if self.retry_after_seconds is not None:
            values["retry_after_seconds"] = self.retry_after_seconds
        if self.field_violations:
            values["field_violations"] = [
                violation.to_dict() for violation in self.field_violations
            ]
        return values


class DomainFailure(Exception):
    """A domain failure with one safe code and bounded public details."""

    def __init__(
        self,
        code: ProblemCode | str,
        *,
        details: ProblemDetails | None = None,
    ) -> None:
        try:
            self.code = ProblemCode(code)
        except ValueError as exc:
            raise ValueError("unknown domain failure code") from exc
        if details is not None and not isinstance(details, ProblemDetails):
            raise TypeError("details must be ProblemDetails")
        self.details = details or ProblemDetails()
        super().__init__(self.code)


__all__ = [
    "DomainFailure",
    "FieldViolation",
    "FieldViolationCode",
    "ProblemCode",
    "ProblemDetails",
]
