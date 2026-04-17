from __future__ import annotations

import asyncio
import contextlib
import logging

from ..config import AgentSettings
from .execution import DeviceWorkerPool, JobScheduler, LeaseRecoveryCoordinator
from .services import DeviceCatalog, JobService
from .store import RuntimeStore

logger = logging.getLogger(__name__)


class RuntimeSupervisor:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        store: RuntimeStore,
        device_catalog: DeviceCatalog,
        job_service: JobService,
        job_scheduler: JobScheduler,
        lease_recovery: LeaseRecoveryCoordinator,
        worker_pool: DeviceWorkerPool,
    ) -> None:
        self.settings = settings
        self.store = store
        self.device_catalog = device_catalog
        self.job_service = job_service
        self.job_scheduler = job_scheduler
        self.lease_recovery = lease_recovery
        self.worker_pool = worker_pool
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False
        self._stopping = False

    async def start(self) -> None:
        if self._started:
            return
        self.store.initialize()
        await self.device_catalog.refresh()
        for job in self.job_scheduler.job_repository.recover_expired():
            await self.job_service.publish_event("job.recovered", job)
        self._tasks = [
            asyncio.create_task(self._discovery_loop(), name="iot-agent-discovery"),
            asyncio.create_task(
                self.job_scheduler.run_forever(), name="iot-agent-scheduler"
            ),
            asyncio.create_task(
                self.lease_recovery.run_forever(), name="iot-agent-lease-recovery"
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
