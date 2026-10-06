from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import pytest

from inari.config import AgentSettings
from inari.core.exceptions import PrinterServiceError
from inari.drivers import (
    DeviceIdentity,
    DeviceKind,
    DeviceTransport,
    DriverMetadata,
    DriverRegistry,
)
from inari.printing.drivers.base import PrinterDriver
from inari.printing.protocols import (
    CutMode,
    EscPosCommands,
    PrintJobResult,
    PrinterCapabilities,
    PrinterDevice,
    PrinterTransport,
)
from inari.printing.service import PrinterService


def _test_identity(instance_id: str) -> DeviceIdentity:
    return DeviceIdentity(
        transport=DeviceTransport.SPOOLER,
        os_instance_id=f"test-queue:{instance_id}",
    )


@dataclass(slots=True)
class FakePrinterDriver(PrinterDriver):
    devices: tuple[PrinterDevice, ...]
    raw_jobs: list[tuple[str, bytes, str]] = field(default_factory=list)
    drawer_pulses: list[str] = field(default_factory=list)

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
        return next(device for device in self.devices if device.name == printer_name)

    def get_default_device_name(self) -> str | None:
        return None

    def submit_raw_job(
        self,
        printer: PrinterDevice,
        payload: bytes,
        *,
        document_name: str,
    ) -> PrintJobResult:
        self.raw_jobs.append((printer.name, payload, document_name))
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.RAW,
            bytes_written=len(payload),
            job_id=1,
        )

    def open_cash_drawer(self, printer: PrinterDevice) -> PrintJobResult:
        self.drawer_pulses.append(printer.name)
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.RAW,
            bytes_written=5,
            job_id=2,
        )

    def submit_document_job(
        self,
        printer: PrinterDevice,
        payload: bytes,
        *,
        media_type: str,
        document_name: str,
        dpi: int,
    ) -> PrintJobResult:
        del media_type
        return self.submit_raw_job(printer, payload, document_name=document_name)


def printer(*, raw: bool = True) -> PrinterDevice:
    return PrinterDevice(
        name="EPSON TM-T20III",
        driver_key=FakePrinterDriver.metadata.key,
        identity=_test_identity("epson-receipt"),
        capabilities=PrinterCapabilities(
            raw=raw,
            text=False,
            documents=False,
            cash_drawer=raw,
        ),
    )


def test_feed_command_uses_the_selected_raw_device() -> None:
    device = printer()
    driver = FakePrinterDriver(devices=(device,))
    service = PrinterService(
        settings=AgentSettings(),
        driver_registry=DriverRegistry(drivers=(driver,)),
    )

    result = service.feed_lines(2, printer_name=device.name)

    assert result.printer_name == device.name
    assert driver.raw_jobs == [(device.name, b"\x1bd\x02", "Feed Lines")]
    assert driver.drawer_pulses == []


def test_command_rejects_a_device_without_raw_capability() -> None:
    device = printer(raw=False)
    service = PrinterService(
        settings=AgentSettings(),
        driver_registry=DriverRegistry(drivers=(FakePrinterDriver(devices=(device,)),)),
    )

    with pytest.raises(PrinterServiceError, match="RAW receipt printing"):
        service.feed_lines(1, printer_name=device.name)


def test_diagnostic_sends_the_full_receipt_pattern_to_the_selected_device() -> None:
    device = printer()
    driver = FakePrinterDriver(devices=(device,))
    service = PrinterService(
        settings=AgentSettings(),
        driver_registry=DriverRegistry(drivers=(driver,)),
    )

    result = service.print_test_ticket(printer_name=device.name)

    assert len(driver.raw_jobs) == 1
    name, payload, document_name = driver.raw_jobs[0]
    assert name == device.name
    assert document_name == "Receipt Test"
    assert payload.startswith(b"\x1b@\x1dv0\x00\x48\x00\x20\x04")
    assert len(payload) == 10 + 72 * 1056 + 3 + 3
    assert payload.endswith(
        EscPosCommands.feed_lines(3) + EscPosCommands.cut(CutMode.PARTIAL)
    )
    assert result.bytes_written == len(payload)
    assert driver.drawer_pulses == []
