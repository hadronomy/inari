from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest

from inari.db.migrations import DatabaseMigrator
from inari.drivers import (
    DeviceIdentity,
    DeviceKind,
    DeviceTransport,
    DriverMetadata,
    DriverRegistry,
)
from inari.printing.drivers.base import PrinterDriver
from inari.printing.protocols import (
    PrintJobResult,
    PrinterCapabilities,
    PrinterDevice,
    PrinterTransport,
)
from inari.runtime.devices.discovery import DiscoveryCoordinator
from inari.runtime.events import EventHub
from inari.runtime.models import DeviceRecord, build_device_id
from inari.runtime.repositories import DeviceRepository
from inari.runtime.store import RuntimeStore


@dataclass(slots=True)
class FakePrinterDriver(PrinterDriver):
    devices: tuple[PrinterDevice, ...]
    default_name: str | None = None
    raw_jobs: list[tuple[str, bytes, str]] = field(default_factory=list)

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

    def submit_raw_job(
        self, printer: PrinterDevice, payload: bytes, *, document_name: str
    ) -> PrintJobResult:
        self.raw_jobs.append((printer.name, payload, document_name))
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.RAW,
            bytes_written=len(payload),
            job_id=1,
        )

    def open_cash_drawer(self, printer: PrinterDevice) -> PrintJobResult:
        return PrintJobResult(
            printer=printer, transport=PrinterTransport.RAW, bytes_written=5, job_id=2
        )


def test_device_id_is_independent_from_the_display_name() -> None:
    identity = DeviceIdentity(
        transport=DeviceTransport.USB,
        vendor_id=0x04B8,
        product_id=0x0E28,
        serial_number="printer-serial-01",
    )
    before_rename = PrinterDevice(
        name="Kitchen Printer",
        driver_key=FakePrinterDriver.metadata.key,
        identity=identity,
    )
    after_rename = PrinterDevice(
        name="Front Counter Printer",
        driver_key=FakePrinterDriver.metadata.key,
        identity=identity,
    )

    assert (
        DeviceRecord.from_printer(before_rename).id
        == DeviceRecord.from_printer(after_rename).id
    )


@pytest.mark.anyio
async def test_discovery_ignores_timestamp_only_device_changes(
    tmp_path: Path,
) -> None:
    printer = PrinterDevice(
        name="OneNote (Desktop)",
        driver_key=FakePrinterDriver.metadata.key,
        identity=DeviceIdentity(
            transport=DeviceTransport.SPOOLER,
            os_instance_id="test-queue:onenote",
        ),
        is_default=False,
        preferred_transport=PrinterTransport.RAW,
        capabilities=PrinterCapabilities(
            raw=True, text=False, documents=False, cash_drawer=False
        ),
        metadata={"source": "windows_spooler", "queue_name": "OneNote (Desktop)"},
    )
    driver = FakePrinterDriver(devices=(printer,))
    registry = DriverRegistry(drivers=(driver,))
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    DatabaseMigrator(store.database_path).ensure_current()
    repository = DeviceRepository(store)
    discovery = DiscoveryCoordinator(
        driver_registry=registry,
        device_repository=repository,
        event_hub=EventHub(),
    )
    device_id = build_device_id(
        kind=DeviceKind.PRINTER,
        identity=printer.identity,
    )

    await discovery.sync_once()
    await discovery.sync_once()

    events = repository.list_events(device_id, limit=10)
    assert [event.event_type for event in events] == ["device.connected"]
