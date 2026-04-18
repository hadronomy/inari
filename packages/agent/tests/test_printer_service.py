from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping

import pytest

from inari.binary_payloads import BinaryPayload
from inari.config import AgentSettings
from inari.drivers import DeviceKind, DriverMetadata, DriverRegistry
from inari.drivers.printers.base import PrinterDriver
from inari.exceptions import PrinterServiceError
from inari.print_jobs import PrintJob, ReceiptImageContent
from inari.printer_service import PrinterService
from inari.printers import (
    PrintJobResult,
    PrinterCapabilities,
    PrinterDevice,
    PrinterTransport,
    RenderedDocument,
)


@dataclass(slots=True)
class FakePrinterDriver(PrinterDriver):
    devices: tuple[PrinterDevice, ...]
    default_name: str | None = None
    available: bool = True
    raw_jobs: list[tuple[str, bytes, str]] = field(default_factory=list)
    text_jobs: list[tuple[str, str, str]] = field(default_factory=list)
    document_jobs: list[tuple[str, RenderedDocument]] = field(default_factory=list)
    drawer_pulses: list[str] = field(default_factory=list)

    metadata: ClassVar[DriverMetadata] = DriverMetadata(
        key="tests.fake-printers",
        display_name="Fake Printer Driver",
        kind=DeviceKind.PRINTER,
        platform="test",
    )

    def is_available(self) -> bool:
        return self.available

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
        if requested is not PrinterTransport.AUTO:
            return requested
        return printer.preferred_transport

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
        self.document_jobs.append((printer.name, document))
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.DOCUMENT,
            bytes_written=len(document.content),
            job_id=3,
        )

    def open_cash_drawer(self, printer: PrinterDevice) -> PrintJobResult:
        self.drawer_pulses.append(printer.name)
        return PrintJobResult(
            printer=printer, transport=PrinterTransport.RAW, bytes_written=5, job_id=4
        )


@dataclass(slots=True)
class FakeStructuredReceiptRenderer:
    payload: bytes = b"structured-receipt"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def render(self, receipt: Mapping[str, Any]) -> bytes:
        self.calls.append(dict(receipt))
        return self.payload


@dataclass(slots=True)
class FakeReceiptImageRenderer:
    payload: bytes = b"image-receipt"
    calls: list[tuple[bytes, str | None]] = field(default_factory=list)

    def render(self, image_bytes: bytes, *, mime_type: str | None = None) -> bytes:
        self.calls.append((image_bytes, mime_type))
        return self.payload


def make_service(
    *drivers: FakePrinterDriver,
    default_printer_name: str | None = None,
    structured_receipt_renderer: FakeStructuredReceiptRenderer | None = None,
    image_receipt_renderer: FakeReceiptImageRenderer | None = None,
) -> PrinterService:
    registry = DriverRegistry(drivers=drivers)
    settings = AgentSettings(default_printer_name=default_printer_name)
    return PrinterService(
        settings=settings,
        driver_registry=registry,
        structured_receipt_renderer=structured_receipt_renderer,
        image_receipt_renderer=image_receipt_renderer,
    )


def test_print_receipt_data_routes_raw_jobs_through_selected_driver() -> None:
    printer = PrinterDevice(
        name="EPSON TM-T20III",
        driver_key=FakePrinterDriver.metadata.key,
        is_default=True,
        preferred_transport=PrinterTransport.RAW,
        capabilities=PrinterCapabilities(
            raw=True, text=True, documents=True, cash_drawer=True
        ),
    )
    renderer = FakeStructuredReceiptRenderer()
    driver = FakePrinterDriver(devices=(printer,), default_name=printer.name)
    service = make_service(driver, structured_receipt_renderer=renderer)

    result = service.print_receipt_data(
        {
            "name": "POS/001",
            "orderlines": [
                {"product_name": "Coffee", "qty": 1, "price_display": "2.50"}
            ],
            "amount_tax": 0,
            "amount_total": 2.50,
        }
    )

    assert result.printer_name == printer.name
    assert result.transport is PrinterTransport.RAW
    assert renderer.calls[0]["name"] == "POS/001"
    assert driver.raw_jobs[0][1] == b"structured-receipt"


