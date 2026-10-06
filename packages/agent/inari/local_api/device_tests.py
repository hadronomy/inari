from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from pydantic import Field

from ..application.container import AgentContainer
from ..core.failures import DomainFailure, ProblemCode
from ..device_tests import (
    DeviceTestRequest,
    DeviceTestService,
    PhysicalCheckAnswer,
    TestState,
)
from ..device_tests.models import DeviceTestRecord
from ..device_tests.pattern import (
    CODE_VALUE,
    PATTERN_DIGEST,
    PATTERN_VERSION,
    REQUIRED_CHECKS,
)
from .dependencies import get_container
from .device_work import authorized_device_work_request, idempotency_key_from_request
from .problem_handlers import problem_responses
from .schemas.base import APIModel


Identifier = Annotated[
    str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
]


class DeviceTestInput(APIModel):
    contract_major: Literal[1]
    device_test_id: Identifier
    device_id: Identifier
    binding_revision_id: Identifier


class PhysicalChecksInput(APIModel):
    text: PhysicalCheckAnswer
    accents: PhysicalCheckAnswer
    code128: PhysicalCheckAnswer
    qr: PhysicalCheckAnswer
    feed_and_cut: PhysicalCheckAnswer


class DeviceTestResponse(APIModel):
    ok: Literal[True] = True
    contract_major: Literal[1] = 1
    device_test_id: str
    device_id: str
    binding_revision_id: str
    state: TestState
    state_version: int
    pattern_version: str = PATTERN_VERSION
    test_pattern_digest: str = PATTERN_DIGEST
    code_value: str = CODE_VALUE
    required_checks: tuple[str, ...] = REQUIRED_CHECKS
    accepted_at: datetime
    started_at: datetime | None
    terminal_at: datetime | None
    output_evidence: str | None
    platform_job_id: str | None
    error_code: str | None
    checks: dict[str, str] | None
    signed_result: dict[str, object] | None
    replayed: bool = False

    @classmethod
    def from_domain(cls, record: DeviceTestRecord, *, replayed: bool = False):
        return cls(
            device_test_id=record.test_id,
            device_id=record.device_id,
            binding_revision_id=record.binding_revision_id,
            state=record.state,
            state_version=record.state_version,
            accepted_at=record.accepted_at,
            started_at=record.started_at,
            terminal_at=record.terminal_at,
            output_evidence=record.output_evidence,
            platform_job_id=record.platform_job_id,
            error_code=record.error_code,
            checks=record.checks,
            signed_result=record.signed_result,
            replayed=replayed,
        )


def get_device_test_service(
    container: Annotated[AgentContainer, Depends(get_container)],
) -> DeviceTestService:
    return container.device_test_service


Service = Annotated[DeviceTestService, Depends(get_device_test_service)]
router = APIRouter(tags=["Device Tests"])


@router.post(
    "/v1/device-tests",
    response_model=DeviceTestResponse,
    status_code=202,
    responses={
        200: {"description": "Existing Device Test", "model": DeviceTestResponse},
        **problem_responses(400, 401, 403, 404, 409, 422, 500, 503),
    },
)
async def submit_device_test(
    body: DeviceTestInput,
    connection: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    service: Service,
) -> DeviceTestResponse:
    authorization = authorized_device_work_request(connection)
    if body.device_test_id != idempotency_key_from_request(connection):
        raise DomainFailure(ProblemCode.PAYLOAD_INVALID)
    accepted = await service.submit(
        DeviceTestRequest(
            body.device_test_id, body.device_id, body.binding_revision_id
        ),
        authorization,
    )
    if accepted.permit is not None:
        background_tasks.add_task(service.execute, accepted)
    if accepted.replayed:
        response.status_code = 200
    return DeviceTestResponse.from_domain(accepted.record, replayed=accepted.replayed)


@router.get(
    "/v1/device-tests/{test_id}",
    response_model=DeviceTestResponse,
    responses=problem_responses(401, 403, 404, 409, 422, 500, 503),
)
async def get_device_test(
    test_id: Identifier, connection: Request, service: Service
) -> DeviceTestResponse:
    return DeviceTestResponse.from_domain(
        service.get(test_id, authorized_device_work_request(connection))
    )


@router.post(
    "/v1/device-tests/{test_id}/checks",
    response_model=DeviceTestResponse,
    responses=problem_responses(400, 401, 403, 404, 409, 422, 500, 503),
)
async def finalize_device_test(
    test_id: Identifier,
    body: PhysicalChecksInput,
    connection: Request,
    service: Service,
) -> DeviceTestResponse:
    return DeviceTestResponse.from_domain(
        service.finalize(
            test_id,
            body.model_dump(mode="json"),
            authorized_device_work_request(connection),
        )
    )
