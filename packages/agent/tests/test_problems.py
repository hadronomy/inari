from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from inari.core.exceptions import AgentError
from inari.core.failures import (
    DomainFailure,
    FieldViolation,
    FieldViolationCode,
    ProblemCode,
)
from inari.core.problems import (
    PROBLEM_CATALOG,
    ProblemDetails,
    ProblemDocument,
    ProblemSpec,
    problem_from_failure,
)
from inari.local_api.problem_handlers import (
    PROBLEM_MEDIA_TYPE,
    install_problem_handlers,
    problem_response,
    problem_responses,
)
from inari.local_api.ingress import IngressError
from inari.local_api.schemas.errors import (
    ErrorResponse,
    FieldViolationResponse,
    ProblemDetailsResponse,
)


def test_catalog_contains_only_the_versioned_lower_case_codes() -> None:
    expected = {
        "request_malformed",
        "trust_required",
        "permission_denied",
        "resource_not_found",
        "method_not_allowed",
        "binding_required",
        "request_conflict",
        "transport_leader_active",
        "transport_leader_lost",
        "scale_in_use",
        "scale_lease_required",
        "idempotency_conflict",
        "contract_mismatch",
        "capability_changed",
        "device_unavailable",
        "paper_out",
        "cover_open",
        "queue_full",
        "spool_quota_exceeded",
        "spool_storage_low",
        "payload_too_large",
        "media_type_unsupported",
        "payload_invalid",
        "label_data_invalid",
        "document_policy_rejected",
        "document_processing_failed",
        "certification_required",
        "expired",
        "recovery_fence",
        "recovery_uncertain",
        "outcome_unknown",
        "service_unavailable",
        "internal_error",
    }

    assert set(PROBLEM_CATALOG) == expected
    assert all(isinstance(spec, ProblemSpec) for spec in PROBLEM_CATALOG.values())
    assert all(spec.code == code for code, spec in PROBLEM_CATALOG.items())


def test_domain_failure_carries_safe_typed_details() -> None:
    failure = DomainFailure(
        "payload_invalid",
        details=ProblemDetails(
            field_violations=(
                FieldViolation(
                    pointer="/context/device_id",
                    code="required",
                    message_key="device_id_required",
                ),
            ),
        ),
    )

    document = problem_from_failure(failure, correlation_id="corr-123")

    assert failure.code is ProblemCode.PAYLOAD_INVALID
    assert not hasattr(failure, "spec")
    assert not hasattr(failure, "status")
    assert document.to_dict() == {
        "type": "urn:inari:problem:v1:payload_invalid",
        "title": "Payload invalid",
        "status": 422,
        "detail": "The submitted payload is invalid.",
        "instance": "urn:inari:request:corr-123",
        "error_code": "payload_invalid",
        "message_key": "payload_invalid",
        "retryable": False,
        "correlation_id": "corr-123",
        "details": {
            "field_violations": [
                {
                    "pointer": "/context/device_id",
                    "code": "required",
                    "message_key": "device_id_required",
                }
            ]
        },
    }


def test_field_violation_accepts_the_root_pointer_and_closed_code() -> None:
    violation = FieldViolation(
        pointer="",
        code="invalid",
        message_key="request_invalid",
    )

    assert violation.code is FieldViolationCode.INVALID
    assert violation.to_dict()["pointer"] == ""
    assert FieldViolationResponse.model_validate(violation.to_dict()).pointer == ""


@pytest.mark.parametrize(
    ("pointer", "code", "message_key"),
    [
        ("x" * 513, "invalid", "request_invalid"),
        ("not-a-pointer", "invalid", "request_invalid"),
        ("/field", "invented", "request_invalid"),
        ("/field", "invalid", "Not Safe"),
    ],
)
def test_field_violation_rejects_unbounded_or_open_values(
    pointer: str, code: str, message_key: str
) -> None:
    with pytest.raises(ValueError):
        FieldViolation(pointer=pointer, code=code, message_key=message_key)


def test_problem_details_enforce_count_types_and_retry_bounds() -> None:
    violation = FieldViolation("/field", "invalid", "field_invalid")

    with pytest.raises(ValueError, match="at most 32"):
        ProblemDetails(field_violations=(violation,) * 33)
    with pytest.raises(TypeError, match="FieldViolation"):
        ProblemDetails(field_violations=("not-a-violation",))  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="86400"):
        ProblemDetails(retry_after_seconds=86_401)
    with pytest.raises(TypeError, match="integer"):
        ProblemDetails(retry_after_seconds=True)  # type: ignore[arg-type]


