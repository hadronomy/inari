from __future__ import annotations

from dishka import Provider, Scope, provide

from ..config import AgentSettings
from ..gateway.repositories import GatewayRepository
from ..runtime.discovery import DiscoveryCoordinator
from ..runtime.events import EventHub
from ..runtime.execution import (
    DeviceWorkerPool,
    JobScheduler,
    LeaseRecoveryCoordinator,
    PrinterOperationExecutor,
    RuntimeJobExecutor,
)
from ..runtime.repositories import DeviceRepository, JobRepository
from ..runtime.services import DeviceCatalog, JobService
from ..runtime.store import RuntimeStore
from ..runtime.supervisor import RuntimeSupervisor


class RuntimeProvider(Provider):
    scope = Scope.APP

    @provide
    def store(self, settings: AgentSettings) -> RuntimeStore:
        return RuntimeStore(settings.runtime_database_path)

    @provide
    def event_hub(self) -> EventHub:
        return EventHub()

    device_repository = provide(DeviceRepository)
    job_repository = provide(JobRepository)
    gateway_repository = provide(GatewayRepository)
    discovery_coordinator = provide(DiscoveryCoordinator)
    device_catalog = provide(DeviceCatalog)
    job_service = provide(JobService)
    printer_operation_executor = provide(PrinterOperationExecutor)
    runtime_job_executor = provide(RuntimeJobExecutor)
    device_worker_pool = provide(DeviceWorkerPool)
    job_scheduler = provide(JobScheduler)
    lease_recovery_coordinator = provide(LeaseRecoveryCoordinator)
    runtime_supervisor = provide(RuntimeSupervisor)
