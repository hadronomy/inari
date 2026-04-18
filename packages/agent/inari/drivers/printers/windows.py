from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, ClassVar, Iterator, Protocol, Sequence

from ...exceptions import PrinterServiceError
from ...printers import EscPosCommands
from ...printers.types import (
    PrintJobResult,
    PrinterCapabilities,
    PrinterDevice,
    PrinterTransport,
    RenderedDocument,
)
from ..base import DeviceKind, DriverMetadata
from .base import PrinterDriver
from .common import RECEIPT_RAW_NAME_HINTS, guess_preferred_transport

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import-time platform boundary
    import win32print as _win32print
except Exception:  # pragma: no cover
    _win32print = None


class Win32PrintAPI(Protocol):
    PRINTER_ENUM_CONNECTIONS: int
    PRINTER_ENUM_LOCAL: int

    def EnumPrinters(self, flags: int) -> Sequence[Any]: ...

    def GetDefaultPrinter(self) -> str: ...

    def OpenPrinter(self, printer_name: str) -> Any: ...

    def ClosePrinter(self, handle: Any) -> None: ...

    def StartDocPrinter(
        self, handle: Any, level: int, document: tuple[str, None, str]
    ) -> int: ...

    def EndDocPrinter(self, handle: Any) -> None: ...

    def StartPagePrinter(self, handle: Any) -> None: ...

    def EndPagePrinter(self, handle: Any) -> None: ...

    def WritePrinter(self, handle: Any, payload: bytes) -> int: ...


@dataclass(slots=True, frozen=True)
class SpoolWriteResult:
    bytes_written: int
    job_id: int | None = None


class WindowsSpooler:
    def __init__(self, api: Win32PrintAPI | None = None) -> None:
        self._api = api or _win32print

    def is_available(self) -> bool:
        return self._api is not None

    def list_printer_names(self) -> tuple[str, ...]:
        api = self._require_api()
        flags = api.PRINTER_ENUM_LOCAL | api.PRINTER_ENUM_CONNECTIONS
        names = [
            self._printer_name_from_enum_entry(entry)
            for entry in api.EnumPrinters(flags)
        ]
        return tuple(sorted(names, key=str.casefold))

    def get_default_printer_name(self, *, optional: bool = False) -> str | None:
        api = self._require_api(optional=optional)
        if api is None:
            return None

        try:
            return api.GetDefaultPrinter()
        except Exception as exc:
            if optional:
                return None
            raise PrinterServiceError("DEFAULT_PRINTER_NOT_FOUND", str(exc)) from exc

    def write_job(
        self,
        *,
        printer_name: str,
        payload: bytes,
        data_type: str,
        document_name: str,
        use_page_calls: bool,
    ) -> SpoolWriteResult:
        api = self._require_api()
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
                job_id = api.StartDocPrinter(
                    handle, 1, (document_name, None, data_type)
                )
                started_doc = True

                if use_page_calls:
                    api.StartPagePrinter(handle)
                    started_page = True

                bytes_written = api.WritePrinter(handle, payload)

                if started_page:
                    api.EndPagePrinter(handle)
                    started_page = False

                api.EndDocPrinter(handle)
                started_doc = False
            except Exception as exc:
                if started_page:
                    try:
                        api.EndPagePrinter(handle)
                    except Exception:  # pragma: no cover - best effort cleanup
                        logger.debug(
                            "Failed to end page for %s", printer_name, exc_info=True
                        )
                if started_doc:
                    try:
                        api.EndDocPrinter(handle)
                    except Exception:  # pragma: no cover - best effort cleanup
                        logger.debug(
                            "Failed to end document for %s", printer_name, exc_info=True
                        )
                raise PrinterServiceError("PRINT_FAILED", str(exc)) from exc

        logger.info(
            "Submitted print job %s to %s with job id %s",
            document_name,
            printer_name,
            job_id,
        )
        return SpoolWriteResult(bytes_written=bytes_written, job_id=job_id)

    @contextmanager
    def _open_printer(self, printer_name: str) -> Iterator[Any]:
        api = self._require_api()
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
                logger.debug(
                    "Failed to close printer handle for %s", printer_name, exc_info=True
                )

    @staticmethod
    def _printer_name_from_enum_entry(entry: Any) -> str:
        if isinstance(entry, dict):
            value = entry.get("pPrinterName")
            if isinstance(value, str) and value:
                return value

        if isinstance(entry, tuple) and len(entry) >= 3 and isinstance(entry[2], str):
            return entry[2]

        raise PrinterServiceError(
            "PRINTER_ENUM_FAILED", f"Unsupported printer enumeration entry: {entry!r}"
        )

    def _require_api(self, *, optional: bool = False) -> Win32PrintAPI | None:
        if self._api is not None:
            return self._api
        if optional:
            return None
        raise PrinterServiceError(
            "WIN32_UNAVAILABLE", "pywin32 is not available on this machine."
        )


