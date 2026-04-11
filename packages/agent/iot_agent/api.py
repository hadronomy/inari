from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from .dependencies import get_device_catalog, get_event_hub, get_job_service
from .device_commands import DeviceCommandKind
from .exceptions import AgentError
from .models import (
    DeviceCommandRequest,
    DeviceDirectoryResponse,
    DeviceDirectorySummaryResponse,
    DeviceEventCollectionResponse,
    DeviceResourceResponse,
    DeviceResponse,
    JobAttemptResponse,
    JobCollectionResponse,
    JobHistoryResponse,
    JobResourceResponse,
    JobResponse,
    PrintJobRequest,
    QueueSummaryResponse,
    RuntimeEventResponse,
    ServiceDescriptorResponse,
    SystemStatusResponse,
)
from .print_jobs import PrintContentKind
from .runtime.events import EventHub
from .runtime.models import JobState
from .runtime.services import DeviceCatalog, JobService

SERVICE_NAME = "IoT Agent"
API_VERSION = "1.7.0a2"

router = APIRouter()
system_router = APIRouter(prefix="/system", tags=["system"])
devices_router = APIRouter(prefix="/devices", tags=["devices"])
jobs_router = APIRouter(tags=["jobs"])
events_router = APIRouter(tags=["events"])

DeviceCatalogDependency = Annotated[DeviceCatalog, Depends(get_device_catalog)]
JobServiceDependency = Annotated[JobService, Depends(get_job_service)]
EventHubDependency = Annotated[EventHub, Depends(get_event_hub)]


@system_router.get("/status", response_model=SystemStatusResponse)
async def system_status(
    device_catalog: DeviceCatalogDependency,
    job_service: JobServiceDependency,
) -> SystemStatusResponse:
    devices = list(device_catalog.list_devices())
    return SystemStatusResponse(
        service=ServiceDescriptorResponse(name=SERVICE_NAME, version=API_VERSION),
        devices=DeviceDirectorySummaryResponse.from_devices(devices),
        queue=QueueSummaryResponse.from_counts(dict(job_service.queue_counts())),
        supported_content_kinds=tuple(PrintContentKind),
        supported_device_commands=tuple(DeviceCommandKind),
    )


@devices_router.get("", response_model=DeviceDirectoryResponse)
async def list_devices(device_catalog: DeviceCatalogDependency) -> DeviceDirectoryResponse:
    devices = list(device_catalog.list_devices())
    return DeviceDirectoryResponse(
        devices=[DeviceResponse.from_domain(device) for device in devices],
        summary=DeviceDirectorySummaryResponse.from_devices(devices),
    )


@devices_router.get("/{device_id}", response_model=DeviceResourceResponse)
async def get_device(device_id: str, device_catalog: DeviceCatalogDependency) -> DeviceResourceResponse:
    device = device_catalog.get_device(device_id)
    if device is None:
        raise AgentError("DEVICE_NOT_FOUND", f"Device {device_id!r} was not found.", status_code=404)
    return DeviceResourceResponse(device=DeviceResponse.from_domain(device))


@devices_router.get("/{device_id}/events", response_model=DeviceEventCollectionResponse)
async def list_device_events(
    device_id: str,
    device_catalog: DeviceCatalogDependency,
    limit: int = Query(default=50, ge=1, le=500),
) -> DeviceEventCollectionResponse:
    device = device_catalog.get_device(device_id)
    if device is None:
        raise AgentError("DEVICE_NOT_FOUND", f"Device {device_id!r} was not found.", status_code=404)
    events = device_catalog.list_device_events(device_id, limit=limit)
    return DeviceEventCollectionResponse(events=[RuntimeEventResponse.from_domain(event) for event in events])


@jobs_router.post("/print-jobs", response_model=JobResourceResponse, status_code=202)
async def submit_print_job(
    request: PrintJobRequest,
    job_service: JobServiceDependency,
) -> JobResourceResponse:
    job = await job_service.enqueue_print(request.to_operation())
    return JobResourceResponse(job=JobResponse.from_domain(job))


@jobs_router.post("/device-commands", response_model=JobResourceResponse, status_code=202)
async def submit_device_command(
    request: DeviceCommandRequest,
    job_service: JobServiceDependency,
) -> JobResourceResponse:
    job = await job_service.enqueue_command(request.to_operation())
    return JobResourceResponse(job=JobResponse.from_domain(job))


@jobs_router.get("/jobs", response_model=JobCollectionResponse)
async def list_jobs(
    job_service: JobServiceDependency,
    state: JobState | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> JobCollectionResponse:
    jobs = list(job_service.list_jobs(state=state, limit=limit))
    return JobCollectionResponse(
        jobs=[JobResponse.from_domain(job) for job in jobs],
        queue=QueueSummaryResponse.from_counts(dict(job_service.queue_counts())),
    )


@jobs_router.get("/jobs/{job_id}", response_model=JobResourceResponse)
async def get_job(job_id: str, job_service: JobServiceDependency) -> JobResourceResponse:
    job = job_service.get_job(job_id)
    if job is None:
        raise AgentError("JOB_NOT_FOUND", f"Job {job_id!r} was not found.", status_code=404)
    return JobResourceResponse(job=JobResponse.from_domain(job))


@jobs_router.get("/jobs/{job_id}/history", response_model=JobHistoryResponse)
async def get_job_history(job_id: str, job_service: JobServiceDependency) -> JobHistoryResponse:
    job = job_service.get_job(job_id)
    if job is None:
        raise AgentError("JOB_NOT_FOUND", f"Job {job_id!r} was not found.", status_code=404)
    attempts = job_service.list_job_attempts(job_id)
    events = job_service.list_job_history(job_id)
    return JobHistoryResponse(
        job=JobResponse.from_domain(job),
        attempts=[JobAttemptResponse.from_domain(attempt) for attempt in attempts],
        events=[RuntimeEventResponse.from_domain(event) for event in events],
    )


@jobs_router.post("/jobs/{job_id}/cancel", response_model=JobResourceResponse)
async def cancel_job(job_id: str, job_service: JobServiceDependency) -> JobResourceResponse:
    job = await job_service.cancel(job_id)
    return JobResourceResponse(job=JobResponse.from_domain(job))


@events_router.websocket("/events")
async def stream_events(websocket: WebSocket, event_hub: EventHubDependency) -> None:
    await websocket.accept()
    try:
        async for event in event_hub.iter_events():
            await websocket.send_json(RuntimeEventResponse.from_domain(event).model_dump(mode="json"))
    except WebSocketDisconnect:
        return


router.include_router(system_router)
router.include_router(devices_router)
router.include_router(jobs_router)
router.include_router(events_router)
