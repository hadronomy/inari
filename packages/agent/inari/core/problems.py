from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import re
from typing import Mapping
from uuid import uuid4

from .failures import DomainFailure, ProblemCode, ProblemDetails


@dataclass(frozen=True, slots=True)
class ProblemSpec:
    code: ProblemCode
    status: int
    title: str
    message_key: str
    detail: str
    retryable: bool


def _spec(
    code: ProblemCode,
    status: int,
    title: str,
    detail: str,
    retryable: bool,
) -> ProblemSpec:
    return ProblemSpec(
        code=code,
        status=status,
        title=title,
        message_key=code,
        detail=detail,
        retryable=retryable,
    )


_PROBLEM_SPECS = (
    _spec(
        ProblemCode.REQUEST_MALFORMED,
        400,
        "Request malformed",
        "The request is malformed.",
        False,
    ),
    _spec(
        ProblemCode.TRUST_REQUIRED,
        401,
        "Trust required",
        "The client trust must be established.",
        False,
    ),
    _spec(
        ProblemCode.PERMISSION_DENIED,
        403,
        "Permission denied",
        "The client does not have permission for this operation.",
        False,
    ),
    _spec(
        ProblemCode.RESOURCE_NOT_FOUND,
        404,
        "Resource not found",
        "The requested resource was not found.",
        False,
    ),
    _spec(
        ProblemCode.METHOD_NOT_ALLOWED,
        405,
        "Method not allowed",
        "The request method is not allowed for this resource.",
        False,
    ),
    _spec(
        ProblemCode.BINDING_REQUIRED,
        409,
        "Binding required",
        "The device binding must be completed.",
        False,
    ),
    _spec(
        ProblemCode.REQUEST_CONFLICT,
        409,
        "Request conflict",
        "The request conflicts with the current resource state.",
        False,
    ),
    _spec(
        ProblemCode.TRANSPORT_LEADER_ACTIVE,
        409,
        "Transport Leader active",
        "Another browser context owns the Transport Leader Lease.",
        True,
    ),
    _spec(
        ProblemCode.TRANSPORT_LEADER_LOST,
        409,
        "Transport Leader lost",
        "The Transport Leader Lease is no longer current.",
        True,
    ),
    _spec(
        ProblemCode.SCALE_IN_USE,
        409,
        "Scale in use",
        "Another register owns the Scale Lease.",
        True,
    ),
    _spec(
        ProblemCode.SCALE_LEASE_REQUIRED,
        409,
        "Scale Lease required",
        "A current Scale Lease is required for this stream.",
        True,
    ),
    _spec(
        ProblemCode.IDEMPOTENCY_CONFLICT,
        409,
        "Idempotency conflict",
        "The Idempotency Key identifies different Device Work.",
        False,
    ),
    _spec(
        ProblemCode.CONTRACT_MISMATCH,
        409,
        "Contract mismatch",
        "The client and device contracts do not match.",
        False,
    ),
    _spec(
        ProblemCode.CAPABILITY_CHANGED,
        409,
        "Capability changed",
        "The device capabilities changed. Refresh the device status.",
        True,
    ),
    _spec(
        ProblemCode.DEVICE_UNAVAILABLE,
        503,
        "Device unavailable",
        "The device is not available.",
        True,
    ),
    _spec(
        ProblemCode.PAPER_OUT,
        409,
        "Paper out",
        "The device needs paper before it can continue.",
        False,
    ),
    _spec(
        ProblemCode.COVER_OPEN,
        409,
        "Cover open",
        "Close the device cover before it can continue.",
        False,
    ),
    _spec(
        ProblemCode.QUEUE_FULL,
        429,
        "Queue full",
        "The device queue is full. Try again later.",
        True,
    ),
    _spec(
        ProblemCode.SPOOL_QUOTA_EXCEEDED,
        429,
        "Spool quota exceeded",
        "The protected device spool is full.",
        True,
    ),
    _spec(
        ProblemCode.SPOOL_STORAGE_LOW,
        507,
        "Spool storage low",
        "The protected device storage reserve is unavailable.",
        True,
    ),
    _spec(
        ProblemCode.PAYLOAD_TOO_LARGE,
        413,
        "Payload too large",
        "The submitted payload exceeds its content limit.",
        False,
    ),
    _spec(
        ProblemCode.MEDIA_TYPE_UNSUPPORTED,
        415,
        "Media type unsupported",
        "The submitted media type is not supported.",
        False,
    ),
    _spec(
        ProblemCode.PAYLOAD_INVALID,
        422,
        "Payload invalid",
        "The submitted payload is invalid.",
        False,
    ),
    _spec(
        ProblemCode.LABEL_DATA_INVALID,
        422,
        "Label data invalid",
        "The submitted label data is invalid.",
        False,
    ),
    _spec(
        ProblemCode.DOCUMENT_POLICY_REJECTED,
        422,
        "Document policy rejected",
        "The document is not allowed by device policy.",
        False,
    ),
    _spec(
        ProblemCode.DOCUMENT_PROCESSING_FAILED,
        422,
        "Document processing failed",
        "The document could not be processed.",
        False,
    ),
    _spec(
        ProblemCode.CERTIFICATION_REQUIRED,
        409,
        "Certification required",
        "The device requires certification before use.",
        False,
    ),
    _spec(
        ProblemCode.EXPIRED,
        410,
        "Expired",
        "The requested resource has expired.",
        False,
    ),
    _spec(
        ProblemCode.RECOVERY_FENCE,
        503,
        "Recovery fence",
        "Recovery is blocked until the device state is verified.",
        True,
    ),
    _spec(
        ProblemCode.RECOVERY_UNCERTAIN,
        503,
        "Recovery uncertain",
        "The device outcome is not confirmed. Reconcile its state.",
        True,
    ),
    _spec(
        ProblemCode.OUTCOME_UNKNOWN,
        503,
        "Outcome unknown",
        "The device outcome is not known.",
        True,
    ),
    _spec(
        ProblemCode.SERVICE_UNAVAILABLE,
        503,
        "Service unavailable",
        "The device service is not available.",
        True,
    ),
    _spec(
        ProblemCode.INTERNAL_ERROR,
        500,
        "Internal error",
        "The Agent could not complete the request.",
        False,
    ),
)

