from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping, Protocol

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
    import cups as _cups
except Exception:  # pragma: no cover
    _cups = None


class CupsConnection(Protocol):
    def getPrinters(self) -> Mapping[str, Mapping[str, Any]]: ...

    def getDefault(self) -> str | None: ...


class CupsAPI(Protocol):
    def Connection(self) -> CupsConnection: ...


@dataclass(slots=True)
class CupsPrinterDriver(PrinterDriver):
    cups_api: CupsAPI | None = None
    default_transport: PrinterTransport = PrinterTransport.AUTO
    raw_name_hints: frozenset[str] = field(
        default_factory=lambda: RECEIPT_RAW_NAME_HINTS
    )

    metadata: ClassVar[DriverMetadata] = DriverMetadata(
        key="cups.printers",
        display_name="CUPS Printer Service",
        kind=DeviceKind.PRINTER,
        platform="linux,macos",
    )

    def __post_init__(self) -> None:
        if self.cups_api is None:
            self.cups_api = _cups

    def is_available(self) -> bool:
        if self._lp_command() is not None and self._lpstat_command() is not None:
            return True
        return self._connection(optional=True) is not None

    def list_devices(self) -> tuple[PrinterDevice, ...]:
        default_name = self.get_default_device_name()
        printer_attributes = self._list_printer_attributes()
        if printer_attributes:
            devices = [
                self._build_device(
                    name, is_default=name == default_name, attributes=attributes
                )
                for name, attributes in printer_attributes.items()
            ]
        else:
            devices = [
                self._build_device(name, is_default=name == default_name, attributes={})
                for name in self._list_printer_names_from_cli()
            ]
        return tuple(
            sorted(
                devices, key=lambda item: (not item.is_default, item.name.casefold())
            )
        )

    def get_device(self, printer_name: str) -> PrinterDevice:
        default_name = self.get_default_device_name()
        attributes = self._list_printer_attributes().get(printer_name, {})
        return self._build_device(
            printer_name, is_default=printer_name == default_name, attributes=attributes
        )

    def get_default_device_name(self) -> str | None:
        connection = self._connection(optional=True)
        if connection is not None:
            try:
                return connection.getDefault()
            except Exception:
                logger.debug("Failed to query CUPS default printer", exc_info=True)
        return self._default_printer_from_cli(optional=True)

    def resolve_transport(
        self, printer: PrinterDevice, requested: PrinterTransport
    ) -> PrinterTransport:
        if requested is not PrinterTransport.AUTO:
            self._ensure_transport_supported(printer, requested)
            return requested
        if self.default_transport is not PrinterTransport.AUTO:
            self._ensure_transport_supported(printer, self.default_transport)
            return self.default_transport
        return printer.preferred_transport

    def submit_raw_job(
        self, printer: PrinterDevice, payload: bytes, *, document_name: str
    ) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.RAW)
        job_id = self._submit_bytes(
            printer_name=printer.name,
            payload=payload,
            document_name=document_name,
            raw=True,
        )
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.RAW,
            bytes_written=len(payload),
            job_id=job_id,
        )

    def submit_text_job(
        self, printer: PrinterDevice, text: str, *, document_name: str
    ) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.TEXT)
        payload = text.encode("utf-8", errors="replace")
        job_id = self._submit_bytes(
            printer_name=printer.name,
            payload=payload,
            document_name=document_name,
            raw=False,
        )
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.TEXT,
            bytes_written=len(payload),
            job_id=job_id,
        )

    def submit_document_job(
        self, printer: PrinterDevice, document: RenderedDocument
    ) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.DOCUMENT)
        job_id = self._submit_bytes(
            printer_name=printer.name,
            payload=document.content,
            document_name=document.document_name,
            raw=document.data_type.upper() == "RAW",
        )
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.DOCUMENT,
            bytes_written=len(document.content),
            job_id=job_id,
        )

    def open_cash_drawer(self, printer: PrinterDevice) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.RAW)
        return self.submit_raw_job(
            printer, EscPosCommands.DRAWER_PULSE, document_name="Open Drawer"
        )

    def _build_device(
        self,
        printer_name: str,
        *,
        is_default: bool,
        attributes: Mapping[str, Any],
    ) -> PrinterDevice:
        device_uri = str(attributes.get("device-uri", ""))
        preferred_transport = guess_preferred_transport(
            printer_name,
            raw_name_hints=self.raw_name_hints,
            device_uri=device_uri,
        )
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
            metadata={
                "source": "cups",
                **({"device_uri": device_uri} if device_uri else {}),
            },
        )

    def _list_printer_attributes(self) -> Mapping[str, Mapping[str, Any]]:
        connection = self._connection(optional=True)
        if connection is None:
            return {}
        try:
            return connection.getPrinters()
        except Exception as exc:
            raise PrinterServiceError(
                "DEVICE_DISCOVERY_FAILED", "Unable to enumerate CUPS printers."
            ) from exc

    def _list_printer_names_from_cli(self) -> tuple[str, ...]:
        result = self._run_cli(self._lpstat_command(), "-e")
        names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return tuple(sorted(names, key=str.casefold))

    def _default_printer_from_cli(self, *, optional: bool) -> str | None:
        try:
            result = self._run_cli(self._lpstat_command(), "-d")
        except PrinterServiceError:
            if optional:
                return None
            raise
        match = re.search(r":\s*(.+)$", result.stdout.strip())
        if match is None:
            return None if optional else ""
        return match.group(1).strip() or None

    def _submit_bytes(
        self,
        *,
        printer_name: str,
        payload: bytes,
        document_name: str,
        raw: bool,
    ) -> int | None:
        lp_command = self._lp_command()
        if lp_command is not None:
            return self._submit_with_cli(
                lp_command=lp_command,
                printer_name=printer_name,
                payload=payload,
                document_name=document_name,
                raw=raw,
            )
        raise PrinterServiceError(
            "PRINT_FAILED",
            "CUPS printing is unavailable because the lp command was not found.",
        )

    def _submit_with_cli(
        self,
        *,
        lp_command: str,
        printer_name: str,
        payload: bytes,
        document_name: str,
        raw: bool,
    ) -> int | None:
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        try:
            command = [lp_command, "-d", printer_name, "-t", document_name]
            if raw:
                command.extend(["-o", "raw"])
            command.append(str(temp_path))
            result = self._run_cli(*command)
        finally:
            temp_path.unlink(missing_ok=True)
        return _parse_cups_job_id(result.stdout)

    def _run_cli(self, *command: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(command),
                capture_output=True,
                check=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.strip() or exc.stdout.strip() or "CUPS command failed."
            raise PrinterServiceError("PRINT_FAILED", message) from exc

    def _connection(self, *, optional: bool) -> CupsConnection | None:
        if self.cups_api is None:
            return None if optional else self._raise_cups_unavailable()
        try:
            return self.cups_api.Connection()
        except Exception as exc:
            if optional:
                return None
            raise PrinterServiceError(
                "NO_PRINTER_DRIVER", "Unable to connect to the local CUPS service."
            ) from exc

    @staticmethod
    def _raise_cups_unavailable() -> None:
        raise PrinterServiceError(
            "NO_PRINTER_DRIVER", "CUPS support is not available on this machine."
        )

    @staticmethod
    def _ensure_transport_supported(
        printer: PrinterDevice, transport: PrinterTransport
    ) -> None:
        supported = {
            PrinterTransport.RAW: printer.supports_raw,
            PrinterTransport.TEXT: printer.supports_text,
            PrinterTransport.DOCUMENT: printer.supports_documents,
        }
        if supported.get(transport, False):
            return
        raise PrinterServiceError(
            "UNSUPPORTED_TRANSPORT",
            f"Printer {printer.name!r} does not support the {transport.value!r} transport.",
        )

    @staticmethod
    def _lp_command() -> str | None:
        return shutil.which("lp")

    @staticmethod
    def _lpstat_command() -> str | None:
        return shutil.which("lpstat")


def _parse_cups_job_id(output: str) -> int | None:
    match = re.search(r"request id is .+-(\d+)", output)
    if match is None:
        return None
    return int(match.group(1))
