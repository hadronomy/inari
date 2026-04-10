from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .config import AgentSettings
from .drivers import DriverRegistry
from .drivers.printers.base import PrinterDriver
from .exceptions import PrinterServiceError
from .printers import CutMode, EscPosCommands, PrintJobResult, PrinterDevice, PrinterTransport, RenderedDocument
from .receipt_renderers import EscPosRenderer

logger = logging.getLogger(__name__)


class HtmlDocumentRenderer(Protocol):
    def render_html(self, html: str, *, title: str) -> RenderedDocument:
        """Convert HTML into a spooler-ready document."""


@dataclass(slots=True, frozen=True)
class SelectedPrinter:
    driver: PrinterDriver
    printer: PrinterDevice


class PrinterService:
    """Application service for printer-focused workflows."""

    def __init__(
        self,
        settings: AgentSettings,
        *,
        driver_registry: DriverRegistry,
        escpos_renderer: EscPosRenderer | None = None,
        html_renderer: HtmlDocumentRenderer | None = None,
    ) -> None:
        self.settings = settings
        self.driver_registry = driver_registry
        self.escpos_renderer = escpos_renderer or EscPosRenderer()
        self.html_renderer = html_renderer

    def list_printers(self) -> tuple[PrinterDevice, ...]:
        printers: list[PrinterDevice] = []
        for driver in self._printer_drivers():
            printers.extend(driver.list_devices())
        return tuple(sorted(printers, key=lambda item: (not item.is_default, item.name.casefold())))

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

    def print_odoo_receipt(
        self,
        receipt: Mapping[str, Any],
        *,
        printer_name: str | None = None,
        transport: PrinterTransport | str = PrinterTransport.AUTO,
        feed_lines_after: int = 0,
        cut: CutMode | str | None = None,
        document_name: str = "Odoo Receipt",
    ) -> PrintJobResult:
        payload = self.escpos_renderer.render(dict(receipt))
        return self.print_receipt_bytes(
            payload,
            printer_name=printer_name,
            transport=transport,
            feed_lines_after=feed_lines_after,
            cut=cut,
            document_name=document_name,
        )

    def print_receipt_bytes(
        self,
        payload: bytes,
        *,
        printer_name: str | None = None,
        transport: PrinterTransport | str = PrinterTransport.AUTO,
        feed_lines_after: int = 0,
        cut: CutMode | str | None = None,
        document_name: str = "Odoo Receipt",
    ) -> PrintJobResult:
        selection = self._select_printer(printer_name)
        resolved_transport = selection.driver.resolve_transport(selection.printer, PrinterTransport(transport))
        if resolved_transport is not PrinterTransport.RAW:
            raise PrinterServiceError(
                "RAW_NOT_SUPPORTED",
                f"Printer {selection.printer.name!r} does not support RAW receipt printing.",
            )

        final_payload = bytearray(payload)
        if feed_lines_after > 0:
            final_payload.extend(EscPosCommands.feed_lines(feed_lines_after))
        if cut is not None:
            final_payload.extend(EscPosCommands.cut(CutMode(cut)))

        result = selection.driver.submit_raw_job(
            selection.printer,
            bytes(final_payload),
            document_name=document_name,
        )
        logger.info("Submitted RAW receipt to %s through %s", result.printer_name, selection.driver.metadata.key)
        return result

    def print_text_document(
        self,
        text: str,
        *,
        printer_name: str | None = None,
        document_name: str = "Odoo Text Document",
    ) -> PrintJobResult:
        selection = self._select_printer(printer_name)
        return selection.driver.submit_text_job(
            selection.printer,
            text,
            document_name=document_name,
        )

    def print_rendered_document(
        self,
        document: RenderedDocument,
        *,
        printer_name: str | None = None,
    ) -> PrintJobResult:
        selection = self._select_printer(printer_name)
        return selection.driver.submit_document_job(selection.printer, document)

    def print_html_document(
        self,
        html: str,
        *,
        printer_name: str | None = None,
        title: str = "Odoo HTML Document",
    ) -> PrintJobResult:
        if self.html_renderer is None:
            raise PrinterServiceError(
                "HTML_RENDERER_NOT_CONFIGURED",
                "HTML is not directly printable by the Windows spooler. "
                "Configure an HTML renderer that converts HTML to printer-ready bytes.",
            )

        rendered = self.html_renderer.render_html(html, title=title)
        return self.print_rendered_document(rendered, printer_name=printer_name)

    def feed_lines(self, count: int, *, printer_name: str | None = None) -> PrintJobResult:
        selection = self._select_raw_printer(printer_name)
        return selection.driver.submit_raw_job(
            selection.printer,
            EscPosCommands.feed_lines(count),
            document_name="Odoo Feed Lines",
        )

    def feed_dots(self, count: int, *, printer_name: str | None = None) -> PrintJobResult:
        selection = self._select_raw_printer(printer_name)
        return selection.driver.submit_raw_job(
            selection.printer,
            EscPosCommands.feed_dots(count),
            document_name="Odoo Feed Dots",
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
            document_name="Odoo Cut Paper",
        )

    def open_cash_drawer(self, *, printer_name: str | None = None) -> PrintJobResult:
        selection = self._select_raw_printer(printer_name)
        return selection.driver.open_cash_drawer(selection.printer)

    def print_test_ticket(
        self,
        *,
        printer_name: str | None = None,
        transport: PrinterTransport | str = PrinterTransport.AUTO,
    ) -> PrintJobResult:
        selection = self._select_printer(printer_name)
        resolved_transport = selection.driver.resolve_transport(selection.printer, PrinterTransport(transport))

        if resolved_transport is PrinterTransport.RAW:
            payload = (
                EscPosCommands.INITIALIZE
                + b"\x1b!\x38Odoo IoT Agent\n"
                + b"\x1b!\x00Connectivity check\n"
                + b"------------------------------------------\n"
                + b"The agent can reach the receipt printer.\n"
                + b"\n"
                + b"Driver architecture is active and ready for extension.\n"
                + b"\n\n"
                + EscPosCommands.cut(CutMode.PARTIAL)
            )
            return selection.driver.submit_raw_job(
                selection.printer,
                payload,
                document_name="Odoo IoT RAW Test",
            )

        return selection.driver.submit_text_job(
            selection.printer,
            "Odoo IoT Agent\n"
            "Connectivity check\n"
            "------------------------------------------\n"
            "The agent can reach the printer.\n",
            document_name="Odoo IoT TEXT Test",
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
                return SelectedPrinter(driver=driver, printer=driver.get_device(default_name))

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


__all__ = [
    "CutMode",
    "HtmlDocumentRenderer",
    "PrinterService",
    "PrinterTransport",
    "RenderedDocument",
]
