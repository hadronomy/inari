from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect

from .dependencies import (
    get_authorization_service,
    get_device_catalog,
    get_event_hub,
    get_gateway_service,
    get_job_service,
    get_onboarding_service,
    get_standalone_trust_service,
)
from ..core.exceptions import AgentError
from ..gateway.service import GatewayService
from ..gateway.onboarding import ManagedOnboardingService
from ..printing.commands import DeviceCommandKind
from ..printing.jobs import PrintContentKind
from ..runtime.events import EventHub
from ..runtime.models import JobState
from ..runtime.devices.service import DeviceCatalog
from ..runtime.jobs.service import JobService
from ..security.auth import AuthorizationService, connection_origin
from ..security.local_trust import StandaloneTrustService
from ..security.models import AccessScope, AuthenticatedPrincipal
from ..core.version import API_VERSION, SERVICE_NAME
from .schemas import (
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
    LocalChallengeRequest,
    LocalChallengeResponse,
    LocalPairingCompleteRequest,
    LocalPairingCompleteResponse,
    LocalPairingRevokeRequest,
    LocalPairingStartResponse,
    LiveSnapshotResponse,
    LocalTokenRequest,
    LocalTrustStatusResponse,
    JobResourceResponse,
    JobResponse,
    PrincipalResponse,
    PrintJobRequest,
    QueueSummaryResponse,
    RuntimeEventResponse,
    ManagedOnboardingDeviceConfirmationRequest,
    ManagedOnboardingInvitationRequest,
    ManagedOnboardingPreviewResponse,
    ManagedOnboardingStartResponse,
    ManagedOnboardingStatusResponse,
    ServiceDescriptorResponse,
    SystemStatusResponse,
    TokenResponse,
    TrustedLocalClientResponse,
)

router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])
gateway_router = APIRouter(prefix="/gateway", tags=["gateway"])
onboarding_router = APIRouter(prefix="/onboarding", tags=["onboarding"])
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
OnboardingServiceDependency = Annotated[
    ManagedOnboardingService, Depends(get_onboarding_service)
]
StandaloneTrustServiceDependency = Annotated[
    StandaloneTrustService, Depends(get_standalone_trust_service)
]


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
    token = authorization_service.issue_local_token(
        connection,
        client_name=request.client_name,
        requested_scopes=request.requested_scopes,
        attestation=(
            request.attestation.to_domain() if request.attestation is not None else None
        ),
    )
    return TokenResponse.from_issued_token(token)


@auth_router.post("/local-challenge", response_model=LocalChallengeResponse)
async def issue_local_challenge(
    request: LocalChallengeRequest,
    authorization_service: AuthorizationServiceDependency,
    local_trust_service: StandaloneTrustServiceDependency,
    connection: Request,
) -> LocalChallengeResponse:
    authorization_service.policy_service.assert_loopback_client(connection)
    challenge = local_trust_service.issue_challenge(
        purpose=request.purpose,
        client_id=request.client_id,
    )
    return LocalChallengeResponse.from_challenge(challenge)


@auth_router.get("/local-trust", response_model=LocalTrustStatusResponse)
async def local_trust_status(
    authorization_service: AuthorizationServiceDependency,
    local_trust_service: StandaloneTrustServiceDependency,
    connection: Request,
) -> LocalTrustStatusResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.ADMIN_READ)
    return LocalTrustStatusResponse.from_state(
        local_trust_service.current_state(),
        pairing_required=local_trust_service.pairing_required,
    )


@auth_router.post("/pairing/start", response_model=LocalPairingStartResponse)
async def start_local_pairing(
    authorization_service: AuthorizationServiceDependency,
    local_trust_service: StandaloneTrustServiceDependency,
    connection: Request,
) -> LocalPairingStartResponse:
    authorization_service.policy_service.assert_loopback_client(connection)
    result = local_trust_service.start_pairing()
    return LocalPairingStartResponse(
        pairing_secret=result.secret,
        expires_at=result.expires_at,
    )


@auth_router.post("/pairing/complete", response_model=LocalPairingCompleteResponse)
async def complete_local_pairing(
    request: LocalPairingCompleteRequest,
    authorization_service: AuthorizationServiceDependency,
    local_trust_service: StandaloneTrustServiceDependency,
    connection: Request,
) -> LocalPairingCompleteResponse:
    authorization_service.policy_service.assert_loopback_client(connection)
    client = local_trust_service.complete_pairing(
        client_id=request.client_id,
        client_name=request.client_name,
        public_key_pem=request.public_key_pem,
        pairing_secret=request.pairing_secret,
        attestation=request.attestation.to_domain(),
        origin=request.origin or connection_origin(connection),
    )
    return LocalPairingCompleteResponse(
        client=TrustedLocalClientResponse.from_domain(client)
    )


