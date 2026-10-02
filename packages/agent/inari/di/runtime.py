from __future__ import annotations

from importlib.metadata import version
import os
import time

from dishka import Provider, Scope, provide

from ..config import AgentSettings
from ..device_authority import (
    AdmissionAuthorizer,
    DeviceCapabilityAuthority,
    SqliteDeviceAuthorityReader,
)
from ..documents import DocumentAdmission, DocumentAdmissionService
from ..gateway.repositories import GatewayRepository
from ..local_api.device_work import DeviceWorkSubmission
from ..local_api.print_job_queries import PrintJobQueries
from ..physical_execution import (
    EncryptedExecutionSpool,
    ExecutionOwner,
    IsolatedPrinterWorker,
    PhysicalExecution,
    SqliteExecutionLedger,
)
from ..printing.renderers import EscPosImageReceiptRenderer
from ..print_jobs.sqlite import SqlitePrintJobReader
from ..runtime.devices.discovery import DiscoveryCoordinator
from ..runtime.events import EventHub
from ..runtime.jobs.execution import (
    DeviceWorkerPool,
    JobScheduler,
    LeaseRecoveryCoordinator,
    PrinterCommandExecutor,
    RuntimeJobExecutor,
)
from ..runtime.repositories import DeviceRepository, JobRepository
from ..runtime.devices.service import DeviceCatalog
from ..runtime.jobs.service import JobService
from ..runtime.store import RuntimeStore
from ..runtime.supervisor import RuntimeSupervisor
from ..security.secrets import ProtectedSecretStore
from ..spool import (
    ArtifactFileStore,
    DurableSpoolAdmissionStore,
    SqlActiveAuthorityGuard,
)
from ..spool.keys import SpoolRootKeyService
from ..spool.owner import SpoolOwner


class RuntimeProvider(Provider):
    scope = Scope.APP

    @provide
    def store(self, settings: AgentSettings) -> RuntimeStore:
        return RuntimeStore(settings.resolved_runtime_database_path)

    @provide
    def event_hub(self) -> EventHub:
        return EventHub()

    @provide
    def authority_reader(self, store: RuntimeStore) -> SqliteDeviceAuthorityReader:
        return SqliteDeviceAuthorityReader(store)

    @provide
    def admission_authorizer(
        self, reader: SqliteDeviceAuthorityReader
    ) -> AdmissionAuthorizer:
        return DeviceCapabilityAuthority(
            projections=reader,
            observations=reader,
            current_agent_version=version("inari"),
        )

    @provide
    def spool_files(self, settings: AgentSettings) -> ArtifactFileStore:
        data_dir = settings.resolved_data_dir
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            data_dir.chmod(0o700)
        return ArtifactFileStore(data_dir / "spool")

    @provide
    def spool_root_keys(
        self, secret_store: ProtectedSecretStore
    ) -> SpoolRootKeyService:
        return SpoolRootKeyService(secret_store)

    @provide
    def spool_owner(self) -> SpoolOwner:
        return SpoolOwner("agent-spool", time.time_ns())

    @provide
    def active_authority_guard(self) -> SqlActiveAuthorityGuard:
        return SqlActiveAuthorityGuard()

    @provide
    def durable_spool_admission(
        self,
        store: RuntimeStore,
        files: ArtifactFileStore,
        root_keys: SpoolRootKeyService,
        owner: SpoolOwner,
        authority_guard: SqlActiveAuthorityGuard,
    ) -> DurableSpoolAdmissionStore:
        return DurableSpoolAdmissionStore(
            store=store,
            files=files,
            root_keys=root_keys,
            owner=owner,
            authority_guard=authority_guard,
        )

    @provide
    def document_admission(
        self,
        store: DurableSpoolAdmissionStore,
        authority: AdmissionAuthorizer,
    ) -> DocumentAdmission:
        return DocumentAdmissionService(store=store, authority=authority)

    @provide
    def device_work_submission(
        self,
        admission: DocumentAdmission,
    ) -> DeviceWorkSubmission:
        return DeviceWorkSubmission(admission=admission)

    @provide
    def print_job_reader(self, store: RuntimeStore) -> SqlitePrintJobReader:
        return SqlitePrintJobReader(store)

    @provide
    def print_job_queries(self, reader: SqlitePrintJobReader) -> PrintJobQueries:
        return PrintJobQueries(reader=reader)

    @provide
    def execution_owner(self) -> ExecutionOwner:
        return ExecutionOwner("agent-execution", time.time_ns())

    @provide
    def execution_ledger(
        self,
        store: RuntimeStore,
        authority_guard: SqlActiveAuthorityGuard,
    ) -> SqliteExecutionLedger:
        return SqliteExecutionLedger(store=store, authority_guard=authority_guard)

    @provide
    def execution_spool(
        self,
        store: RuntimeStore,
        files: ArtifactFileStore,
        root_keys: SpoolRootKeyService,
    ) -> EncryptedExecutionSpool:
        return EncryptedExecutionSpool(
            store=store,
            files=files,
            root_keys=root_keys,
            renderer=EscPosImageReceiptRenderer(),
        )

    @provide
    def printer_worker(self, settings: AgentSettings) -> IsolatedPrinterWorker:
        return IsolatedPrinterWorker(settings)

    @provide
    def physical_execution(
        self,
        ledger: SqliteExecutionLedger,
        spool: EncryptedExecutionSpool,
        worker: IsolatedPrinterWorker,
    ) -> PhysicalExecution:
        return PhysicalExecution(ledger=ledger, spool=spool, worker=worker)

    device_repository = provide(DeviceRepository)
    job_repository = provide(JobRepository)
    gateway_repository = provide(GatewayRepository)
    discovery_coordinator = provide(DiscoveryCoordinator)
    device_catalog = provide(DeviceCatalog)
    job_service = provide(JobService)
    printer_command_executor = provide(PrinterCommandExecutor)
    runtime_job_executor = provide(RuntimeJobExecutor)
    device_worker_pool = provide(DeviceWorkerPool)
    job_scheduler = provide(JobScheduler)
    lease_recovery_coordinator = provide(LeaseRecoveryCoordinator)
    runtime_supervisor = provide(RuntimeSupervisor)
