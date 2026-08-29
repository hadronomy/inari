from __future__ import annotations

from typing import Any, Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..core.exceptions import AgentError
from ..core.failures import (
    DomainFailure,
    FieldViolation,
    FieldViolationCode,
    ProblemCode,
    ProblemDetails,
)
from ..core.problems import internal_problem, problem_from_failure
from .ingress import IngressError, IngressFailureKind
from .schemas.errors import ErrorResponse


PROBLEM_MEDIA_TYPE = "application/problem+json"


def _request_correlation_id(request: Request) -> str | None:
    return request.headers.get("X-Correlation-ID") or request.headers.get(
        "X-Request-ID"
    )


def problem_response(
    failure: Exception,
    *,
    correlation_id: str | None = None,
) -> JSONResponse:
    document = (
        problem_from_failure(failure, correlation_id=correlation_id)
        if isinstance(failure, DomainFailure)
        else internal_problem(correlation_id=correlation_id)
    )
    return JSONResponse(
        status_code=document.status,
        content=document.to_dict(),
        media_type=PROBLEM_MEDIA_TYPE,
        headers={"X-Correlation-ID": document.correlation_id},
    )


async def _domain_failure_handler(request: Request, failure: Exception) -> JSONResponse:
    if not isinstance(failure, DomainFailure):
        return await _unexpected_failure_handler(request, failure)
    return problem_response(
        failure,
        correlation_id=_request_correlation_id(request),
    )


async def _unexpected_failure_handler(
    request: Request, failure: Exception
) -> JSONResponse:
    return problem_response(
        failure,
        correlation_id=_request_correlation_id(request),
    )


async def _validation_failure_handler(
    request: Request, failure: Exception
) -> JSONResponse:
    if not isinstance(failure, RequestValidationError):
        return await _unexpected_failure_handler(request, failure)
    violations = tuple(_field_violation(item) for item in failure.errors()[:32])
    return problem_response(
        DomainFailure(
            ProblemCode.PAYLOAD_INVALID,
            details=ProblemDetails(field_violations=violations),
        ),
        correlation_id=_request_correlation_id(request),
    )


async def _http_failure_handler(request: Request, failure: Exception) -> JSONResponse:
    if not isinstance(failure, StarletteHTTPException):
        return await _unexpected_failure_handler(request, failure)
    code = {
        404: ProblemCode.RESOURCE_NOT_FOUND,
        405: ProblemCode.METHOD_NOT_ALLOWED,
        413: ProblemCode.PAYLOAD_TOO_LARGE,
        415: ProblemCode.MEDIA_TYPE_UNSUPPORTED,
    }.get(failure.status_code, ProblemCode.REQUEST_MALFORMED)
    return problem_response(
        DomainFailure(code),
        correlation_id=_request_correlation_id(request),
    )


async def _agent_failure_handler(request: Request, failure: Exception) -> JSONResponse:
    if not isinstance(failure, AgentError):
        return await _unexpected_failure_handler(request, failure)
    code = {
        400: ProblemCode.REQUEST_MALFORMED,
        401: ProblemCode.TRUST_REQUIRED,
        403: ProblemCode.PERMISSION_DENIED,
        404: ProblemCode.RESOURCE_NOT_FOUND,
        405: ProblemCode.METHOD_NOT_ALLOWED,
        409: ProblemCode.REQUEST_CONFLICT,
        410: ProblemCode.EXPIRED,
        413: ProblemCode.PAYLOAD_TOO_LARGE,
        415: ProblemCode.MEDIA_TYPE_UNSUPPORTED,
        422: ProblemCode.PAYLOAD_INVALID,
        429: ProblemCode.QUEUE_FULL,
        503: ProblemCode.SERVICE_UNAVAILABLE,
        507: ProblemCode.SPOOL_STORAGE_LOW,
    }.get(failure.status_code, ProblemCode.INTERNAL_ERROR)
    return problem_response(
        DomainFailure(code),
        correlation_id=_request_correlation_id(request),
    )


