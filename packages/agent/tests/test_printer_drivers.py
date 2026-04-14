from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from iot_agent.config import AgentSettings, NetworkPrinterConfig
from iot_agent.container import _build_printer_drivers
from iot_agent.drivers.printers import CupsPrinterDriver, RawSocketPrinterDriver, WindowsPrinterDriver
from iot_agent.printers import PrinterTransport


class RawSocketPrinterDriverTests(unittest.TestCase):
    def test_list_devices_and_send_raw_payload(self) -> None:
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

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].preferred_transport, PrinterTransport.RAW)
        self.assertEqual(driver.get_default_device_name(), "Kitchen Receipt")
        self.assertEqual(result.bytes_written, 5)
        self.assertEqual(sent_payloads, [b"hello"])


class CupsPrinterDriverTests(unittest.TestCase):
    def test_list_devices_from_cups_api_and_submit_raw_job_with_lp(self) -> None:
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

        receipt_printer = next(device for device in devices if device.name == "Receipt Printer")

        with mock.patch("iot_agent.drivers.printers.cups.subprocess.run", side_effect=fake_run):
            result = driver.submit_raw_job(receipt_printer, b"receipt", document_name="Receipt")

        self.assertEqual([device.name for device in devices], ["Office Printer", "Receipt Printer"])
        self.assertEqual(devices[0].name, "Office Printer")
        self.assertEqual(devices[1].preferred_transport, PrinterTransport.RAW)
        self.assertEqual(result.job_id, 42)
        self.assertTrue(any("-o" in command and "raw" in command for command in commands))


class DriverRegistrationTests(unittest.TestCase):
    def test_build_printer_drivers_uses_windows_driver_on_windows(self) -> None:
        drivers = _build_printer_drivers(AgentSettings(), platform_system="Windows")

        self.assertTrue(any(isinstance(driver, WindowsPrinterDriver) for driver in drivers))
        self.assertFalse(any(isinstance(driver, CupsPrinterDriver) for driver in drivers))

    def test_build_printer_drivers_uses_cups_driver_on_linux(self) -> None:
        drivers = _build_printer_drivers(AgentSettings(), platform_system="Linux")

        self.assertTrue(any(isinstance(driver, CupsPrinterDriver) for driver in drivers))
        self.assertFalse(any(isinstance(driver, WindowsPrinterDriver) for driver in drivers))

    def test_build_printer_drivers_uses_cups_driver_on_macos(self) -> None:
        drivers = _build_printer_drivers(AgentSettings(), platform_system="Darwin")

        self.assertTrue(any(isinstance(driver, CupsPrinterDriver) for driver in drivers))
        self.assertFalse(any(isinstance(driver, WindowsPrinterDriver) for driver in drivers))

    def test_build_printer_drivers_includes_raw_socket_driver_when_configured(self) -> None:
        settings = AgentSettings(
            network_printers=[
                NetworkPrinterConfig(name="Kitchen Receipt", host="192.168.1.50"),
            ]
        )

        drivers = _build_printer_drivers(settings, platform_system="Linux")

        self.assertTrue(any(isinstance(driver, RawSocketPrinterDriver) for driver in drivers))


if __name__ == "__main__":
    unittest.main()
