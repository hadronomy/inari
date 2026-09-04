from __future__ import annotations

from inari.config import AgentSettings, NetworkPrinterConfig
from inari.di.drivers import build_printer_drivers
from inari.printing.drivers import (
    CupsPrinterDriver,
    RawSocketPrinterDriver,
    WindowsPrinterDriver,
)
from inari.printing.protocols import PrinterTransport


def test_list_devices_and_send_raw_payload() -> None:
    sent_payloads: list[bytes] = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def sendall(self, payload: bytes) -> None:
            sent_payloads.append(payload)

    driver = RawSocketPrinterDriver(
        configured_printers=(
            NetworkPrinterConfig(
                name="Kitchen Receipt",
                host="192.168.1.50",
                port=9100,
                is_default=True,
                cash_drawer=True,
            ),
        ),
        socket_factory=lambda address, timeout=10.0: FakeConnection(),
    )

    devices = driver.list_devices()
    result = driver.submit_raw_job(devices[0], b"hello", document_name="Receipt")

    assert len(devices) == 1
    assert devices[0].preferred_transport is PrinterTransport.RAW
    assert driver.get_default_device_name() == "Kitchen Receipt"
    assert result.bytes_written == 5
    assert sent_payloads == [b"hello"]


def test_list_devices_from_cups_api_and_stream_raw_job() -> None:
    submissions: list[tuple[str, object]] = []

    class FakeConnection:
        def getPrinters(self):
            return {
                "Receipt Printer": {"device-uri": "socket://192.168.1.99"},
                "Office Printer": {"device-uri": "ipp://printer.local"},
            }

        def getDefault(self):
            return "Office Printer"

        def getPrinterAttributes(self, printer_name):
            return {"document-format-supported": ["application/pdf"]}

        def createJob(self, printer_name, title, options):
            submissions.append(("create", (printer_name, title, options)))
            return 42

        def startDocument(
            self, printer_name, job_id, document_name, document_format, last_document
        ):
            submissions.append(
                (
                    "start",
                    (
                        printer_name,
                        job_id,
                        document_name,
                        document_format,
                        last_document,
                    ),
                )
            )
            return 100

        def writeRequestData(self, payload, length):
            submissions.append(("write", (payload, length)))
            return 100

        def finishDocument(self, printer_name):
            submissions.append(("finish", printer_name))
            return 0

    class FakeCups:
        def Connection(self):
            return FakeConnection()

    driver = CupsPrinterDriver(cups_api=FakeCups())

    devices = driver.list_devices()

    receipt_printer = next(
        device for device in devices if device.name == "Receipt Printer"
    )

    result = driver.submit_raw_job(receipt_printer, b"receipt", document_name="Receipt")

    assert [device.name for device in devices] == ["Office Printer", "Receipt Printer"]
    assert devices[0].name == "Office Printer"
    assert devices[1].preferred_transport is PrinterTransport.RAW
    assert result.job_id == 42
    assert submissions == [
        ("create", ("Receipt Printer", "Receipt", {})),
        (
            "start",
            (
                "Receipt Printer",
                42,
                "Receipt",
                "application/vnd.cups-raw",
                1,
            ),
        ),
        ("write", (b"receipt", 7)),
        ("finish", "Receipt Printer"),
    ]


def test_cups_streams_pdf_with_an_explicit_document_format() -> None:
    calls: list[tuple[str, object]] = []

    class FakeConnection:
        def getPrinters(self):
            return {"Office Printer": {"device-uri": "ipp://printer.local"}}

        def getDefault(self):
            return "Office Printer"

        def getPrinterAttributes(self, printer_name):
            calls.append(("attributes", printer_name))
            return {"document-format-supported": ["application/pdf"]}

        def createJob(self, printer_name, title, options):
            calls.append(("create", (printer_name, title, options)))
            return 17

        def startDocument(self, *arguments):
            calls.append(("start", arguments))
            return 100

        def writeRequestData(self, payload, length):
            calls.append(("write", (payload, length)))
            return 100

        def finishDocument(self, printer_name):
            calls.append(("finish", printer_name))
            return 0

    class FakeCups:
        def Connection(self):
            return FakeConnection()

    driver = CupsPrinterDriver(cups_api=FakeCups())
    printer = driver.get_device("Office Printer")

    result = driver.submit_document_job(
        printer,
        b"%PDF-1.7\n%%EOF",
        media_type="application/pdf",
        document_name="Inari Report",
    )

    assert result.transport is PrinterTransport.DOCUMENT
    assert result.job_id == 17
    assert ("write", (b"%PDF-1.7\n%%EOF", 14)) in calls
    assert (
        "start",
        ("Office Printer", 17, "Inari Report", "application/pdf", 1),
    ) in calls


def test_build_printer_drivers_uses_windows_driver_on_windows() -> None:
    drivers = build_printer_drivers(AgentSettings(), platform_system="Windows")

    assert any(isinstance(driver, WindowsPrinterDriver) for driver in drivers)
    assert not any(isinstance(driver, CupsPrinterDriver) for driver in drivers)


def test_build_printer_drivers_uses_cups_driver_on_linux() -> None:
    drivers = build_printer_drivers(AgentSettings(), platform_system="Linux")

    assert any(isinstance(driver, CupsPrinterDriver) for driver in drivers)
    assert not any(isinstance(driver, WindowsPrinterDriver) for driver in drivers)


def test_build_printer_drivers_uses_cups_driver_on_macos() -> None:
    drivers = build_printer_drivers(AgentSettings(), platform_system="Darwin")

    assert any(isinstance(driver, CupsPrinterDriver) for driver in drivers)
    assert not any(isinstance(driver, WindowsPrinterDriver) for driver in drivers)


def test_build_printer_drivers_includes_raw_socket_driver_when_configured() -> None:
    settings = AgentSettings(
        network_printers=[
            NetworkPrinterConfig(name="Kitchen Receipt", host="192.168.1.50"),
        ]
    )

    drivers = build_printer_drivers(settings, platform_system="Linux")

    assert any(isinstance(driver, RawSocketPrinterDriver) for driver in drivers)
