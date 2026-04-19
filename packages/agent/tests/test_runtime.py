from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest

from inari.config import AgentSettings
from inari.drivers import DeviceKind, DriverMetadata, DriverRegistry
from inari.drivers.printers.base import PrinterDriver
from inari.models import PrintJobRequest
from inari.printer_service import PrinterService
from inari.printers import (
    PrintJobResult,
    PrinterCapabilities,
    PrinterDevice,
    PrinterTransport,
    RenderedDocument,
)
from inari.runtime.discovery import DiscoveryCoordinator
from inari.runtime.events import EventHub
from inari.runtime.execution import (
    DeviceWorkerPool,
    JobScheduler,
    LeaseRecoveryCoordinator,
    PrinterOperationExecutor,
    RuntimeJobExecutor,
)
from inari.runtime.models import JobRecord, JobState, build_device_id
from inari.runtime.repositories import DeviceRepository, JobRepository
from inari.runtime.services import DeviceCatalog, JobService
from inari.runtime.store import RuntimeStore
from inari.runtime.supervisor import RuntimeSupervisor


@dataclass(slots=True)
class FakePrinterDriver(PrinterDriver):
    devices: tuple[PrinterDevice, ...]
    default_name: str | None = None
    text_jobs: list[tuple[str, str, str]] = field(default_factory=list)

    metadata: ClassVar[DriverMetadata] = DriverMetadata(
        key="tests.fake-printers",
        display_name="Fake Printer Driver",
        kind=DeviceKind.PRINTER,
        platform="test",
    )

    def is_available(self) -> bool:
        return True

    def list_devices(self) -> tuple[PrinterDevice, ...]:
        return self.devices

    def get_device(self, printer_name: str) -> PrinterDevice:
        for device in self.devices:
            if device.name == printer_name:
                return device
        raise LookupError(printer_name)

    def get_default_device_name(self) -> str | None:
        return self.default_name

    def resolve_transport(
        self, printer: PrinterDevice, requested: PrinterTransport
    ) -> PrinterTransport:
        return (
            requested
            if requested is not PrinterTransport.AUTO
            else printer.preferred_transport
        )

    def submit_raw_job(
        self, printer: PrinterDevice, payload: bytes, *, document_name: str
    ) -> PrintJobResult:
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.RAW,
            bytes_written=len(payload),
            job_id=1,
        )

    def submit_text_job(
        self, printer: PrinterDevice, text: str, *, document_name: str
    ) -> PrintJobResult:
        self.text_jobs.append((printer.name, text, document_name))
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.TEXT,
            bytes_written=len(text),
            job_id=2,
        )

    def submit_document_job(
        self, printer: PrinterDevice, document: RenderedDocument
    ) -> PrintJobResult:
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.DOCUMENT,
            bytes_written=len(document.content),
            job_id=3,
        )

    def open_cash_drawer(self, printer: PrinterDevice) -> PrintJobResult:
        return PrintJobResult(
            printer=printer, transport=PrinterTransport.RAW, bytes_written=5, job_id=4
        )


@pytest.mark.anyio
async def test_discovery_ignores_timestamp_only_device_changes(
    tmp_path: Path,
) -> None:
    printer = PrinterDevice(
        name="OneNote (Desktop)",
        driver_key=FakePrinterDriver.metadata.key,
        is_default=False,
        preferred_transport=PrinterTransport.TEXT,
        capabilities=PrinterCapabilities(
            raw=False, text=True, documents=True, cash_drawer=False
        ),
        metadata={"source": "windows_spooler", "queue_name": "OneNote (Desktop)"},
    )
    driver = FakePrinterDriver(devices=(printer,))
    registry = DriverRegistry(drivers=(driver,))
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    store.initialize()
    repository = DeviceRepository(store)
    discovery = DiscoveryCoordinator(
        driver_registry=registry,
        device_repository=repository,
        event_hub=EventHub(),
    )
    device_id = build_device_id(
        kind=DeviceKind.PRINTER,
        driver_key=printer.driver_key,
        name=printer.name,
    )

    await discovery.sync_once()
    await discovery.sync_once()

    events = repository.list_events(device_id, limit=10)
    assert [event.event_type for event in events] == ["device.connected"]


@pytest.mark.anyio
@pytest.mark.timeout(10)
async def test_supervisor_executes_queued_print_jobs_asynchronously(
    tmp_path: Path,
) -> None:
    printer = PrinterDevice(
        name="Kitchen Printer",
        driver_key=FakePrinterDriver.metadata.key,
        is_default=True,
        preferred_transport=PrinterTransport.TEXT,
        capabilities=PrinterCapabilities(
            raw=False, text=True, documents=True, cash_drawer=False
        ),
    )
    driver = FakePrinterDriver(devices=(printer,), default_name=printer.name)
    registry = DriverRegistry(drivers=(driver,))

    settings = AgentSettings(
        runtime_database_path=tmp_path / "runtime.sqlite3",
        discovery_poll_interval_seconds=0.05,
        scheduler_poll_interval_seconds=0.05,
        scheduler_batch_size=8,
        job_heartbeat_interval_seconds=0.05,
        job_dispatch_lease_seconds=1,
        job_execution_lease_seconds=1,
        job_execution_timeout_seconds=5.0,
        job_lease_recovery_interval_seconds=0.05,
    )
    printer_service = PrinterService(settings=settings, driver_registry=registry)
    store = RuntimeStore(settings.runtime_database_path)
    event_hub = EventHub()
    device_repository = DeviceRepository(store)
    job_repository = JobRepository(store)
    discovery = DiscoveryCoordinator(
        driver_registry=registry,
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
    worker_pool = DeviceWorkerPool(
        settings=settings,
        job_repository=job_repository,
        job_service=job_service,
        executor=RuntimeJobExecutor(PrinterOperationExecutor(printer_service)),
    )
    supervisor = RuntimeSupervisor(
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

    await supervisor.start()
    try:
        job = await job_service.enqueue_print(
            PrintJobRequest.model_validate(
                {
                    "content": {"kind": "text", "text": "Hello queue"},
                    "target": {"printer_name": printer.name},
                    "options": {"transport": "text"},
                }
            ).to_operation()
        )
        completed = await wait_for_job(job_service, job.id)
    finally:
        await supervisor.stop()

    assert job.state is JobState.QUEUED
    assert completed.state is JobState.SUCCEEDED
    assert driver.text_jobs == [(printer.name, "Hello queue", "Text Document")]


async def wait_for_job(job_service: JobService, job_id: str) -> JobRecord:
    deadline = asyncio.get_running_loop().time() + 2.0
    while True:
        job = job_service.get_job(job_id)
        if job is not None and job.state is JobState.SUCCEEDED:
            return job
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"Job {job_id!r} did not complete in time.")
        await asyncio.sleep(0.05)
