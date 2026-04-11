from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from .dependencies import get_runtime
from .exceptions import AgentError
from .models import (
    DeviceCommandKind,
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
from .runtime.manager import AgentRuntime
from .runtime.models import JobState

SERVICE_NAME = "IoT Agent"
API_VERSION = "1.7.0a1"

router = APIRouter()
system_router = APIRouter(prefix="/system", tags=["system"])
devices_router = APIRouter(prefix="/devices", tags=["devices"])
jobs_router = APIRouter(tags=["jobs"])
events_router = APIRouter(tags=["events"])

RuntimeDependency = Annotated[AgentRuntime, Depends(get_runtime)]


@system_router.get("/status", response_model=SystemStatusResponse)
async def system_status(runtime: RuntimeDependency) -> SystemStatusResponse:
    devices = list(runtime.list_devices())
    return SystemStatusResponse(
        service=ServiceDescriptorResponse(name=SERVICE_NAME, version=API_VERSION),
        devices=DeviceDirectorySummaryResponse.from_devices(devices),
        queue=QueueSummaryResponse.from_counts(dict(runtime.queue_counts())),
        supported_content_kinds=tuple(PrintContentKind),
        supported_device_commands=tuple(DeviceCommandKind),
    )


@devices_router.get("", response_model=DeviceDirectoryResponse)
async def list_devices(runtime: RuntimeDependency) -> DeviceDirectoryResponse:
    devices = list(runtime.list_devices())
    return DeviceDirectoryResponse(
        devices=[DeviceResponse.from_domain(device) for device in devices],
        summary=DeviceDirectorySummaryResponse.from_devices(devices),
    )


@devices_router.get("/{device_id}", response_model=DeviceResourceResponse)
async def get_device(device_id: str, runtime: RuntimeDependency) -> DeviceResourceResponse:
    device = runtime.get_device(device_id)
    if device is None:
        raise AgentError("DEVICE_NOT_FOUND", f"Device {device_id!r} was not found.", status_code=404)
    return DeviceResourceResponse(device=DeviceResponse.from_domain(device))


@devices_router.get("/{device_id}/events", response_model=DeviceEventCollectionResponse)
async def list_device_events(
    device_id: str,
    runtime: RuntimeDependency,
    limit: int = Query(default=50, ge=1, le=500),
) -> DeviceEventCollectionResponse:
    device = runtime.get_device(device_id)
    if device is None:
        raise AgentError("DEVICE_NOT_FOUND", f"Device {device_id!r} was not found.", status_code=404)
    events = runtime.list_device_events(device_id, limit=limit)
    return DeviceEventCollectionResponse(events=[RuntimeEventResponse.from_domain(event) for event in events])


@jobs_router.post("/print-jobs", response_model=JobResourceResponse, status_code=202)
async def submit_print_job(
    request: "PrintJobRequest",
    runtime: RuntimeDependency,
) -> JobResourceResponse:
    job = await runtime.submit_print_job(request)
    return JobResourceResponse(job=JobResponse.from_domain(job))


@jobs_router.post("/device-commands", response_model=JobResourceResponse, status_code=202)
async def submit_device_command(
    request: DeviceCommandRequest,
    runtime: RuntimeDependency,
) -> JobResourceResponse:
    job = await runtime.submit_command(request)
    return JobResourceResponse(job=JobResponse.from_domain(job))


@jobs_router.get("/jobs", response_model=JobCollectionResponse)
async def list_jobs(
    runtime: RuntimeDependency,
    state: JobState | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> JobCollectionResponse:
    jobs = list(runtime.list_jobs(state=state, limit=limit))
    return JobCollectionResponse(
        jobs=[JobResponse.from_domain(job) for job in jobs],
        queue=QueueSummaryResponse.from_counts(dict(runtime.queue_counts())),
    )


@jobs_router.get("/jobs/{job_id}", response_model=JobResourceResponse)
async def get_job(job_id: str, runtime: RuntimeDependency) -> JobResourceResponse:
    job = runtime.get_job(job_id)
    if job is None:
        raise AgentError("JOB_NOT_FOUND", f"Job {job_id!r} was not found.", status_code=404)
    return JobResourceResponse(job=JobResponse.from_domain(job))


@jobs_router.get("/jobs/{job_id}/history", response_model=JobHistoryResponse)
async def get_job_history(job_id: str, runtime: RuntimeDependency) -> JobHistoryResponse:
    job = runtime.get_job(job_id)
    if job is None:
        raise AgentError("JOB_NOT_FOUND", f"Job {job_id!r} was not found.", status_code=404)
    attempts = runtime.list_job_attempts(job_id)
    events = runtime.list_job_history(job_id)
    return JobHistoryResponse(
        job=JobResponse.from_domain(job),
        attempts=[JobAttemptResponse.from_domain(attempt) for attempt in attempts],
        events=[RuntimeEventResponse.from_domain(event) for event in events],
    )


@jobs_router.post("/jobs/{job_id}/cancel", response_model=JobResourceResponse)
async def cancel_job(job_id: str, runtime: RuntimeDependency) -> JobResourceResponse:
    job = await runtime.cancel_job(job_id)
    return JobResourceResponse(job=JobResponse.from_domain(job))


@events_router.websocket("/events")
async def stream_events(websocket: WebSocket, runtime: RuntimeDependency) -> None:
    await websocket.accept()
    try:
        async for event in runtime.event_hub.iter_events():
            await websocket.send_json(RuntimeEventResponse.from_domain(event).model_dump(mode="json"))
    except WebSocketDisconnect:
        return


router.include_router(system_router)
router.include_router(devices_router)
router.include_router(jobs_router)
router.include_router(events_router)
