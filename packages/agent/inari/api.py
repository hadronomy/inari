from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect

from .dependencies import (
    get_authorization_service,
    get_device_catalog,
    get_event_hub,
    get_gateway_service,
    get_job_service,
)
from .device_commands import DeviceCommandKind
from .exceptions import AgentError
from .gateway.service import GatewayService
from .models import (
    AuthenticatedPrincipalResponse,
    DeviceCommandRequest,
    DeviceDirectoryResponse,
    DeviceDirectorySummaryResponse,
    DeviceEventCollectionResponse,
    DeviceResourceResponse,
    DeviceResponse,
    GatewayIdentityResponse,
    GatewayUpstreamStatusResponse,
    JobAttemptResponse,
    JobCollectionResponse,
    JobHistoryResponse,
    LiveEventUpdateResponse,
    LiveSnapshotResponse,
    LocalTokenRequest,
    JobResourceResponse,
    JobResponse,
    PrincipalResponse,
    PrintJobRequest,
    QueueSummaryResponse,
    RuntimeEventResponse,
    ServiceDescriptorResponse,
    SystemStatusResponse,
    TokenResponse,
)
from .print_jobs import PrintContentKind
from .runtime.events import EventHub
from .runtime.models import JobState
from .runtime.services import DeviceCatalog, JobService
from .security.auth import AuthorizationService
from .security.models import AccessScope, AuthenticatedPrincipal
from .version import API_VERSION, SERVICE_NAME

router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])
gateway_router = APIRouter(prefix="/gateway", tags=["gateway"])
system_router = APIRouter(prefix="/system", tags=["system"])
devices_router = APIRouter(prefix="/devices", tags=["devices"])
jobs_router = APIRouter(tags=["jobs"])
events_router = APIRouter(tags=["events"])

DeviceCatalogDependency = Annotated[DeviceCatalog, Depends(get_device_catalog)]
JobServiceDependency = Annotated[JobService, Depends(get_job_service)]
EventHubDependency = Annotated[EventHub, Depends(get_event_hub)]
AuthorizationServiceDependency = Annotated[
    AuthorizationService, Depends(get_authorization_service)
]
GatewayServiceDependency = Annotated[GatewayService, Depends(get_gateway_service)]


def build_system_status_response(
    device_catalog: DeviceCatalog,
    job_service: JobService,
) -> SystemStatusResponse:
    devices = list(device_catalog.list_devices())
    return SystemStatusResponse(
        service=ServiceDescriptorResponse(name=SERVICE_NAME, version=API_VERSION),
        devices=DeviceDirectorySummaryResponse.from_devices(devices),
        queue=QueueSummaryResponse.from_counts(dict(job_service.queue_counts())),
        supported_content_kinds=tuple(PrintContentKind),
        supported_device_commands=tuple(DeviceCommandKind),
    )


def _require_scopes(
    authorization_service: AuthorizationService,
    principal: AuthenticatedPrincipal,
    *scopes: AccessScope,
) -> AuthenticatedPrincipal:
    return authorization_service.require_scopes(principal, scopes)


def _current_principal(
    authorization_service: AuthorizationService, connection: Request
) -> AuthenticatedPrincipal:
    return authorization_service.authenticate_connection(connection)


