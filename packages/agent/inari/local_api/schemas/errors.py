from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictBool, StringConstraints, model_validator

from ...core.failures import FieldViolationCode, ProblemCode
from ...core.problems import get_problem_spec
from .base import APIModel


Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    ),
]
MessageKey = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]
JsonPointer = Annotated[
    str,
    StringConstraints(
        max_length=512,
        pattern=r"^(?:|/(?:[^~\x00-\x1f\x7f]|~[01])*)$",
    ),
]
CorrelationId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]*$",
    ),
]
ProblemType = Annotated[
    str,
    StringConstraints(pattern=r"^urn:inari:problem:v1:[a-z][a-z0-9_]*$"),
]
ProblemInstance = Annotated[
    str,
    StringConstraints(pattern=r"^urn:inari:request:[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$"),
]


class FieldViolationResponse(APIModel):
    pointer: JsonPointer
    code: FieldViolationCode
    message_key: MessageKey


class ProblemDetailsResponse(APIModel):
    device_id: Identifier | None = None
    job_id: Identifier | None = None
    managed_work_id: Identifier | None = None
    retry_after_seconds: Annotated[int, Field(strict=True, ge=0, le=86_400)] | None = (
        None
    )
    field_violations: list[FieldViolationResponse] = Field(
        default_factory=list,
        max_length=32,
    )


class ErrorResponse(APIModel):
    type: ProblemType
    title: str = Field(min_length=1, max_length=128)
    status: Annotated[int, Field(strict=True, ge=400, le=599)]
    detail: str = Field(min_length=1, max_length=512)
    instance: ProblemInstance
    error_code: ProblemCode
    message_key: MessageKey
    retryable: StrictBool
    correlation_id: CorrelationId
    details: ProblemDetailsResponse | None = None

    @model_validator(mode="after")
    def validate_catalog_invariants(self) -> ErrorResponse:
        spec = get_problem_spec(self.error_code)
        expected = (
            f"urn:inari:problem:v1:{spec.code}",
            spec.title,
            spec.status,
            spec.detail,
            spec.message_key,
            spec.retryable,
            f"urn:inari:request:{self.correlation_id}",
        )
        actual = (
            self.type,
            self.title,
            self.status,
            self.detail,
            self.message_key,
            self.retryable,
            self.instance,
        )
        if actual != expected:
            raise ValueError("problem response must match its catalog entry")
        return self


__all__ = [
    "ErrorResponse",
    "FieldViolationResponse",
    "ProblemDetailsResponse",
]