def test_print_job_dispatches_receipt_image_through_image_renderer() -> None:
    printer = PrinterDevice(
        name="EPSON TM-T20III",
        driver_key=FakePrinterDriver.metadata.key,
        is_default=True,
        preferred_transport=PrinterTransport.RAW,
        capabilities=PrinterCapabilities(
            raw=True, text=True, documents=True, cash_drawer=True
        ),
    )
    image_renderer = FakeReceiptImageRenderer()
    driver = FakePrinterDriver(devices=(printer,), default_name=printer.name)
    service = make_service(driver, image_receipt_renderer=image_renderer)

    result = service.print_job(
        PrintJob(
            content=ReceiptImageContent(
                binary_payload=BinaryPayload(
                    content=b"image-bytes",
                    declared_mime_types=("image/png",),
                )
            ),
            printer_name=printer.name,
        )
    )

    assert result.printer_name == printer.name
    assert image_renderer.calls == [(b"image-bytes", "image/png")]
    assert driver.raw_jobs[0][1] == b"image-receipt"


def test_open_cash_drawer_checks_printer_capability_not_default_transport_policy() -> (
    None
):
    printer = PrinterDevice(
        name="EPSON TM-T20III",
        driver_key=FakePrinterDriver.metadata.key,
        is_default=True,
        preferred_transport=PrinterTransport.RAW,
        capabilities=PrinterCapabilities(
            raw=True, text=True, documents=True, cash_drawer=True
        ),
    )
    driver = FakePrinterDriver(devices=(printer,), default_name=printer.name)
    service = make_service(driver)

    result = service.open_cash_drawer()

    assert result.printer_name == printer.name
    assert driver.drawer_pulses == [printer.name]


def test_print_receipt_rejects_non_raw_printers() -> None:
    office_printer = PrinterDevice(
        name="HP LaserJet",
        driver_key=FakePrinterDriver.metadata.key,
        is_default=True,
        preferred_transport=PrinterTransport.DOCUMENT,
        capabilities=PrinterCapabilities(
            raw=False, text=True, documents=True, cash_drawer=False
        ),
    )
    renderer = FakeStructuredReceiptRenderer()
    service = make_service(
        FakePrinterDriver(devices=(office_printer,), default_name=office_printer.name),
        structured_receipt_renderer=renderer,
    )

    with pytest.raises(PrinterServiceError, match="RAW receipt printing"):
        service.print_receipt_data(
            {
                "orderlines": [
                    {"product_name": "Coffee", "qty": 1, "price_display": "2.50"}
                ],
                "amount_tax": 0,
                "amount_total": 2.50,
            }
        )


def test_print_rendered_document_uses_default_printer_name_when_configured() -> None:
    primary = PrinterDevice(
        name="Back Office",
        driver_key=FakePrinterDriver.metadata.key,
        preferred_transport=PrinterTransport.DOCUMENT,
        capabilities=PrinterCapabilities(
            raw=False, text=True, documents=True, cash_drawer=False
        ),
    )
    kitchen = PrinterDevice(
        name="Kitchen Receipt Printer",
        driver_key=FakePrinterDriver.metadata.key,
        preferred_transport=PrinterTransport.RAW,
        capabilities=PrinterCapabilities(
            raw=True, text=True, documents=True, cash_drawer=True
        ),
    )
    driver = FakePrinterDriver(devices=(primary, kitchen), default_name=primary.name)
    service = make_service(driver, default_printer_name=kitchen.name)

    result = service.print_rendered_document(
        RenderedDocument(content=b"document", data_type="RAW")
    )

    assert result.printer_name == kitchen.name
    assert driver.document_jobs[0][0] == kitchen.name