@dataclass(slots=True)
class WindowsPrinterDriver(PrinterDriver):
    spooler: WindowsSpooler
    default_transport: PrinterTransport = PrinterTransport.AUTO
    raw_name_hints: frozenset[str] = field(
        default_factory=lambda: RECEIPT_RAW_NAME_HINTS
    )

    metadata: ClassVar[DriverMetadata] = DriverMetadata(
        key="windows.printers",
        display_name="Windows Print Spooler",
        kind=DeviceKind.PRINTER,
        platform="windows",
    )

    def is_available(self) -> bool:
        return self.spooler.is_available()

    def list_devices(self) -> tuple[PrinterDevice, ...]:
        default_name = self.get_default_device_name()
        devices = [
            self._build_device(name, is_default=name == default_name)
            for name in self.spooler.list_printer_names()
        ]
        return tuple(
            sorted(
                devices, key=lambda item: (not item.is_default, item.name.casefold())
            )
        )

    def get_device(self, printer_name: str) -> PrinterDevice:
        default_name = self.get_default_device_name()
        return self._build_device(printer_name, is_default=printer_name == default_name)

    def get_default_device_name(self) -> str | None:
        return self.spooler.get_default_printer_name(optional=True)

    def resolve_transport(
        self,
        printer: PrinterDevice,
        requested: PrinterTransport,
    ) -> PrinterTransport:
        if requested is not PrinterTransport.AUTO:
            self._ensure_transport_supported(printer, requested)
            return requested

        if self.default_transport is not PrinterTransport.AUTO:
            self._ensure_transport_supported(printer, self.default_transport)
            return self.default_transport

        return printer.preferred_transport

    def submit_raw_job(
        self,
        printer: PrinterDevice,
        payload: bytes,
        *,
        document_name: str,
    ) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.RAW)
        result = self.spooler.write_job(
            printer_name=printer.name,
            payload=payload,
            data_type="RAW",
            document_name=document_name,
            use_page_calls=True,
        )
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.RAW,
            bytes_written=result.bytes_written,
            job_id=result.job_id,
        )

    def submit_text_job(
        self,
        printer: PrinterDevice,
        text: str,
        *,
        document_name: str,
    ) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.TEXT)
        payload = text.replace("\n", "\r\n").encode("mbcs", errors="replace")
        result = self.spooler.write_job(
            printer_name=printer.name,
            payload=payload,
            data_type="TEXT",
            document_name=document_name,
            use_page_calls=False,
        )
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.TEXT,
            bytes_written=result.bytes_written,
            job_id=result.job_id,
        )

    def submit_document_job(
        self,
        printer: PrinterDevice,
        document: RenderedDocument,
    ) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.DOCUMENT)
        result = self.spooler.write_job(
            printer_name=printer.name,
            payload=document.content,
            data_type=document.data_type,
            document_name=document.document_name,
            use_page_calls=document.data_type.upper() == "RAW",
        )
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.DOCUMENT,
            bytes_written=result.bytes_written,
            job_id=result.job_id,
        )

    def open_cash_drawer(self, printer: PrinterDevice) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.RAW)
        result = self.spooler.write_job(
            printer_name=printer.name,
            payload=EscPosCommands.DRAWER_PULSE,
            data_type="RAW",
            document_name="Open Drawer",
            use_page_calls=True,
        )
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.RAW,
            bytes_written=result.bytes_written,
            job_id=result.job_id,
        )

    def _build_device(self, printer_name: str, *, is_default: bool) -> PrinterDevice:
        preferred_transport = self._guess_preferred_transport(printer_name)
        supports_raw = preferred_transport is PrinterTransport.RAW
        return PrinterDevice(
            name=printer_name,
            driver_key=self.metadata.key,
            is_default=is_default,
            preferred_transport=preferred_transport,
            capabilities=PrinterCapabilities(
                raw=supports_raw,
                text=True,
                documents=True,
                cash_drawer=supports_raw,
            ),
        )

    def _guess_preferred_transport(self, printer_name: str) -> PrinterTransport:
        return guess_preferred_transport(
            printer_name, raw_name_hints=self.raw_name_hints
        )

    @staticmethod
    def _ensure_transport_supported(
        printer: PrinterDevice, transport: PrinterTransport
    ) -> None:
        match transport:
            case PrinterTransport.RAW if not printer.supports_raw:
                raise PrinterServiceError(
                    "RAW_NOT_SUPPORTED",
                    f"Printer {printer.name!r} does not support RAW receipt printing.",
                )
            case PrinterTransport.TEXT if not printer.supports_text:
                raise PrinterServiceError(
                    "TEXT_NOT_SUPPORTED",
                    f"Printer {printer.name!r} does not support text printing.",
                )
            case PrinterTransport.DOCUMENT if not printer.supports_documents:
                raise PrinterServiceError(
                    "DOCUMENT_NOT_SUPPORTED",
                    f"Printer {printer.name!r} does not support rendered document printing.",
                )