PROBLEM_CATALOG: Mapping[str, ProblemSpec] = MappingProxyType(
    {spec.code: spec for spec in _PROBLEM_SPECS}
)

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")


def get_problem_spec(code: ProblemCode | str) -> ProblemSpec:
    try:
        safe_code = ProblemCode(code)
    except ValueError as exc:
        raise ValueError("unknown problem code") from exc
    return PROBLEM_CATALOG[safe_code]


def new_correlation_id() -> str:
    return uuid4().hex


def _safe_correlation_id(value: str | None) -> str:
    return value if value and _CORRELATION_ID.fullmatch(value) else new_correlation_id()


@dataclass(frozen=True, slots=True)
class ProblemDocument:
    type: str
    title: str
    status: int
    detail: str
    instance: str
    error_code: ProblemCode
    message_key: str
    retryable: bool
    correlation_id: str
    details: ProblemDetails | None = None

    def __post_init__(self) -> None:
        spec = get_problem_spec(self.error_code)
        expected = {
            "type": f"urn:inari:problem:v1:{spec.code}",
            "title": spec.title,
            "status": spec.status,
            "detail": spec.detail,
            "message_key": spec.message_key,
            "retryable": spec.retryable,
        }
        if any(getattr(self, name) != value for name, value in expected.items()):
            raise ValueError("problem document must match its catalog entry")
        if not _CORRELATION_ID.fullmatch(self.correlation_id):
            raise ValueError("problem document correlation ID is invalid")
        if self.instance != f"urn:inari:request:{self.correlation_id}":
            raise ValueError("problem document instance must identify its request")
        if self.details is not None and not isinstance(self.details, ProblemDetails):
            raise TypeError("problem document details must be ProblemDetails")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": self.instance,
            "error_code": self.error_code,
            "message_key": self.message_key,
            "retryable": self.retryable,
            "correlation_id": self.correlation_id,
        }
        if self.details is not None and (detail_values := self.details.to_dict()):
            payload["details"] = detail_values
        return payload


def problem_from_failure(
    failure: DomainFailure,
    *,
    correlation_id: str | None = None,
) -> ProblemDocument:
    spec = get_problem_spec(failure.code)
    safe_id = _safe_correlation_id(correlation_id)
    return ProblemDocument(
        type=f"urn:inari:problem:v1:{spec.code}",
        title=spec.title,
        status=spec.status,
        detail=spec.detail,
        instance=f"urn:inari:request:{safe_id}",
        error_code=spec.code,
        message_key=spec.message_key,
        retryable=spec.retryable,
        correlation_id=safe_id,
        details=failure.details,
    )


def internal_problem(*, correlation_id: str | None = None) -> ProblemDocument:
    return problem_from_failure(
        DomainFailure(ProblemCode.INTERNAL_ERROR), correlation_id=correlation_id
    )


__all__ = [
    "PROBLEM_CATALOG",
    "ProblemCode",
    "ProblemDetails",
    "ProblemDocument",
    "ProblemSpec",
    "get_problem_spec",
    "internal_problem",
    "new_correlation_id",
    "problem_from_failure",
]
