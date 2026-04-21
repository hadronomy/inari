from __future__ import annotations

from ..devices.service import DeviceCatalog
from ..events import EventHub
from ..models import JobEventRecord, JobKind, JobRecord, JobState
from ..repositories import JobRepository
from .operations import (
    QueuedDeviceCommandOperation,
    QueuedPrintOperation,
    serialize_device_command_operation,
    serialize_print_operation,
)
from ...config import AgentSettings
from ...core.exceptions import AgentError


class JobService:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        job_repository: JobRepository,
        device_catalog: DeviceCatalog,
        event_hub: EventHub,
    ) -> None:
        self.settings = settings
        self.job_repository = job_repository
        self.device_catalog = device_catalog
        self.event_hub = event_hub

    async def enqueue_print(self, operation: QueuedPrintOperation) -> JobRecord:
        device = await self.device_catalog.resolve_target(operation.target)
        canonical = operation.with_resolved_printer(
            device_id=device.id, printer_name=device.name
        )
        job = self.job_repository.create(
            kind=JobKind.PRINT,
            operation="print_job",
            device=device,
            request_payload=serialize_print_operation(canonical),
            request_metadata=canonical.job.metadata,
            content_kind=canonical.job.content.kind.value,
            command_kind=None,
            max_attempts=self.settings.job_max_attempts,
        )
        await self.publish_event("job.queued", job)
        return job

    async def enqueue_command(
        self, operation: QueuedDeviceCommandOperation
    ) -> JobRecord:
        device = await self.device_catalog.resolve_target(operation.target)
        canonical = operation.with_resolved_printer(
            device_id=device.id, printer_name=device.name
        )
        job = self.job_repository.create(
            kind=JobKind.COMMAND,
            operation=canonical.command.kind.value,
            device=device,
            request_payload=serialize_device_command_operation(canonical),
            request_metadata=canonical.metadata,
            content_kind=None,
            command_kind=canonical.command.kind.value,
            max_attempts=self.settings.job_max_attempts,
        )
        await self.publish_event("job.queued", job)
        return job

    def list_jobs(
        self, *, state: JobState | None = None, limit: int = 100
    ) -> tuple[JobRecord, ...]:
        return self.job_repository.list(state=state, limit=limit)

    def get_job(self, job_id: str) -> JobRecord | None:
        return self.job_repository.get(job_id)

    def list_job_history(self, job_id: str) -> tuple[JobEventRecord, ...]:
        return self.job_repository.list_events(job_id)

    def list_job_attempts(self, job_id: str):
        return self.job_repository.list_attempts(job_id)

    def queue_counts(self):
        return self.job_repository.queue_counts()

    async def cancel(self, job_id: str) -> JobRecord:
        job = self.job_repository.cancel(job_id)
        if job is None:
            current = self.job_repository.get(job_id)
            if current is None:
                raise AgentError(
                    "JOB_NOT_FOUND", f"Job {job_id!r} was not found.", status_code=404
                )
            raise AgentError(
                "JOB_NOT_CANCELLABLE",
                f"Job {job_id!r} can no longer be cancelled once it is running or finished.",
                status_code=409,
            )
        await self.publish_event("job.cancelled", job)
        return job

    async def publish_event(self, event_type: str, job: JobRecord) -> None:
        event = self.job_repository.append_event(
            job_id=job.id,
            event_type=event_type,
            payload=_job_event_payload(job),
        )
        await self.event_hub.publish(event)


def _job_event_payload(job: JobRecord) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": job.id,
        "kind": job.kind.value,
        "operation": job.operation,
        "state": job.state.value,
        "device_id": job.device_id,
        "device_name": job.device_name,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
    }
    if job.content_kind is not None:
        payload["content_kind"] = job.content_kind
    if job.command_kind is not None:
        payload["command_kind"] = job.command_kind
    if job.last_error_code is not None:
        payload["error_code"] = job.last_error_code
    if job.last_error_detail is not None:
        payload["error_detail"] = job.last_error_detail
    if job.result_payload is not None:
        payload["result"] = dict(job.result_payload)
    return payload