@auth_router.post("/local-token", response_model=TokenResponse)
async def issue_local_token(
    request: LocalTokenRequest,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> TokenResponse:
    token = authorization_service.issue_loopback_token(
        connection,
        client_name=request.client_name,
        requested_scopes=request.requested_scopes,
    )
    return TokenResponse.from_issued_token(token)


@auth_router.get("/me", response_model=AuthenticatedPrincipalResponse)
async def auth_me(
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> AuthenticatedPrincipalResponse:
    principal = _current_principal(authorization_service, connection)
    return AuthenticatedPrincipalResponse(
        principal=PrincipalResponse.from_principal(principal)
    )


@gateway_router.get("/identity", response_model=GatewayIdentityResponse)
async def gateway_identity(
    gateway_service: GatewayServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> GatewayIdentityResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.ADMIN_READ)
    identity = gateway_service.get_identity()
    return GatewayIdentityResponse.from_identity(
        identity,
        mode=gateway_service.settings.gateway_mode,
        exposure=gateway_service.settings.gateway_exposure,
    )


@gateway_router.get("/upstream/status", response_model=GatewayUpstreamStatusResponse)
async def gateway_upstream_status(
    gateway_service: GatewayServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> GatewayUpstreamStatusResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.ADMIN_READ)
    return GatewayUpstreamStatusResponse.from_status(
        gateway_service.get_upstream_status()
    )


@system_router.get("/status", response_model=SystemStatusResponse)
async def system_status(
    device_catalog: DeviceCatalogDependency,
    job_service: JobServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> SystemStatusResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.SYSTEM_READ)
    return build_system_status_response(device_catalog, job_service)


def _device_response(device_catalog: DeviceCatalog, device) -> DeviceResponse:
    return DeviceResponse.from_domain(
        device,
        driver_metadata=device_catalog.get_driver_metadata(
            kind=device.kind,
            driver_key=device.driver_key,
        ),
    )


@devices_router.get("", response_model=DeviceDirectoryResponse)
async def list_devices(
    device_catalog: DeviceCatalogDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> DeviceDirectoryResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.DEVICES_READ)
    devices = list(device_catalog.list_devices())
    return DeviceDirectoryResponse(
        devices=[_device_response(device_catalog, device) for device in devices],
        summary=DeviceDirectorySummaryResponse.from_devices(devices),
    )


@devices_router.get("/{device_id}", response_model=DeviceResourceResponse)
async def get_device(
    device_id: str,
    device_catalog: DeviceCatalogDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> DeviceResourceResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.DEVICES_READ)
    device = device_catalog.get_device(device_id)
    if device is None:
        raise AgentError(
            "DEVICE_NOT_FOUND", f"Device {device_id!r} was not found.", status_code=404
        )
    return DeviceResourceResponse(device=_device_response(device_catalog, device))


@devices_router.get("/{device_id}/events", response_model=DeviceEventCollectionResponse)
async def list_device_events(
    device_id: str,
    device_catalog: DeviceCatalogDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> DeviceEventCollectionResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.EVENTS_READ)
    device = device_catalog.get_device(device_id)
    if device is None:
        raise AgentError(
            "DEVICE_NOT_FOUND", f"Device {device_id!r} was not found.", status_code=404
        )
    events = device_catalog.list_device_events(device_id, limit=limit)
    return DeviceEventCollectionResponse(
        events=[RuntimeEventResponse.from_domain(event) for event in events]
    )


@jobs_router.post("/print-jobs", response_model=JobResourceResponse, status_code=202)
async def submit_print_job(
    request: PrintJobRequest,
    job_service: JobServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> JobResourceResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.JOBS_SUBMIT)
    job = await job_service.enqueue_print(request.to_operation())
    return JobResourceResponse(job=JobResponse.from_domain(job))


@jobs_router.post(
    "/device-commands", response_model=JobResourceResponse, status_code=202
)
async def submit_device_command(
    request: DeviceCommandRequest,
    job_service: JobServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> JobResourceResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.COMMANDS_EXECUTE)
    job = await job_service.enqueue_command(request.to_operation())
    return JobResourceResponse(job=JobResponse.from_domain(job))


@jobs_router.get("/jobs", response_model=JobCollectionResponse)
async def list_jobs(
    job_service: JobServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
    state: JobState | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> JobCollectionResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.JOBS_READ)
    jobs = list(job_service.list_jobs(state=state, limit=limit))
    return JobCollectionResponse(
        jobs=[JobResponse.from_domain(job) for job in jobs],
        queue=QueueSummaryResponse.from_counts(dict(job_service.queue_counts())),
    )


@jobs_router.get("/jobs/{job_id}", response_model=JobResourceResponse)
async def get_job(
    job_id: str,
    job_service: JobServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> JobResourceResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.JOBS_READ)
    job = job_service.get_job(job_id)
    if job is None:
        raise AgentError(
            "JOB_NOT_FOUND", f"Job {job_id!r} was not found.", status_code=404
        )
    return JobResourceResponse(job=JobResponse.from_domain(job))


@jobs_router.get("/jobs/{job_id}/history", response_model=JobHistoryResponse)
async def get_job_history(
    job_id: str,
    job_service: JobServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> JobHistoryResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.JOBS_READ)
    job = job_service.get_job(job_id)
    if job is None:
        raise AgentError(
            "JOB_NOT_FOUND", f"Job {job_id!r} was not found.", status_code=404
        )
    attempts = job_service.list_job_attempts(job_id)
    events = job_service.list_job_history(job_id)
    return JobHistoryResponse(
        job=JobResponse.from_domain(job),
        attempts=[JobAttemptResponse.from_domain(attempt) for attempt in attempts],
        events=[RuntimeEventResponse.from_domain(event) for event in events],
    )


@jobs_router.post("/jobs/{job_id}/cancel", response_model=JobResourceResponse)
async def cancel_job(
    job_id: str,
    job_service: JobServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> JobResourceResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.JOBS_SUBMIT)
    job = await job_service.cancel(job_id)
    return JobResourceResponse(job=JobResponse.from_domain(job))


@events_router.websocket("/events")
async def stream_events(
    websocket: WebSocket,
    device_catalog: DeviceCatalogDependency,
    job_service: JobServiceDependency,
    event_hub: EventHubDependency,
    authorization_service: AuthorizationServiceDependency,
) -> None:
    try:
        principal = authorization_service.authenticate_connection(websocket)
        authorization_service.require_scopes(principal, (AccessScope.EVENTS_READ,))
    except AgentError as exc:
        await websocket.close(code=4401, reason=exc.code)
        return
    await websocket.accept()
    await websocket.send_json(
        LiveSnapshotResponse(
            status=build_system_status_response(device_catalog, job_service),
        ).model_dump(mode="json")
    )
    try:
        async for event in event_hub.iter_events():
            await websocket.send_json(
                LiveEventUpdateResponse(
                    status=build_system_status_response(device_catalog, job_service),
                    event=RuntimeEventResponse.from_domain(event),
                ).model_dump(mode="json")
            )
    except WebSocketDisconnect:
        return


router.include_router(auth_router)
router.include_router(gateway_router)
router.include_router(system_router)
router.include_router(devices_router)
router.include_router(jobs_router)
router.include_router(events_router)
