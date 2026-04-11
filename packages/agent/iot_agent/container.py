from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .config import AgentSettings, get_settings
from .drivers import DriverRegistry
from .drivers.printers import WindowsPrinterDriver, WindowsSpooler
from .printers import PrinterTransport
from .printer_service import PrinterService
from .receipt_renderers import EscPosImageReceiptRenderer, EscPosRenderer
from .runtime.discovery import DiscoveryCoordinator
from .runtime.events import EventHub
from .runtime.execution import DeviceWorkerPool, JobScheduler, LeaseRecoveryCoordinator, PrinterOperationExecutor, RuntimeJobExecutor
from .runtime.repositories import DeviceRepository, JobRepository
from .runtime.services import DeviceCatalog, JobService
from .runtime.store import RuntimeStore
from .runtime.supervisor import RuntimeSupervisor


@dataclass(slots=True, frozen=True)
class AgentContainer:
    settings: AgentSettings
    driver_registry: DriverRegistry
    printer_service: PrinterService
    event_hub: EventHub
    device_catalog: DeviceCatalog
    job_service: JobService
    runtime_supervisor: RuntimeSupervisor


def build_container(settings: AgentSettings) -> AgentContainer:
    driver_registry = DriverRegistry(
        drivers=(
            WindowsPrinterDriver(
                spooler=WindowsSpooler(),
                default_transport=PrinterTransport(settings.default_printer_mode),
            ),
        )
    )
    printer_service = PrinterService(
        settings=settings,
        driver_registry=driver_registry,
        structured_receipt_renderer=EscPosRenderer(),
        image_receipt_renderer=EscPosImageReceiptRenderer(),
    )
    store = RuntimeStore(settings.runtime_database_path)
    event_hub = EventHub()
    device_repository = DeviceRepository(store)
    job_repository = JobRepository(store)
    discovery = DiscoveryCoordinator(
        driver_registry=driver_registry,
        device_repository=device_repository,
        event_hub=event_hub,
    )
    device_catalog = DeviceCatalog(
        device_repository=device_repository,
        discovery=discovery,
        printer_service=printer_service,
    )
    job_service = JobService(
        settings=settings,
        job_repository=job_repository,
        device_catalog=device_catalog,
        event_hub=event_hub,
    )
    executor = RuntimeJobExecutor(PrinterOperationExecutor(printer_service))
    worker_pool = DeviceWorkerPool(
        settings=settings,
        job_repository=job_repository,
        job_service=job_service,
        executor=executor,
    )
    runtime_supervisor = RuntimeSupervisor(
        settings=settings,
        store=store,
        device_catalog=device_catalog,
        job_service=job_service,
        job_scheduler=JobScheduler(
            settings=settings,
            job_repository=job_repository,
            job_service=job_service,
            worker_pool=worker_pool,
        ),
        lease_recovery=LeaseRecoveryCoordinator(
            settings=settings,
            job_repository=job_repository,
            job_service=job_service,
        ),
        worker_pool=worker_pool,
    )
    return AgentContainer(
        settings=settings,
        driver_registry=driver_registry,
        printer_service=printer_service,
        event_hub=event_hub,
        device_catalog=device_catalog,
        job_service=job_service,
        runtime_supervisor=runtime_supervisor,
    )


@lru_cache(maxsize=1)
def get_default_container() -> AgentContainer:
    return build_container(get_settings())
