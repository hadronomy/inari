from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import Mapping

from ..config import AgentSettings
from ..exceptions import AgentError
from ..models import DeviceCommandKind, DeviceCommandRequest, PrintJobRequest
from ..printer_service import PrinterService
from ..printers import PrintJobResult
from .discovery import DiscoveryCoordinator
from .events import EventHub
from .models import DeviceRecord, JobEventRecord, JobKind, JobRecord, JobState, RuntimeEvent
from .store import RuntimeStore

logger = logging.getLogger(__name__)

RETRYABLE_ERROR_CODES = {
    "DEFAULT_PRINTER_NOT_FOUND",
    "DEVICE_DISCOVERY_FAILED",
    "NO_PRINTER_DRIVER",
    "PRINT_FAILED",
    "PRINTER_NOT_CONFIGURED",
    "PRINTER_NOT_FOUND",
    "PRINTER_OPEN_FAILED",
    "WIN32_UNAVAILABLE",
}


class AgentRuntime:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        printer_service: PrinterService,
        store: RuntimeStore,
        discovery: DiscoveryCoordinator,
        event_hub: EventHub,
    ) -> None:
        self.settings = settings
        self.printer_service = printer_service
        self.store = store
        self.discovery = discovery
        self.event_hub = event_hub
        self._tasks: list[asyncio.Task[None]] = []
        self._device_queues: dict[str, asyncio.Queue[str | None]] = {}
        self._device_workers: dict[str, asyncio.Task[None]] = {}
        self._started = False
        self._stopping = False

    async def start(self) -> None:
        if self._started:
            return
        self.store.initialize()
        await self.discovery.sync_once()
        for job in self.store.recover_expired_jobs():
            await self._publish_job_event("job.recovered", job)
        self._tasks = [
            asyncio.create_task(self._discovery_loop(), name="iot-agent-discovery"),
            asyncio.create_task(self._scheduler_loop(), name="iot-agent-scheduler"),
            asyncio.create_task(self._lease_recovery_loop(), name="iot-agent-lease-recovery"),
        ]
        self._started = True

    async def stop(self) -> None:
        if not self._started or self._stopping:
            return
        self._stopping = True
        for queue in self._device_queues.values():
            await queue.put(None)
        for task in self._tasks:
            task.cancel()
        for task in self._device_workers.values():
            task.cancel()
        for task in (*self._tasks, *self._device_workers.values()):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._device_queues.clear()
        self._device_workers.clear()
        self._tasks.clear()
        self._stopping = False
        self._started = False

    def list_devices(self) -> tuple[DeviceRecord, ...]:
        return self.store.list_devices()

    def get_device(self, device_id: str) -> DeviceRecord | None:
        return self.store.get_device(device_id)

    def list_device_events(self, device_id: str, *, limit: int = 50) -> tuple[RuntimeEvent, ...]:
        return self.store.list_device_events(device_id, limit=limit)

    def list_jobs(self, *, state: JobState | None = None, limit: int = 100) -> tuple[JobRecord, ...]:
        return self.store.list_jobs(state=state, limit=limit)

    def get_job(self, job_id: str) -> JobRecord | None:
        return self.store.get_job(job_id)

    def list_job_history(self, job_id: str) -> tuple[JobEventRecord, ...]:
        return self.store.list_job_events(job_id)

    def list_job_attempts(self, job_id: str):
        return self.store.list_job_attempts(job_id)

    def queue_counts(self) -> Mapping[str, int]:
        return self.store.queue_counts()

    async def submit_print_job(self, request: PrintJobRequest) -> JobRecord:
        device = await self._resolve_target_device(request.target.printer_name, getattr(request.target, "device_id", None))
        canonical_request = request.model_copy(deep=True)
        canonical_request.target.printer_name = device.name
        if hasattr(canonical_request.target, "device_id"):
            canonical_request.target.device_id = device.id
        job = self.store.create_job(
            kind=JobKind.PRINT,
            operation="print_job",
            device_id=device.id,
            device_kind=device.kind,
            device_name=device.name,
            request_payload=canonical_request.model_dump(mode="json"),
            request_metadata=canonical_request.metadata,
            content_kind=canonical_request.content.kind,
            command_kind=None,
            max_attempts=self.settings.job_max_attempts,
        )
        await self._publish_job_event("job.queued", job)
        return job

    async def submit_command(self, request: DeviceCommandRequest) -> JobRecord:
        device = await self._resolve_target_device(request.target.printer_name, getattr(request.target, "device_id", None))
        canonical_request = request.model_copy(deep=True)
        canonical_request.target.printer_name = device.name
        if hasattr(canonical_request.target, "device_id"):
            canonical_request.target.device_id = device.id
        job = self.store.create_job(
            kind=JobKind.COMMAND,
            operation=request.command.kind,
            device_id=device.id,
            device_kind=device.kind,
            device_name=device.name,
            request_payload=canonical_request.model_dump(mode="json"),
            request_metadata=canonical_request.metadata,
            content_kind=None,
            command_kind=request.command.kind,
            max_attempts=self.settings.job_max_attempts,
        )
        await self._publish_job_event("job.queued", job)
        return job

    async def cancel_job(self, job_id: str) -> JobRecord:
        job = self.store.cancel_job(job_id)
        if job is None:
            current = self.store.get_job(job_id)
            if current is None:
                raise AgentError("JOB_NOT_FOUND", f"Job {job_id!r} was not found.", status_code=404)
            raise AgentError(
                "JOB_NOT_CANCELLABLE",
                f"Job {job_id!r} can no longer be cancelled once it is running or finished.",
                status_code=409,
            )
        await self._publish_job_event("job.cancelled", job)
        return job

    async def _resolve_target_device(self, printer_name: str | None, device_id: str | None) -> DeviceRecord:
        if device_id:
            device = self.store.get_device(device_id)
            if device is None:
                await self.discovery.sync_once()
                device = self.store.get_device(device_id)
            if device is None:
                raise AgentError("DEVICE_NOT_FOUND", f"Device {device_id!r} was not found.", status_code=404)
            return device

        selected = self.printer_service.resolve_printer(printer_name)
        selected_device = DeviceRecord.from_printer(selected)
        device = self.store.get_device(selected_device.id)
        if device is not None:
            return device
        await self.discovery.sync_once()
        device = self.store.get_device(selected_device.id)
        if device is None:
            raise AgentError(
                "DEVICE_NOT_FOUND",
                f"Printer {selected.name!r} was not found in the runtime device cache.",
                status_code=404,
            )
        return device

    async def _discovery_loop(self) -> None:
        while True:
            try:
                await self.discovery.sync_once()
            except Exception:
                logger.exception("Device discovery loop failed")
            await asyncio.sleep(self.settings.discovery_poll_interval_seconds)

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                claimed = self.store.claim_runnable_jobs(
                    limit=self.settings.scheduler_batch_size,
                    lease_seconds=self.settings.job_dispatch_lease_seconds,
                )
                for job in claimed:
                    await self._publish_job_event("job.dispatched", job)
                    queue = await self._worker_queue(job.device_id)
                    await queue.put(job.id)
            except Exception:
                logger.exception("Job scheduler loop failed")
            await asyncio.sleep(self.settings.scheduler_poll_interval_seconds)

    async def _lease_recovery_loop(self) -> None:
        while True:
            try:
                recovered = self.store.recover_expired_jobs()
                for job in recovered:
                    event_type = "job.retry_scheduled" if job.state is JobState.RETRY_SCHEDULED else "job.failed"
                    await self._publish_job_event(event_type, job)
            except Exception:
                logger.exception("Lease recovery loop failed")
            await asyncio.sleep(self.settings.job_lease_recovery_interval_seconds)

    async def _worker_queue(self, device_id: str) -> asyncio.Queue[str | None]:
        queue = self._device_queues.get(device_id)
        if queue is not None:
            return queue
        queue = asyncio.Queue()
        self._device_queues[device_id] = queue
        self._device_workers[device_id] = asyncio.create_task(self._device_worker(device_id, queue))
        return queue

    async def _device_worker(self, device_id: str, queue: asyncio.Queue[str | None]) -> None:
        while True:
            job_id = await queue.get()
            if job_id is None:
                return
            try:
                await self._process_job(job_id)
            except Exception:
                logger.exception("Device worker failed for %s", device_id)

    async def _process_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None or job.state is JobState.CANCELLED:
            return

        job, attempt = self.store.start_job_attempt(job.id, lease_seconds=self.settings.job_execution_lease_seconds)
        await self._publish_job_event("job.running", job)

        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._execute_job_sync, job),
                timeout=self.settings.job_execution_timeout_seconds,
            )
        except asyncio.TimeoutError:
            updated = await self._handle_failure(
                job=job,
                attempt_number=attempt.attempt_number,
                exc=AgentError(
                    "JOB_TIMEOUT",
                    f"Job {job.id!r} exceeded the execution timeout of {self.settings.job_execution_timeout_seconds} seconds.",
                    status_code=504,
                ),
            )
            await self._publish_job_event(updated[0], updated[1])
            return
        except Exception as exc:
            updated = await self._handle_failure(job=job, attempt_number=attempt.attempt_number, exc=exc)
            await self._publish_job_event(updated[0], updated[1])
            return
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        completed = self.store.mark_job_succeeded(
            job.id,
            attempt_number=attempt.attempt_number,
            result_payload=_serialize_execution_result(result),
        )
        await self._publish_job_event("job.succeeded", completed)

    async def _handle_failure(
        self,
        *,
        job: JobRecord,
        attempt_number: int,
        exc: Exception,
    ) -> tuple[str, JobRecord]:
        error = _coerce_error(exc)
        if _is_retryable_error(error) and attempt_number < job.max_attempts:
            retry_at = utc_delta(self.settings.job_retry_base_delay_seconds, attempt_number, self.settings.job_retry_max_delay_seconds)
            updated = self.store.mark_job_retry(
                job.id,
                attempt_number=attempt_number,
                next_run_at=retry_at,
                error_code=error.code,
                error_detail=error.message,
            )
            return "job.retry_scheduled", updated
        updated = self.store.mark_job_failed(
            job.id,
            attempt_number=attempt_number,
            error_code=error.code,
            error_detail=error.message,
        )
        return "job.failed", updated

    async def _heartbeat(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(self.settings.job_heartbeat_interval_seconds)
            self.store.renew_job_lease(job_id, lease_seconds=self.settings.job_execution_lease_seconds)

    def _execute_job_sync(self, job: JobRecord) -> PrintJobResult:
        if job.kind is JobKind.PRINT:
            request = PrintJobRequest.model_validate(job.request_payload)
            return self.printer_service.print_job(request.to_domain())

        request = DeviceCommandRequest.model_validate(job.request_payload)
        printer_name = request.target.printer_name
        command = request.command

        if command.kind == DeviceCommandKind.OPEN_CASH_DRAWER:
            return self.printer_service.open_cash_drawer(printer_name=printer_name)
        if command.kind == DeviceCommandKind.PRINT_TEST_PAGE:
            return self.printer_service.print_test_ticket(
                printer_name=printer_name,
                transport=command.transport,
            )
        if command.kind == DeviceCommandKind.FEED_LINES:
            return self.printer_service.feed_lines(command.count, printer_name=printer_name)
        if command.kind == DeviceCommandKind.FEED_DOTS:
            return self.printer_service.feed_dots(command.count, printer_name=printer_name)
        if command.kind == DeviceCommandKind.CUT_PAPER:
            return self.printer_service.cut_paper(printer_name=printer_name, mode=command.mode)
        raise AssertionError(f"Unsupported device command: {command.kind}")

    async def _publish_job_event(self, event_type: str, job: JobRecord) -> None:
        event = self.store.create_job_event(
            job_id=job.id,
            event_type=event_type,
            payload=_job_event_payload(job),
        )
        await self.event_hub.publish(event)


def _coerce_error(exc: Exception) -> AgentError:
    if isinstance(exc, AgentError):
        return exc
    if isinstance(exc, TimeoutError):
        return AgentError("JOB_TIMEOUT", "The queued job timed out.", status_code=504)
    return AgentError(
        "JOB_EXECUTION_FAILED",
        f"Job execution failed with {type(exc).__name__}.",
        status_code=500,
        details={"cause": type(exc).__name__},
    )


def _is_retryable_error(error: AgentError) -> bool:
    if error.status_code >= 500:
        return True
    return error.code in RETRYABLE_ERROR_CODES


def utc_delta(base_delay_seconds: int, attempt_number: int, max_delay_seconds: int):
    delay_seconds = min(max_delay_seconds, base_delay_seconds * (2 ** max(0, attempt_number - 1)))
    return job_time_after(delay_seconds)


def job_time_after(delay_seconds: int):
    from .models import utc_now

    return utc_now() + timedelta(seconds=delay_seconds)


def _serialize_execution_result(result: PrintJobResult) -> dict[str, object]:
    return {
        "printer": {
            "device_id": DeviceRecord.from_printer(result.printer).id,
            "printer_name": result.printer.name,
            "driver": result.printer.driver_key,
            "is_default": result.printer.is_default,
        },
        "transport": result.transport.value,
        "bytes_written": result.bytes_written,
        "device_job_id": result.job_id,
    }


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
    if job.last_error_code is not None:
        payload["error_code"] = job.last_error_code
    if job.last_error_detail is not None:
        payload["error_detail"] = job.last_error_detail
    if job.result_payload is not None:
        payload["result"] = dict(job.result_payload)
    return payload