def test_problem_document_cannot_bypass_the_catalog() -> None:
    with pytest.raises(ValueError, match="catalog"):
        ProblemDocument(
            type="urn:inari:problem:v1:internal_error",
            title="Leaked driver failure",
            status=418,
            detail="secret driver path",
            instance="urn:inari:request:corr-1",
            error_code=ProblemCode.INTERNAL_ERROR,
            message_key="internal_error",
            retryable=True,
            correlation_id="corr-1",
        )

    with pytest.raises(TypeError):
        PROBLEM_CATALOG["internal_error"] = PROBLEM_CATALOG["payload_invalid"]  # ty: ignore[invalid-assignment]


def test_problem_response_uses_rfc9457_and_never_leaks_exception_text() -> None:
    response = problem_response(
        RuntimeError("secret driver path /var/lib/inari/receipt.jpg"),
        correlation_id="corr-500",
    )

    assert response.status_code == 500
    assert response.media_type == PROBLEM_MEDIA_TYPE
    assert response.body is not None
    assert b"secret driver" not in response.body
    assert b"/var/lib" not in response.body
    assert ErrorResponse.model_validate_json(bytes(response.body))


def test_problem_response_maps_domain_failure_and_correlation_header() -> None:
    response = problem_response(
        DomainFailure("device_unavailable"),
        correlation_id="corr-503",
    )

    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.body is not None
    assert b'"error_code":"device_unavailable"' in response.body
    assert b'"correlation_id":"corr-503"' in response.body


def test_openapi_helper_describes_problem_json_for_each_status() -> None:
    responses = problem_responses(409, 422)

    assert set(responses) == {409, 422}
    assert responses[409]["model"] is ErrorResponse
    assert responses[409]["content"][PROBLEM_MEDIA_TYPE]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


def test_openapi_defaults_include_expired_problem_status() -> None:
    assert {404, 405, 410, 413, 415} <= set(problem_responses())


def test_error_schema_enforces_problem_codes_urns_and_bounds() -> None:
    document = problem_from_failure(
        DomainFailure("queue_full", details=ProblemDetails(retry_after_seconds=30)),
        correlation_id="corr-schema",
    )
    assert ErrorResponse.model_validate(document.to_dict())

    invalid = document.to_dict()
    invalid["type"] = "https://example.test/problem"
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate(invalid)

    for field, value in (
        ("error_code", "invented_failure"),
        ("status", 399),
        ("correlation_id", "not safe"),
        ("retryable", "true"),
    ):
        invalid = document.to_dict()
        invalid[field] = value
        with pytest.raises(ValidationError):
            ErrorResponse.model_validate(invalid)

    with pytest.raises(ValidationError):
        ProblemDetailsResponse(retry_after_seconds=86_401)
    with pytest.raises(ValidationError):
        ProblemDetailsResponse(retry_after_seconds=True)
    with pytest.raises(ValidationError):
        FieldViolationResponse(
            pointer="/" + "x" * 512,
            code="invalid",
            message_key="field_invalid",
        )
    with pytest.raises(ValidationError):
        ProblemDetailsResponse(
            field_violations=[
                {
                    "pointer": f"/{index}",
                    "code": "invalid",
                    "message_key": "field_invalid",
                }
                for index in range(33)
            ]
        )

    with pytest.raises(ValueError, match="400 through 599"):
        problem_responses(200)


def test_install_problem_handlers_registers_domain_and_unexpected_handlers() -> None:
    app = FastAPI()
    install_problem_handlers(app)

    assert {
        AgentError,
        DomainFailure,
        Exception,
        IngressError,
        RequestValidationError,
        StarletteHTTPException,
    } <= set(app.exception_handlers)


def test_installed_handlers_return_problem_json_for_framework_failures() -> None:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/items/{item_id}", responses=problem_responses(422))
    async def get_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    client = TestClient(app, raise_server_exceptions=False)
    validation = client.get("/items/not-an-integer", headers={"X-Request-ID": "req-1"})
    missing = client.get("/missing")

    assert validation.status_code == 422
    assert validation.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert validation.headers["x-correlation-id"] == "req-1"
    assert validation.json()["error_code"] == "payload_invalid"
    assert validation.json()["details"]["field_violations"]
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "resource_not_found"


def test_problem_openapi_declares_only_problem_json_for_problem_responses() -> None:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/item", responses=problem_responses(409))
    async def get_item() -> dict[str, bool]:
        return {"ok": True}

    response = app.openapi()["paths"]["/item"]["get"]["responses"]["409"]
    assert set(response["content"]) == {PROBLEM_MEDIA_TYPE}
