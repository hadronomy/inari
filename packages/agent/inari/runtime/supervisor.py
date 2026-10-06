from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from ..config import AgentSettings
from ..physical_execution import ExecutionOwner, PhysicalExecution
from ..device_tests.sqlite import SqliteDeviceTestLedger
from ..spool import DurableSpoolAdmissionStore
from .devices.service import DeviceCatalog
from .jobs.execution import DeviceWorkerPool, JobScheduler, LeaseRecoveryCoordinator
from .jobs.service import JobService
from .models import RuntimeEventKind

logger = logging.getLogger(__name__)


class RuntimeSupervisor:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        device_catalog: DeviceCatalog,
        spool_admission: DurableSpoolAdmissionStore,
        physical_execution: PhysicalExecution,
        execution_owner: ExecutionOwner,
        job_service: JobService,
        job_scheduler: JobScheduler,
        lease_recovery: LeaseRecoveryCoordinator,
        worker_pool: DeviceWorkerPool,
        device_test_ledger: SqliteDeviceTestLedger,
    ) -> None:
        self.settings = settings
        self.device_catalog = device_catalog
        self.spool_admission = spool_admission
        self.physical_execution = physical_execution
        self.execution_owner = execution_owner
        self.job_service = job_service
        self.job_scheduler = job_scheduler
        self.lease_recovery = lease_recovery
        self.worker_pool = worker_pool
        self.device_test_ledger = device_test_ledger
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False
        self._stopping = False

    async def start(self) -> None:
        if self._started:
            return
        await self.device_catalog.refresh()
        await self.spool_admission.reconcile()
        await self.physical_execution.recover_after_restart(self.execution_owner)
        self.device_test_ledger.recover_after_restart(now=datetime.now(UTC))
        for job in self.job_scheduler.job_repository.recover_expired():
            await self.job_service.publish_event(RuntimeEventKind.JOB_RECOVERED, job)
        self._tasks = [
            asyncio.create_task(self._discovery_loop(), name="inari-discovery"),
            asyncio.create_task(
                self.job_scheduler.run_forever(), name="inari-scheduler"
            ),
            asyncio.create_task(
                self.lease_recovery.run_forever(), name="inari-lease-recovery"
            ),
            asyncio.create_task(
                self._physical_execution_loop(), name="inari-physical-execution"
            ),
        ]
        self._started = True

    async def stop(self) -> None:
        if not self._started or self._stopping:
            return
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.worker_pool.stop()
        self._tasks.clear()
        self._stopping = False
        self._started = False

    async def _discovery_loop(self) -> None:
        while True:
            try:
                await self.device_catalog.refresh()
            except Exception:
                logger.exception("Device discovery loop failed")
            await asyncio.sleep(self.settings.discovery_poll_interval_seconds)

    async def _physical_execution_loop(self) -> None:
        while True:
            results = await asyncio.gather(
                *(
                    self.physical_execution.run_one(self.execution_owner)
                    for _ in range(self.settings.scheduler_batch_size)
                ),
                return_exceptions=True,
            )
            completed = False
            for result in results:
                if isinstance(result, BaseException):
                    logger.error(
                        "Physical Device Work execution failed",
                        exc_info=(type(result), result, result.__traceback__),
                    )
                elif result is not None:
                    completed = True
            if not completed:
                await asyncio.sleep(self.settings.scheduler_poll_interval_seconds)
