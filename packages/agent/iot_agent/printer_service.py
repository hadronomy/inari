from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterator, Mapping, Protocol

from .config import AgentSettings
from .exceptions import PrinterServiceError
from .receipt_renderers.escpos_renderer import EscPosRenderer

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import-time platform boundary
    import win32print
except Exception:  # pragma: no cover
    win32print = None


_RAW_HINTS = (
    "epson tm",
    "tm-t",
    "receipt",
    "pos",
    "esc/pos",
    "thermal",
    "star tsp",
    "bixolon",
)


class PrinterTransport(StrEnum):
    AUTO = "auto"
    RAW = "raw"
    TEXT = "text"
    DOCUMENT = "document"


class CutMode(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


@dataclass(slots=True, frozen=True)
class PrinterInfo:
    name: str
    is_default: bool = False
    preferred_transport: PrinterTransport = PrinterTransport.DOCUMENT
    supports_raw: bool = False
    supports_text: bool = True
    supports_documents: bool = True


@dataclass(slots=True, frozen=True)
class RenderedDocument:
    """
    A printer-ready document.

    `data_type` is the Windows spooler data type for the target queue.
    Examples include:
    - RAW
    - TEXT
    - XPS_PASS

    The service does not try to magically render HTML/PDF into a printable
    representation. That work belongs to a renderer upstream.
    """

    content: bytes
    data_type: str = "RAW"
    document_name: str = "Odoo Document"


class HtmlDocumentRenderer(Protocol):
    """
    Converts HTML into printer-ready bytes.

    A good implementation would typically produce XPS, EMF, PDF routed through
    a real print pipeline, or printer-native language for the target device.
    """

    def render_html(self, html: str, *, title: str) -> RenderedDocument: ...


class PrinterService:
    """
    Windows printer service with a clear separation between:

    - receipt printing: ESC/POS RAW bytes for receipt printers
    - text printing: plain text through the Windows TEXT print processor
    - document printing: printer-ready bytes produced by a real renderer

    Design principles:
    - no ShellExecute "printto" path
    - no pretending that HTML is directly printable
    - explicit transport selection
    - explicit paper-control commands for receipt printers
    """

    def __init__(
        self,
        settings: AgentSettings,
        *,
        escpos_renderer: EscPosRenderer | None = None,
        html_renderer: HtmlDocumentRenderer | None = None,
    ) -> None:
        self.settings = settings
        self.escpos_renderer = escpos_renderer or EscPosRenderer()
        self.html_renderer = html_renderer

    # -------------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------------

    def list_printers(self) -> list[PrinterInfo]:
        api = self._require_print_api()
        flags = api.PRINTER_ENUM_LOCAL | api.PRINTER_ENUM_CONNECTIONS
        default_name = self.get_default_printer_name(optional=True)

        printers: list[PrinterInfo] = []
        for entry in api.EnumPrinters(flags):
            name = self._printer_name_from_enum_entry(entry)
            preferred_transport = self._guess_preferred_transport(name)
            printers.append(
                PrinterInfo(
                    name=name,
                    is_default=name == default_name,
                    preferred_transport=preferred_transport,
                    supports_raw=preferred_transport is PrinterTransport.RAW,
                    supports_text=True,
                    supports_documents=True,
                )
            )

        return sorted(printers, key=lambda item: (not item.is_default, item.name.casefold()))

    def get_default_printer_name(self, optional: bool = False) -> str | None:
        configured = getattr(self.settings, "default_printer_name", None)
        if configured:
            return configured

        api = self._require_print_api(optional=optional)
        if api is None:
            return None

        try:
            return api.GetDefaultPrinter()
        except Exception as exc:
            if optional:
                return None
            raise PrinterServiceError("DEFAULT_PRINTER_NOT_FOUND", str(exc)) from exc

    def get_printer_info(self, printer_name: str) -> PrinterInfo:
        default_name = self.get_default_printer_name(optional=True)
        preferred_transport = self._guess_preferred_transport(printer_name)
        return PrinterInfo(
            name=printer_name,
            is_default=printer_name == default_name,
            preferred_transport=preferred_transport,
            supports_raw=preferred_transport is PrinterTransport.RAW,
            supports_text=True,
            supports_documents=True,
        )

    # -------------------------------------------------------------------------
    # Receipt printing
    # -------------------------------------------------------------------------

    def print_odoo_receipt(
        self,
        receipt: Mapping[str, Any],
        *,
        printer_name: str | None = None,
        transport: PrinterTransport | str = PrinterTransport.AUTO,
        feed_lines_after: int = 0,
        cut: CutMode | str | None = None,
        document_name: str = "Odoo Receipt",
    ) -> PrinterInfo:
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
    ) -> PrinterInfo:
        target_printer = self._resolve_printer_name(printer_name)
        resolved_transport = self._resolve_transport(target_printer, requested=transport)

        if resolved_transport is not PrinterTransport.RAW:
            raise PrinterServiceError(
                "RAW_NOT_SUPPORTED",
                f"Printer {target_printer!r} does not look like a receipt printer. "
                "ESC/POS receipt bytes must be sent through the RAW transport.",
            )

        final_payload = bytearray(payload)
        if feed_lines_after > 0:
            final_payload.extend(self._escpos_feed_lines_bytes(feed_lines_after))
        if cut is not None:
            final_payload.extend(self._escpos_cut_bytes(CutMode(cut)))

        self._write_spool_bytes(
            bytes(final_payload),
            printer_name=target_printer,
            data_type="RAW",
            document_name=document_name,
            use_page_calls=True,
        )
        return self.get_printer_info(target_printer)

    # -------------------------------------------------------------------------
    # Document printing
    # -------------------------------------------------------------------------

    def print_text_document(
        self,
        text: str,
        *,
        printer_name: str | None = None,
        document_name: str = "Odoo Text Document",
    ) -> PrinterInfo:
        target_printer = self._resolve_printer_name(printer_name)
        payload = text.replace("\n", "\r\n").encode("mbcs", errors="replace")
        self._write_spool_bytes(
            payload,
            printer_name=target_printer,
            data_type="TEXT",
            document_name=document_name,
            use_page_calls=False,
        )
        logger.info("Submitted TEXT document %s to %s", document_name, target_printer)
        return self.get_printer_info(target_printer)

    def print_rendered_document(
        self,
        document: RenderedDocument,
        *,
        printer_name: str | None = None,
    ) -> PrinterInfo:
        target_printer = self._resolve_printer_name(printer_name)
        self._write_spool_bytes(
            document.content,
            printer_name=target_printer,
            data_type=document.data_type,
            document_name=document.document_name,
            use_page_calls=document.data_type.upper() == "RAW",
        )
        logger.info(
            "Submitted rendered document %s (%s) to %s",
            document.document_name,
            document.data_type,
            target_printer,
        )
        return self.get_printer_info(target_printer)

    def print_html_document(
        self,
        html: str,
        *,
        printer_name: str | None = None,
        title: str = "Odoo HTML Document",
    ) -> PrinterInfo:
        if self.html_renderer is None:
            raise PrinterServiceError(
                "HTML_RENDERER_NOT_CONFIGURED",
                "HTML is not directly printable by the Windows spooler. "
                "Configure an HTML renderer that converts HTML to printer-ready bytes.",
            )

        rendered = self.html_renderer.render_html(html, title=title)
        return self.print_rendered_document(rendered, printer_name=printer_name)

    # -------------------------------------------------------------------------
    # Paper / cutter / drawer control for receipt printers
    # -------------------------------------------------------------------------

    def feed_lines(self, count: int, *, printer_name: str | None = None) -> PrinterInfo:
        if count < 1:
            raise PrinterServiceError("INVALID_FEED", "Line feed count must be at least 1.")

        target_printer = self._require_raw_printer(printer_name)
        payload = self._escpos_feed_lines_bytes(count)
        self._write_spool_bytes(
            payload,
            printer_name=target_printer,
            data_type="RAW",
            document_name="Odoo Feed Lines",
            use_page_calls=True,
        )
        return self.get_printer_info(target_printer)

    def feed_dots(self, count: int, *, printer_name: str | None = None) -> PrinterInfo:
        if count < 1:
            raise PrinterServiceError("INVALID_FEED", "Dot feed count must be at least 1.")

        target_printer = self._require_raw_printer(printer_name)
        payload = self._escpos_feed_dots_bytes(count)
        self._write_spool_bytes(
            payload,
            printer_name=target_printer,
            data_type="RAW",
            document_name="Odoo Feed Dots",
            use_page_calls=True,
        )
        return self.get_printer_info(target_printer)

    def cut_paper(self, *, printer_name: str | None = None, mode: CutMode | str = CutMode.PARTIAL) -> PrinterInfo:
        target_printer = self._require_raw_printer(printer_name)
        payload = self._escpos_cut_bytes(CutMode(mode))
        self._write_spool_bytes(
            payload,
            printer_name=target_printer,
            data_type="RAW",
            document_name="Odoo Cut Paper",
            use_page_calls=True,
        )
        return self.get_printer_info(target_printer)

    def open_cash_drawer(self, *, printer_name: str | None = None) -> PrinterInfo:
        target_printer = self._require_raw_printer(printer_name)
        pulse = b"\x1b\x70\x00\x19\xfa"
        self._write_spool_bytes(
            pulse,
            printer_name=target_printer,
            data_type="RAW",
            document_name="Odoo Open Drawer",
            use_page_calls=True,
        )
        return self.get_printer_info(target_printer)

    # -------------------------------------------------------------------------
    # Testing
    # -------------------------------------------------------------------------

    def print_test_ticket(
        self,
        *,
        printer_name: str | None = None,
        transport: PrinterTransport | str = PrinterTransport.AUTO,
    ) -> PrinterInfo:
        target_printer = self._resolve_printer_name(printer_name)
        resolved_transport = self._resolve_transport(target_printer, requested=transport)

        if resolved_transport is PrinterTransport.RAW:
            payload = (
                b"\x1b@"
                b"\x1b!\x38Odoo IoT Agent\n"
                b"\x1b!\x00Connectivity check\n"
                b"------------------------------------------\n"
                b"The agent can reach the receipt printer.\n"
                b"\n"
                b"Use feed_lines / feed_dots / cut_paper for paper control.\n"
                b"\n\n\n"
            )
            return self.print_receipt_bytes(
                payload,
                printer_name=target_printer,
                transport=PrinterTransport.RAW,
                cut=CutMode.PARTIAL,
                document_name="Odoo IoT RAW Test",
            )

        return self.print_text_document(
            "Odoo IoT Agent\n"
            "Connectivity check\n"
            "------------------------------------------\n"
            "The agent can reach the printer.\n",
            printer_name=target_printer,
            document_name="Odoo IoT TEXT Test",
        )

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _resolve_printer_name(self, printer_name: str | None) -> str:
        resolved = printer_name or self.get_default_printer_name()
        if not resolved:
            raise PrinterServiceError("PRINTER_NOT_CONFIGURED", "No printer is configured.")
        return resolved

    def _require_raw_printer(self, printer_name: str | None) -> str:
        target_printer = self._resolve_printer_name(printer_name)
        if self._resolve_transport(target_printer, requested=PrinterTransport.AUTO) is not PrinterTransport.RAW:
            raise PrinterServiceError(
                "RAW_NOT_SUPPORTED",
                f"Printer {target_printer!r} does not look like a RAW receipt printer.",
            )
        return target_printer

    def _resolve_transport(
        self,
        printer_name: str,
        *,
        requested: PrinterTransport | str,
    ) -> PrinterTransport:
        requested_transport = PrinterTransport(requested)
        if requested_transport is not PrinterTransport.AUTO:
            return requested_transport

        configured_default = getattr(self.settings, "default_printer_mode", PrinterTransport.AUTO.value)
        try:
            configured_transport = PrinterTransport(configured_default)
        except ValueError:
            configured_transport = PrinterTransport.AUTO

        if configured_transport is not PrinterTransport.AUTO:
            return configured_transport

        return self._guess_preferred_transport(printer_name)

    def _guess_preferred_transport(self, printer_name: str) -> PrinterTransport:
        normalized = printer_name.casefold()
        if any(hint in normalized for hint in _RAW_HINTS):
            return PrinterTransport.RAW
        return PrinterTransport.DOCUMENT

    def _write_spool_bytes(
        self,
        payload: bytes,
        *,
        printer_name: str,
        data_type: str,
        document_name: str,
        use_page_calls: bool,
    ) -> None:
        api = self._require_print_api()
        logger.info(
            "Submitting %s bytes to printer %s with data type %s",
            len(payload),
            printer_name,
            data_type,
        )

        with self._open_printer(printer_name) as handle:
            started_doc = False
            started_page = False
            try:
                job_id = api.StartDocPrinter(handle, 1, (document_name, None, data_type))
                started_doc = True

                if use_page_calls:
                    api.StartPagePrinter(handle)
                    started_page = True

                api.WritePrinter(handle, payload)

                if started_page:
                    api.EndPagePrinter(handle)
                    started_page = False

                api.EndDocPrinter(handle)
                started_doc = False

                logger.info(
                    "Submitted print job %s to %s with job id %s",
                    document_name,
                    printer_name,
                    job_id,
                )
            except Exception as exc:
                if started_page:
                    try:
                        api.EndPagePrinter(handle)
                    except Exception:  # pragma: no cover - best effort cleanup
                        logger.debug("Failed to end page for %s", printer_name, exc_info=True)
                if started_doc:
                    try:
                        api.EndDocPrinter(handle)
                    except Exception:  # pragma: no cover - best effort cleanup
                        logger.debug("Failed to end document for %s", printer_name, exc_info=True)
                raise PrinterServiceError("PRINT_FAILED", str(exc)) from exc

    @contextmanager
    def _open_printer(self, printer_name: str) -> Iterator[Any]:
        api = self._require_print_api()
        try:
            handle = api.OpenPrinter(printer_name)
        except Exception as exc:
            raise PrinterServiceError("PRINTER_OPEN_FAILED", str(exc)) from exc

        try:
            yield handle
        finally:
            try:
                api.ClosePrinter(handle)
            except Exception:  # pragma: no cover - best effort cleanup
                logger.debug("Failed to close printer handle for %s", printer_name, exc_info=True)

    @staticmethod
    def _printer_name_from_enum_entry(entry: Any) -> str:
        if isinstance(entry, dict):
            value = entry.get("pPrinterName")
            if isinstance(value, str) and value:
                return value

        if isinstance(entry, tuple) and len(entry) >= 3 and isinstance(entry[2], str):
            return entry[2]

        raise PrinterServiceError("PRINTER_ENUM_FAILED", f"Unsupported printer enumeration entry: {entry!r}")

    @staticmethod
    def _escpos_feed_lines_bytes(count: int) -> bytes:
        """
        ESC d n: print and feed n lines.
        """
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = min(remaining, 255)
            chunks.append(b"\x1b\x64" + bytes((chunk,)))
            remaining -= chunk
        return b"".join(chunks)

    @staticmethod
    def _escpos_feed_dots_bytes(count: int) -> bytes:
        """
        ESC J n: print and feed n dots.
        """
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = min(remaining, 255)
            chunks.append(b"\x1b\x4a" + bytes((chunk,)))
            remaining -= chunk
        return b"".join(chunks)

    @staticmethod
    def _escpos_cut_bytes(mode: CutMode) -> bytes:
        if mode is CutMode.FULL:
            return b"\x1d\x56\x00"
        return b"\x1d\x56\x01"

    @staticmethod
    def _require_print_api(*, optional: bool = False):
        if win32print is not None:
            return win32print
        if optional:
            return None
        raise PrinterServiceError("WIN32_UNAVAILABLE", "pywin32 is not available on this machine.")