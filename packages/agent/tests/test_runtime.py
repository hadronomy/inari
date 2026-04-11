from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from iot_agent.config import AgentSettings
from iot_agent.drivers import DeviceKind, DriverMetadata, DriverRegistry
from iot_agent.drivers.printers.base import PrinterDriver
from iot_agent.models import PrintJobRequest
from iot_agent.printer_service import PrinterService
from iot_agent.printers import PrintJobResult, PrinterCapabilities, PrinterDevice, PrinterTransport, RenderedDocument
from iot_agent.runtime.discovery import DiscoveryCoordinator
from iot_agent.runtime.events import EventHub
from iot_agent.runtime.execution import DeviceWorkerPool, JobScheduler, LeaseRecoveryCoordinator, PrinterOperationExecutor, RuntimeJobExecutor
from iot_agent.runtime.models import JobRecord, JobState
from iot_agent.runtime.repositories import DeviceRepository, JobRepository
from iot_agent.runtime.services import DeviceCatalog, JobService
from iot_agent.runtime.store import RuntimeStore
from iot_agent.runtime.supervisor import RuntimeSupervisor


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

    def resolve_transport(self, printer: PrinterDevice, requested: PrinterTransport) -> PrinterTransport:
        return requested if requested is not PrinterTransport.AUTO else printer.preferred_transport

    def submit_raw_job(self, printer: PrinterDevice, payload: bytes, *, document_name: str) -> PrintJobResult:
        return PrintJobResult(printer=printer, transport=PrinterTransport.RAW, bytes_written=len(payload), job_id=1)

    def submit_text_job(self, printer: PrinterDevice, text: str, *, document_name: str) -> PrintJobResult:
        self.text_jobs.append((printer.name, text, document_name))
        return PrintJobResult(printer=printer, transport=PrinterTransport.TEXT, bytes_written=len(text), job_id=2)

    def submit_document_job(self, printer: PrinterDevice, document: RenderedDocument) -> PrintJobResult:
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.DOCUMENT,
            bytes_written=len(document.content),
            job_id=3,
        )

    def open_cash_drawer(self, printer: PrinterDevice) -> PrintJobResult:
        return PrintJobResult(printer=printer, transport=PrinterTransport.RAW, bytes_written=5, job_id=4)


class RuntimeArchitectureTests(unittest.TestCase):
    def test_supervisor_executes_queued_print_jobs_asynchronously(self) -> None:
        async def scenario() -> None:
            printer = PrinterDevice(
                name="Kitchen Printer",
                driver_key=FakePrinterDriver.metadata.key,
                is_default=True,
                preferred_transport=PrinterTransport.TEXT,
                capabilities=PrinterCapabilities(raw=False, text=True, documents=True, cash_drawer=False),
            )
            driver = FakePrinterDriver(devices=(printer,), default_name=printer.name)
            registry = DriverRegistry(drivers=(driver,))

            with tempfile.TemporaryDirectory() as temp_dir:
                settings = AgentSettings(
                    runtime_database_path=Path(temp_dir) / "runtime.sqlite3",
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

                self.assertEqual(job.state, JobState.QUEUED)
                self.assertEqual(completed.state, JobState.SUCCEEDED)
                self.assertEqual(driver.text_jobs, [(printer.name, "Hello queue", "Text Document")])

        asyncio.run(scenario())


async def wait_for_job(job_service: JobService, job_id: str) -> JobRecord:
    deadline = asyncio.get_running_loop().time() + 2.0
    while True:
        job = job_service.get_job(job_id)
        if job is not None and job.state is JobState.SUCCEEDED:
            return job
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"Job {job_id!r} did not complete in time.")
        await asyncio.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
