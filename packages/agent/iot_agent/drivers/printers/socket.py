from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from ...config import NetworkPrinterConfig
from ...exceptions import PrinterServiceError
from ...printers import EscPosCommands
from ...printers.types import PrintJobResult, PrinterCapabilities, PrinterDevice, PrinterTransport, RenderedDocument
from ..base import DeviceKind, DriverMetadata
from .base import PrinterDriver


class SocketFactory(Protocol):
    def __call__(self, address: tuple[str, int], timeout: float = 10.0): ...


@dataclass(slots=True)
class RawSocketPrinterDriver(PrinterDriver):
    configured_printers: tuple[NetworkPrinterConfig, ...] = ()
    connect_timeout_seconds: float = 10.0
    socket_factory: SocketFactory = socket.create_connection

    metadata: ClassVar[DriverMetadata] = DriverMetadata(
        key="socket.printers",
        display_name="Raw Socket Printers",
        kind=DeviceKind.PRINTER,
        platform="any",
    )

    def is_available(self) -> bool:
        return bool(self.configured_printers)

    def list_devices(self) -> tuple[PrinterDevice, ...]:
        return tuple(
            sorted(
                (self._build_device(config) for config in self.configured_printers),
                key=lambda item: (not item.is_default, item.name.casefold()),
            )
        )

    def get_device(self, printer_name: str) -> PrinterDevice:
        config = self._require_config(printer_name)
        return self._build_device(config)

    def get_default_device_name(self) -> str | None:
        for config in self.configured_printers:
            if config.is_default:
                return config.name
        return None

    def resolve_transport(self, printer: PrinterDevice, requested: PrinterTransport) -> PrinterTransport:
        if requested is not PrinterTransport.AUTO:
            self._ensure_transport_supported(printer, requested)
            return requested
        return printer.preferred_transport

    def submit_raw_job(self, printer: PrinterDevice, payload: bytes, *, document_name: str) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.RAW)
        bytes_written = self._send(printer.name, payload)
        return PrintJobResult(printer=printer, transport=PrinterTransport.RAW, bytes_written=bytes_written)

    def submit_text_job(self, printer: PrinterDevice, text: str, *, document_name: str) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.TEXT)
        config = self._require_config(printer.name)
        payload = text.encode(config.encoding, errors="replace")
        bytes_written = self._send(printer.name, payload)
        return PrintJobResult(printer=printer, transport=PrinterTransport.TEXT, bytes_written=bytes_written)

    def submit_document_job(self, printer: PrinterDevice, document: RenderedDocument) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.DOCUMENT)
        bytes_written = self._send(printer.name, document.content)
        return PrintJobResult(printer=printer, transport=PrinterTransport.DOCUMENT, bytes_written=bytes_written)

    def open_cash_drawer(self, printer: PrinterDevice) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.RAW)
        bytes_written = self._send(printer.name, EscPosCommands.DRAWER_PULSE)
        return PrintJobResult(printer=printer, transport=PrinterTransport.RAW, bytes_written=bytes_written)

    def _build_device(self, config: NetworkPrinterConfig) -> PrinterDevice:
        return PrinterDevice(
            name=config.name,
            driver_key=self.metadata.key,
            is_default=config.is_default,
            preferred_transport=PrinterTransport(config.preferred_transport),
            capabilities=PrinterCapabilities(
                raw=True,
                text=config.text_enabled,
                documents=config.document_enabled,
                cash_drawer=config.cash_drawer,
            ),
        )

    def _require_config(self, printer_name: str) -> NetworkPrinterConfig:
        normalized = printer_name.casefold()
        for config in self.configured_printers:
            if config.name.casefold() == normalized:
                return config
        raise PrinterServiceError("PRINTER_NOT_FOUND", f"Printer {printer_name!r} is not configured.")

    def _send(self, printer_name: str, payload: bytes) -> int:
        config = self._require_config(printer_name)
        try:
            with self.socket_factory((config.host, config.port), timeout=self.connect_timeout_seconds) as connection:
                connection.sendall(payload)
        except Exception as exc:
            raise PrinterServiceError(
                "PRINT_FAILED",
                f"Unable to send data to network printer {config.name!r} at {config.host}:{config.port}.",
            ) from exc
        return len(payload)

    @staticmethod
    def _ensure_transport_supported(printer: PrinterDevice, transport: PrinterTransport) -> None:
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
