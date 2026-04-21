from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..config import AgentSettings
from ..drivers import DriverRegistry
from ..core.exceptions import PrinterServiceError
from .drivers.base import PrinterDriver
from .jobs import (
    HtmlDocumentContent,
    PdfDocumentContent,
    PrintJob,
    RawDocumentContent,
    ReceiptImageContent,
    StructuredReceiptContent,
    TextDocumentContent,
)
from .protocols import (
    CutMode,
    EscPosCommands,
    PrintJobResult,
    PrinterDevice,
    PrinterTransport,
    RenderedDocument,
)
from .renderers import EscPosImageReceiptRenderer, EscPosRenderer

logger = logging.getLogger(__name__)


class StructuredReceiptRenderer(Protocol):
    def render(self, receipt: Mapping[str, Any]) -> bytes:
        """Convert structured receipt data into printer-native receipt bytes."""


class ReceiptImageRenderer(Protocol):
    def render(self, image_bytes: bytes, *, mime_type: str | None = None) -> bytes:
        """Convert a receipt image into printer-native receipt bytes."""


class HtmlDocumentRenderer(Protocol):
    def render_html(self, html: str, *, document_name: str) -> RenderedDocument:
        """Convert HTML into a spooler-ready document."""


class PdfDocumentRenderer(Protocol):
    def render_pdf(self, pdf_bytes: bytes, *, document_name: str) -> RenderedDocument:
        """Convert PDF bytes into a spooler-ready document."""


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
        structured_receipt_renderer: StructuredReceiptRenderer | None = None,
        image_receipt_renderer: ReceiptImageRenderer | None = None,
        html_renderer: HtmlDocumentRenderer | None = None,
        pdf_renderer: PdfDocumentRenderer | None = None,
    ) -> None:
        self.settings = settings
        self.driver_registry = driver_registry
        self.structured_receipt_renderer = (
            structured_receipt_renderer or EscPosRenderer()
        )
        self.image_receipt_renderer = (
            image_receipt_renderer or EscPosImageReceiptRenderer()
        )
        self.html_renderer = html_renderer
        self.pdf_renderer = pdf_renderer

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

    def print_job(self, job: PrintJob) -> PrintJobResult:
        content = job.content

        if isinstance(content, StructuredReceiptContent):
            result = self.print_receipt_data(
                content.payload,
                printer_name=job.printer_name,
                transport=job.transport,
                document_name=content.document_name,
            )
        elif isinstance(content, ReceiptImageContent):
            result = self.print_receipt_image(
                content.image_bytes,
                mime_type=content.mime_type,
                printer_name=job.printer_name,
                transport=job.transport,
                document_name=content.document_name,
            )
        elif isinstance(content, TextDocumentContent):
            result = self.print_text_document(
                content.text,
                printer_name=job.printer_name,
                document_name=content.document_name,
            )
        elif isinstance(content, HtmlDocumentContent):
            result = self.print_html_document(
                content.html,
                printer_name=job.printer_name,
                document_name=content.document_name,
            )
        elif isinstance(content, PdfDocumentContent):
            result = self.print_pdf_document(
                content.pdf_bytes,
                printer_name=job.printer_name,
                document_name=content.document_name,
            )
        elif isinstance(content, RawDocumentContent):
            result = self.print_raw_document(
                content.payload,
                printer_name=job.printer_name,
                transport=job.transport,
                data_type=content.data_type,
                document_name=content.document_name,
            )
        else:  # pragma: no cover - defensive path
            raise PrinterServiceError(
                "UNSUPPORTED_PRINT_CONTENT",
                f"Unsupported print content: {type(content)!r}.",
            )

        if job.open_drawer:
            self.open_cash_drawer(printer_name=job.printer_name)

        return result

    def print_receipt_data(
        self,
        receipt: Mapping[str, Any],
        *,
        printer_name: str | None = None,
        transport: PrinterTransport | str = PrinterTransport.AUTO,
        feed_lines_after: int = 0,
        cut: CutMode | str | None = None,
        document_name: str = "Receipt",
    ) -> PrintJobResult:
        payload = self.structured_receipt_renderer.render(dict(receipt))
        return self.print_receipt_bytes(
            payload,
            printer_name=printer_name,
            transport=transport,
            feed_lines_after=feed_lines_after,
            cut=cut,
            document_name=document_name,
        )

    def print_receipt_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str | None = "image/jpeg",
        printer_name: str | None = None,
        transport: PrinterTransport | str = PrinterTransport.AUTO,
        feed_lines_after: int = 0,
        cut: CutMode | str | None = None,
        document_name: str = "Receipt",
    ) -> PrintJobResult:
        payload = self.image_receipt_renderer.render(image_bytes, mime_type=mime_type)
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
        document_name: str = "Receipt",
    ) -> PrintJobResult:
        selection = self._select_printer(printer_name)
        resolved_transport = selection.driver.resolve_transport(
            selection.printer, PrinterTransport(transport)
        )
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
        logger.info(
            "Submitted RAW receipt to %s through %s",
            result.printer_name,
            selection.driver.metadata.key,
        )
        return result

    def print_raw_document(
        self,
        payload: bytes,
        *,
        printer_name: str | None = None,
        transport: PrinterTransport | str = PrinterTransport.AUTO,
        data_type: str = "RAW",
        document_name: str = "Raw Document",
    ) -> PrintJobResult:
        requested_transport = PrinterTransport(transport)
        if requested_transport not in {PrinterTransport.AUTO, PrinterTransport.RAW}:
            raise PrinterServiceError(
                "RAW_TRANSPORT_REQUIRED",
                "Raw documents must use the RAW transport.",
            )
        if data_type.upper() != "RAW":
            raise PrinterServiceError(
                "RAW_DATA_TYPE_REQUIRED",
                "Raw documents must declare the RAW spooler data type.",
            )

        selection = self._select_raw_printer(printer_name)
        return selection.driver.submit_raw_job(
            selection.printer,
            payload,
            document_name=document_name,
        )

    def print_text_document(
        self,
        text: str,
        *,
        printer_name: str | None = None,
        document_name: str = "Text Document",
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
        document_name: str = "HTML Document",
    ) -> PrintJobResult:
        if not self.settings.html_print_enabled:
            raise PrinterServiceError(
                "HTML_MODE_DISABLED",
                "HTML printing is disabled.",
            )
        if self.html_renderer is None:
            raise PrinterServiceError(
                "HTML_RENDERER_NOT_CONFIGURED",
                "HTML is not directly printable by the Windows spooler. "
                "Configure an HTML renderer that converts HTML to printer-ready bytes.",
            )

        rendered = self.html_renderer.render_html(html, document_name=document_name)
        return self.print_rendered_document(rendered, printer_name=printer_name)

    def print_pdf_document(
        self,
        pdf_bytes: bytes,
        *,
        printer_name: str | None = None,
        document_name: str = "PDF Document",
    ) -> PrintJobResult:
        if self.pdf_renderer is None:
            raise PrinterServiceError(
                "PDF_RENDERER_NOT_CONFIGURED",
                "PDF printing requires a renderer or external print pipeline to turn PDF bytes into a printable job.",
            )

        rendered = self.pdf_renderer.render_pdf(pdf_bytes, document_name=document_name)
        return self.print_rendered_document(rendered, printer_name=printer_name)

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
        transport: PrinterTransport | str = PrinterTransport.AUTO,
    ) -> PrintJobResult:
        selection = self._select_printer(printer_name)
        resolved_transport = selection.driver.resolve_transport(
            selection.printer, PrinterTransport(transport)
        )

        if resolved_transport is PrinterTransport.RAW:
            payload = (
                EscPosCommands.INITIALIZE
                + b"\x1b!\x38Inari\n"
                + b"\x1b!\x00Connectivity check\n"
                + b"------------------------------------------\n"
                + b"The agent can reach the receipt printer.\n"
                + b"\n"
                + b"Generic print-job routing is active.\n"
                + b"\n\n"
                + EscPosCommands.cut(CutMode.PARTIAL)
            )
            return selection.driver.submit_raw_job(
                selection.printer,
                payload,
                document_name="RAW Test",
            )

        return selection.driver.submit_text_job(
            selection.printer,
            "Inari\n"
            "Connectivity check\n"
            "------------------------------------------\n"
            "The agent can reach the printer.\n",
            document_name="TEXT Test",
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


__all__ = [
    "CutMode",
    "HtmlDocumentRenderer",
    "PdfDocumentRenderer",
    "PrintJob",
    "PrinterService",
    "PrinterTransport",
    "ReceiptImageRenderer",
    "RenderedDocument",
    "StructuredReceiptRenderer",
]
