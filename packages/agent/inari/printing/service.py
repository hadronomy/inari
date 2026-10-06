from __future__ import annotations

from dataclasses import dataclass

from ..config import AgentSettings
from ..core.exceptions import PrinterServiceError
from ..drivers import DriverRegistry
from .drivers.base import PrinterDriver
from .protocols import CutMode, EscPosCommands, PrintJobResult, PrinterDevice
from .receipt_pattern import prepared_receipt
from .renderers import EscPosImageReceiptRenderer


@dataclass(slots=True, frozen=True)
class SelectedPrinter:
    driver: PrinterDriver
    printer: PrinterDevice


class PrinterService:
    """Own printer discovery and explicit operator Device commands."""

    def __init__(
        self,
        settings: AgentSettings,
        *,
        driver_registry: DriverRegistry,
    ) -> None:
        self.settings = settings
        self.driver_registry = driver_registry

    def list_printers(self) -> tuple[PrinterDevice, ...]:
        printers: list[PrinterDevice] = []
        for driver in self._printer_drivers():
            printers.extend(driver.list_devices())
        return tuple(
            sorted(
                printers, key=lambda item: (not item.is_default, item.name.casefold())
            )
        )

    def get_default_printer_name(self, optional: bool = False) -> str | None:
        configured = self.settings.default_printer_name
        if configured is not None:
            return configured

        for driver in self._printer_drivers(optional=optional):
            if default_name := driver.get_default_device_name():
                return default_name

        if optional:
            return None
        raise PrinterServiceError("PRINTER_NOT_CONFIGURED", "No printer is configured.")

    def get_printer_info(self, printer_name: str) -> PrinterDevice:
        return self._select_printer(printer_name).printer

    def resolve_printer(self, printer_name: str | None = None) -> PrinterDevice:
        return self._select_printer(printer_name).printer

    def feed_lines(
        self, count: int, *, printer_name: str | None = None
    ) -> PrintJobResult:
        selection = self._select_raw_printer(printer_name)
        return selection.driver.submit_raw_job(
            selection.printer,
            EscPosCommands.feed_lines(count),
            document_name="Feed Lines",
        )

    def feed_dots(
        self, count: int, *, printer_name: str | None = None
    ) -> PrintJobResult:
        selection = self._select_raw_printer(printer_name)
        return selection.driver.submit_raw_job(
            selection.printer,
            EscPosCommands.feed_dots(count),
            document_name="Feed Dots",
        )

    def cut_paper(
        self,
        *,
        printer_name: str | None = None,
        mode: CutMode | str = CutMode.PARTIAL,
    ) -> PrintJobResult:
        selection = self._select_raw_printer(printer_name)
        return selection.driver.submit_raw_job(
            selection.printer,
            EscPosCommands.cut(CutMode(mode)),
            document_name="Cut Paper",
        )

    def open_cash_drawer(self, *, printer_name: str | None = None) -> PrintJobResult:
        selection = self._select_raw_printer(printer_name)
        return selection.driver.open_cash_drawer(selection.printer)

    def print_test_ticket(
        self,
        *,
        printer_name: str | None = None,
    ) -> PrintJobResult:
        selection = self._select_raw_printer(printer_name)
        payload = prepared_receipt(EscPosImageReceiptRenderer())
        return selection.driver.submit_raw_job(
            selection.printer,
            payload,
            document_name="Receipt Test",
        )

    def _select_printer(self, printer_name: str | None) -> SelectedPrinter:
        drivers = self._printer_drivers()
        requested_name = printer_name or self.settings.default_printer_name

        if requested_name:
            for driver in drivers:
                match = self._find_printer(driver, requested_name)
                if match is not None:
                    return SelectedPrinter(driver=driver, printer=match)
            raise PrinterServiceError(
                "PRINTER_NOT_FOUND",
                f"Printer {requested_name!r} was not found in the registered drivers.",
            )

        for driver in drivers:
            if default_name := driver.get_default_device_name():
                return SelectedPrinter(
                    driver=driver, printer=driver.get_device(default_name)
                )

        for driver in drivers:
            devices = tuple(driver.list_devices())
            if devices:
                return SelectedPrinter(driver=driver, printer=devices[0])

        raise PrinterServiceError("PRINTER_NOT_CONFIGURED", "No printer is configured.")

    def _select_raw_printer(self, printer_name: str | None) -> SelectedPrinter:
        selection = self._select_printer(printer_name)
        if not selection.printer.supports_raw:
            raise PrinterServiceError(
                "RAW_NOT_SUPPORTED",
                f"Printer {selection.printer.name!r} does not support RAW receipt printing.",
            )
        return selection

    def _printer_drivers(self, *, optional: bool = False) -> tuple[PrinterDriver, ...]:
        drivers = self.driver_registry.printer_drivers(available_only=True)
        if drivers or optional:
            return drivers
        raise PrinterServiceError(
            "NO_PRINTER_DRIVER",
            "No printer driver is available on this machine.",
        )

    @staticmethod
    def _find_printer(driver: PrinterDriver, printer_name: str) -> PrinterDevice | None:
        normalized_name = printer_name.casefold()
        for printer in driver.list_devices():
            if printer.name.casefold() == normalized_name:
                return printer
        return None


__all__ = ["PrinterService"]