async def _ingress_failure_handler(
    request: Request, failure: Exception
) -> JSONResponse:
    if not isinstance(failure, IngressError):
        return await _unexpected_failure_handler(request, failure)
    code = {
        IngressFailureKind.MALFORMED: ProblemCode.REQUEST_MALFORMED,
        IngressFailureKind.TOO_LARGE: ProblemCode.PAYLOAD_TOO_LARGE,
        IngressFailureKind.UNSUPPORTED_MEDIA_TYPE: (ProblemCode.MEDIA_TYPE_UNSUPPORTED),
        IngressFailureKind.INVALID_ENVELOPE: ProblemCode.PAYLOAD_INVALID,
    }[failure.kind]
    return problem_response(
        DomainFailure(code),
        correlation_id=_request_correlation_id(request),
    )


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainFailure, _domain_failure_handler)
    app.add_exception_handler(IngressError, _ingress_failure_handler)
    app.add_exception_handler(AgentError, _agent_failure_handler)
    app.add_exception_handler(RequestValidationError, _validation_failure_handler)
    app.add_exception_handler(StarletteHTTPException, _http_failure_handler)
    app.add_exception_handler(Exception, _unexpected_failure_handler)
    _install_problem_openapi(app)


def problem_responses(
    *statuses: int,
) -> dict[int | str, dict[str, Any]]:
    selected = statuses or (
        400,
        401,
        403,
        404,
        405,
        409,
        410,
        413,
        415,
        422,
        429,
        500,
        503,
        507,
    )
    if any(
        isinstance(status, bool)
        or not isinstance(status, int)
        or not 400 <= status <= 599
        for status in selected
    ):
        raise ValueError("problem response status must be from 400 through 599")
    return {
        status: {
            "description": "RFC 9457 Problem Details",
            "x-inari-problem-response": True,
            "model": ErrorResponse,
            "content": {
                PROBLEM_MEDIA_TYPE: {
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                }
            },
        }
        for status in selected
    }


def _field_violation(item: Mapping[str, Any]) -> FieldViolation:
    raw_location = item.get("loc", ())
    location = raw_location if isinstance(raw_location, tuple | list) else ()
    pointer = "/" + "/".join(_pointer_token(value) for value in location[:16])
    if len(pointer) > 512:
        pointer = "/request"
    raw_type = str(item.get("type", "invalid"))
    code = (
        FieldViolationCode.REQUIRED
        if raw_type == "missing"
        else FieldViolationCode.OUT_OF_RANGE
        if any(token in raw_type for token in ("greater", "less", "range"))
        else FieldViolationCode.INVALID
    )
    return FieldViolation(
        pointer=pointer,
        code=code,
        message_key=f"request_field_{code.value}",
    )


def _pointer_token(value: object) -> str:
    return str(value)[:64].replace("~", "~0").replace("/", "~1")


def _install_problem_openapi(app: FastAPI) -> None:
    original_openapi = app.openapi

    def problem_openapi() -> dict[str, Any]:
        schema = original_openapi()
        paths = schema.get("paths", {})
        if isinstance(paths, dict):
            for path_item in paths.values():
                if not isinstance(path_item, dict):
                    continue
                for operation in path_item.values():
                    if not isinstance(operation, dict):
                        continue
                    responses = operation.get("responses", {})
                    if not isinstance(responses, dict):
                        continue
                    for response in responses.values():
                        if not isinstance(response, dict) or not response.pop(
                            "x-inari-problem-response", False
                        ):
                            continue
                        content = response.get("content")
                        if isinstance(content, dict):
                            content.pop("application/json", None)
        return schema

    setattr(app, "openapi", problem_openapi)


__all__ = [
    "PROBLEM_MEDIA_TYPE",
    "install_problem_handlers",
    "problem_response",
    "problem_responses",
]
