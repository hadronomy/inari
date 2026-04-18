from __future__ import annotations

import subprocess

from inari.config import AgentSettings, NetworkPrinterConfig
from inari.di.drivers import build_printer_drivers
from inari.drivers.printers import (
    CupsPrinterDriver,
    RawSocketPrinterDriver,
    WindowsPrinterDriver,
)
from inari.printers import PrinterTransport


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


def test_list_devices_from_cups_api_and_submit_raw_job_with_lp(mocker) -> None:
    class FakeConnection:
        def getPrinters(self):
            return {
                "Receipt Printer": {"device-uri": "socket://192.168.1.99"},
                "Office Printer": {"device-uri": "ipp://printer.local"},
            }

        def getDefault(self):
            return "Office Printer"

    class FakeCups:
        def Connection(self):
            return FakeConnection()

    class DriverUnderTest(CupsPrinterDriver):
        @staticmethod
        def _lp_command() -> str | None:
            return "lp"

        @staticmethod
        def _lpstat_command() -> str | None:
            return "lpstat"

    commands: list[list[str]] = []

    def fake_run(command, capture_output, check, text):
        commands.append(list(command))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="request id is Receipt-42 (1 file(s))\n",
            stderr="",
        )

    driver = DriverUnderTest(cups_api=FakeCups())

    devices = driver.list_devices()

    receipt_printer = next(
        device for device in devices if device.name == "Receipt Printer"
    )

    mocker.patch("inari.drivers.printers.cups.subprocess.run", side_effect=fake_run)
    result = driver.submit_raw_job(receipt_printer, b"receipt", document_name="Receipt")

    assert [device.name for device in devices] == ["Office Printer", "Receipt Printer"]
    assert devices[0].name == "Office Printer"
    assert devices[1].preferred_transport is PrinterTransport.RAW
    assert result.job_id == 42
    assert any("-o" in command and "raw" in command for command in commands)


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
