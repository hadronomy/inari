from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta

from ..config import AgentSettings
from ..device_commands import CutPaper, FeedDots, FeedLines, OpenCashDrawer, PrintTestPage
from ..exceptions import AgentError
from ..printer_service import PrinterService
from ..printers import PrintJobResult
from .models import DeviceRecord, JobKind, JobRecord, JobState
from .operations import (
    QueuedDeviceCommandOperation,
    QueuedPrintOperation,
    deserialize_device_command_operation,
    deserialize_print_operation,
)
from .repositories import JobRepository
from .services import JobService

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


class PrinterOperationExecutor:
    def __init__(self, printer_service: PrinterService) -> None:
        self.printer_service = printer_service

    def execute_print(self, operation: QueuedPrintOperation) -> PrintJobResult:
        return self.printer_service.print_job(operation.job)

    def execute_command(self, operation: QueuedDeviceCommandOperation) -> PrintJobResult:
        printer_name = operation.target.printer_name
        command = operation.command
        match command:
            case OpenCashDrawer():
                return self.printer_service.open_cash_drawer(printer_name=printer_name)
            case PrintTestPage(transport=transport):
                return self.printer_service.print_test_ticket(
                    printer_name=printer_name,
                    transport=transport,
                )
            case FeedLines(count=count):
                return self.printer_service.feed_lines(count, printer_name=printer_name)
            case FeedDots(count=count):
                return self.printer_service.feed_dots(count, printer_name=printer_name)
            case CutPaper(mode=mode):
                return self.printer_service.cut_paper(printer_name=printer_name, mode=mode)
            case _:
                raise TypeError(f"Unsupported device command: {type(command)!r}")


class RuntimeJobExecutor:
    def __init__(self, printer_executor: PrinterOperationExecutor) -> None:
        self.printer_executor = printer_executor

    def execute(self, job: JobRecord) -> PrintJobResult:
        if job.kind is JobKind.PRINT:
            operation = deserialize_print_operation(job.request_payload)
            return self.printer_executor.execute_print(operation)
        operation = deserialize_device_command_operation(job.request_payload)
        return self.printer_executor.execute_command(operation)