@auth_router.post("/pairing/rotate", response_model=LocalPairingStartResponse)
async def rotate_local_pairing_secret(
    authorization_service: AuthorizationServiceDependency,
    local_trust_service: StandaloneTrustServiceDependency,
    connection: Request,
) -> LocalPairingStartResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.ADMIN_WRITE)
    result = local_trust_service.start_pairing(allow_when_paired=True)
    return LocalPairingStartResponse(
        pairing_secret=result.secret,
        expires_at=result.expires_at,
    )


@auth_router.post("/pairing/revoke", response_model=LocalTrustStatusResponse)
async def revoke_local_pairing(
    request: LocalPairingRevokeRequest,
    authorization_service: AuthorizationServiceDependency,
    local_trust_service: StandaloneTrustServiceDependency,
    connection: Request,
) -> LocalTrustStatusResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.ADMIN_WRITE)
    state = local_trust_service.revoke_client(request.client_id)
    return LocalTrustStatusResponse.from_state(
        state,
        pairing_required=local_trust_service.pairing_required,
    )


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


def _onboarding_status_response(
    onboarding_service: ManagedOnboardingService,
    device_catalog: DeviceCatalog,
):
    status = onboarding_service.status()
    return ManagedOnboardingStatusResponse.from_domain(
        status,
        devices=[_device_response(device_catalog, device) for device in status.devices],
    )


@onboarding_router.post(
    "/managed/preview", response_model=ManagedOnboardingPreviewResponse
)
async def preview_managed_onboarding(
    request: ManagedOnboardingInvitationRequest,
    onboarding_service: OnboardingServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> ManagedOnboardingPreviewResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.ADMIN_READ)
    preview = await onboarding_service.preview(
        request.invitation, controller_url=request.controller_url
    )
    return ManagedOnboardingPreviewResponse.from_domain(preview)


@onboarding_router.post("/managed/start", response_model=ManagedOnboardingStartResponse)
async def start_managed_onboarding(
    request: ManagedOnboardingInvitationRequest,
    onboarding_service: OnboardingServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> ManagedOnboardingStartResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.ADMIN_WRITE)
    preview, restart_required = await onboarding_service.start(
        request.invitation, controller_url=request.controller_url
    )
    return ManagedOnboardingStartResponse.from_start(
        preview, restart_required=restart_required
    )


@onboarding_router.get("/status", response_model=ManagedOnboardingStatusResponse)
async def managed_onboarding_status(
    onboarding_service: OnboardingServiceDependency,
    device_catalog: DeviceCatalogDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> ManagedOnboardingStatusResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.ADMIN_READ)
    return _onboarding_status_response(onboarding_service, device_catalog)


@onboarding_router.post(
    "/devices/confirm", response_model=ManagedOnboardingStatusResponse
)
async def confirm_onboarding_devices(
    request: ManagedOnboardingDeviceConfirmationRequest,
    onboarding_service: OnboardingServiceDependency,
    device_catalog: DeviceCatalogDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> ManagedOnboardingStatusResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.ADMIN_WRITE)
    onboarding_service.confirm_devices(
        device_ids=request.device_ids,
        labels=request.labels,
        default_printer_device_id=request.default_printer_device_id,
    )
    return _onboarding_status_response(onboarding_service, device_catalog)


@onboarding_router.post("/cancel", response_model=ManagedOnboardingStatusResponse)
async def cancel_managed_onboarding(
    onboarding_service: OnboardingServiceDependency,
    authorization_service: AuthorizationServiceDependency,
    connection: Request,
) -> ManagedOnboardingStatusResponse:
    principal = _current_principal(authorization_service, connection)
    _require_scopes(authorization_service, principal, AccessScope.ADMIN_WRITE)
    return ManagedOnboardingStatusResponse.from_domain(
        onboarding_service.cancel(), devices=[]
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
router.include_router(onboarding_router)
router.include_router(system_router)
router.include_router(devices_router)
router.include_router(jobs_router)
router.include_router(events_router)
