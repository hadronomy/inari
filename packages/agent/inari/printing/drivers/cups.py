from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping, NoReturn, Protocol, cast

from ...core.exceptions import PrinterServiceError
from ..protocols import EscPosCommands
from ..protocols.types import (
    PrintJobResult,
    PrinterCapabilities,
    PrinterDevice,
    PrinterTransport,
)
from ...drivers.base import DeviceIdentity, DeviceKind, DeviceTransport, DriverMetadata
from .base import PrinterDriver
from .common import RECEIPT_RAW_NAME_HINTS, guess_preferred_transport

logger = logging.getLogger(__name__)


class CupsConnection(Protocol):
    def getPrinters(self) -> Mapping[str, Mapping[str, Any]]: ...

    def getDefault(self) -> str | None: ...

    def getPrinterAttributes(self, printer_name: str) -> Mapping[str, Any]: ...

    def createJob(
        self, printer_name: str, title: str, options: Mapping[str, str]
    ) -> int: ...

    def startDocument(
        self,
        printer_name: str,
        job_id: int,
        document_name: str,
        document_format: str,
        last_document: int,
    ) -> int: ...

    def writeRequestData(self, payload: bytes, length: int) -> int: ...

    def finishDocument(self, printer_name: str) -> int: ...


class CupsAPI(Protocol):
    def Connection(self) -> CupsConnection: ...


@dataclass(slots=True)
class CupsPrinterDriver(PrinterDriver):
    cups_api: CupsAPI | None = None
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
            self.cups_api = _load_cups_api()

    def is_available(self) -> bool:
        return self._connection(optional=True) is not None

    def list_devices(self) -> tuple[PrinterDevice, ...]:
        default_name = self.get_default_device_name()
        printer_attributes = self._list_printer_attributes()
        devices = [
            self._build_device(
                name, is_default=name == default_name, attributes=attributes
            )
            for name, attributes in printer_attributes.items()
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
        return None

    def submit_raw_job(
        self, printer: PrinterDevice, payload: bytes, *, document_name: str
    ) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.RAW)
        job_id = self._submit_document_bytes(
            printer_name=printer.name,
            payload=payload,
            document_name=document_name,
            document_format="application/vnd.cups-raw",
        )
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.RAW,
            bytes_written=len(payload),
            job_id=job_id,
        )

    def submit_document_job(
        self,
        printer: PrinterDevice,
        payload: bytes,
        *,
        media_type: str,
        document_name: str,
    ) -> PrintJobResult:
        if not printer.supports_documents or media_type != "application/pdf":
            raise PrinterServiceError(
                "UNSUPPORTED_TRANSPORT",
                f"Printer {printer.name!r} does not support {media_type!r} documents.",
            )
        self._require_document_format(printer.name, media_type)
        job_id = self._submit_document_bytes(
            printer_name=printer.name,
            payload=payload,
            document_name=document_name,
            document_format=media_type,
        )
        return PrintJobResult(
            printer=printer,
            transport=PrinterTransport.DOCUMENT,
            bytes_written=len(payload),
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
            identity=DeviceIdentity(
                transport=DeviceTransport.SPOOLER,
                os_instance_id=device_uri or f"cups-queue:{printer_name}",
            ),
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

    def _submit_document_bytes(
        self,
        *,
        printer_name: str,
        payload: bytes,
        document_name: str,
        document_format: str,
    ) -> int | None:
        try:
            connection = self._connection(optional=False)
            if connection is None:  # pragma: no cover - narrowed by optional=False
                self._raise_cups_unavailable()
            job_id = connection.createJob(printer_name, document_name, {})
            connection.startDocument(
                printer_name,
                job_id,
                document_name,
                document_format,
                1,
            )
            connection.writeRequestData(payload, len(payload))
            connection.finishDocument(printer_name)
            return job_id
        except PrinterServiceError:
            raise
        except Exception as exc:
            raise PrinterServiceError(
                "PRINT_FAILED", "CUPS rejected the print document."
            ) from exc

    def _require_document_format(self, printer_name: str, media_type: str) -> None:
        connection = self._connection(optional=False)
        if connection is None:  # pragma: no cover - narrowed by optional=False
            self._raise_cups_unavailable()
        try:
            attributes = connection.getPrinterAttributes(printer_name)
        except Exception as exc:
            raise PrinterServiceError(
                "DEVICE_PREFLIGHT_FAILED",
                "CUPS printer capabilities could not be read.",
            ) from exc
        advertised = attributes.get("document-format-supported", ())
        if isinstance(advertised, str):
            advertised = (advertised,)
        if media_type not in advertised:
            raise PrinterServiceError(
                "UNSUPPORTED_TRANSPORT",
                f"Printer {printer_name!r} does not advertise {media_type!r}.",
            )

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
    def _raise_cups_unavailable() -> NoReturn:
        raise PrinterServiceError(
            "NO_PRINTER_DRIVER", "CUPS support is not available on this machine."
        )

    @staticmethod
    def _ensure_transport_supported(
        printer: PrinterDevice, transport: PrinterTransport
    ) -> None:
        if transport is PrinterTransport.RAW and printer.supports_raw:
            return
        raise PrinterServiceError(
            "UNSUPPORTED_TRANSPORT",
            f"Printer {printer.name!r} does not support the {transport.value!r} transport.",
        )


def _load_cups_api() -> CupsAPI | None:
    try:
        return cast(CupsAPI, importlib.import_module("cups"))
    except Exception:  # pragma: no cover - import-time platform boundary
        return None