class DeviceWorkerPool:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        job_repository: JobRepository,
        job_service: JobService,
        executor: RuntimeJobExecutor,
    ) -> None:
        self.settings = settings
        self.job_repository = job_repository
        self.job_service = job_service
        self.executor = executor
        self._device_queues: dict[str, asyncio.Queue[str | None]] = {}
        self._device_workers: dict[str, asyncio.Task[None]] = {}

    async def enqueue(self, job: JobRecord) -> None:
        queue = self._device_queues.get(job.device_id)
        if queue is None:
            queue = asyncio.Queue()
            self._device_queues[job.device_id] = queue
            self._device_workers[job.device_id] = asyncio.create_task(
                self._device_worker(job.device_id, queue),
                name=f"iot-agent-worker-{job.device_id}",
            )
        await queue.put(job.id)

    async def stop(self) -> None:
        for queue in self._device_queues.values():
            await queue.put(None)
        for task in self._device_workers.values():
            task.cancel()
        for task in self._device_workers.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._device_queues.clear()
        self._device_workers.clear()

    async def _device_worker(self, device_id: str, queue: asyncio.Queue[str | None]) -> None:
        while True:
            job_id = await queue.get()
            if job_id is None:
                return
            try:
                await self._process(job_id)
            except Exception:
                logger.exception("Device worker failed for %s", device_id)

    async def _process(self, job_id: str) -> None:
        job = self.job_repository.get(job_id)
        if job is None or job.state is JobState.CANCELLED:
            return

        job, attempt = self.job_repository.start_attempt(
            job.id,
            lease_seconds=self.settings.job_execution_lease_seconds,
        )
        await self.job_service.publish_event("job.running", job)

        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self.executor.execute, job),
                timeout=self.settings.job_execution_timeout_seconds,
            )
        except asyncio.TimeoutError:
            event_type, updated = self._handle_failure(
                job=job,
                attempt_number=attempt.attempt_number,
                exc=AgentError(
                    "JOB_TIMEOUT",
                    f"Job {job.id!r} exceeded the execution timeout of {self.settings.job_execution_timeout_seconds} seconds.",
                    status_code=504,
                ),
            )
            await self.job_service.publish_event(event_type, updated)
            return
        except Exception as exc:
            event_type, updated = self._handle_failure(
                job=job,
                attempt_number=attempt.attempt_number,
                exc=exc,
            )
            await self.job_service.publish_event(event_type, updated)
            return
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        completed = self.job_repository.mark_succeeded(
            job.id,
            attempt_number=attempt.attempt_number,
            result_payload=_serialize_execution_result(result),
        )
        await self.job_service.publish_event("job.succeeded", completed)

    def _handle_failure(
        self,
        *,
        job: JobRecord,
        attempt_number: int,
        exc: Exception,
    ) -> tuple[str, JobRecord]:
        error = _coerce_error(exc)
        if _is_retryable(error) and attempt_number < job.max_attempts:
            retry_at = _retry_at(
                base_delay_seconds=self.settings.job_retry_base_delay_seconds,
                attempt_number=attempt_number,
                max_delay_seconds=self.settings.job_retry_max_delay_seconds,
            )
            updated = self.job_repository.mark_retry(
                job.id,
                attempt_number=attempt_number,
                next_run_at=retry_at,
                error_code=error.code,
                error_detail=error.message,
            )
            return "job.retry_scheduled", updated
        updated = self.job_repository.mark_failed(
            job.id,
            attempt_number=attempt_number,
            error_code=error.code,
            error_detail=error.message,
        )
        return "job.failed", updated

    async def _heartbeat(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(self.settings.job_heartbeat_interval_seconds)
            self.job_repository.renew_lease(
                job_id,
                lease_seconds=self.settings.job_execution_lease_seconds,
            )


class JobScheduler:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        job_repository: JobRepository,
        job_service: JobService,
        worker_pool: DeviceWorkerPool,
    ) -> None:
        self.settings = settings
        self.job_repository = job_repository
        self.job_service = job_service
        self.worker_pool = worker_pool

    async def run_forever(self) -> None:
        while True:
            try:
                claimed = self.job_repository.claim_runnable(
                    limit=self.settings.scheduler_batch_size,
                    lease_seconds=self.settings.job_dispatch_lease_seconds,
                )
                for job in claimed:
                    await self.job_service.publish_event("job.dispatched", job)
                    await self.worker_pool.enqueue(job)
            except Exception:
                logger.exception("Job scheduler loop failed")
            await asyncio.sleep(self.settings.scheduler_poll_interval_seconds)


class LeaseRecoveryCoordinator:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        job_repository: JobRepository,
        job_service: JobService,
    ) -> None:
        self.settings = settings
        self.job_repository = job_repository
        self.job_service = job_service

    async def run_forever(self) -> None:
        while True:
            try:
                for job in self.job_repository.recover_expired():
                    event_type = "job.retry_scheduled" if job.state is JobState.RETRY_SCHEDULED else "job.failed"
                    await self.job_service.publish_event(event_type, job)
            except Exception:
                logger.exception("Lease recovery loop failed")
            await asyncio.sleep(self.settings.job_lease_recovery_interval_seconds)


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


def _is_retryable(error: AgentError) -> bool:
    if error.status_code >= 500:
        return True
    return error.code in RETRYABLE_ERROR_CODES


def _retry_at(*, base_delay_seconds: int, attempt_number: int, max_delay_seconds: int):
    from .models import utc_now

    delay_seconds = min(max_delay_seconds, base_delay_seconds * (2 ** max(0, attempt_number - 1)))
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
