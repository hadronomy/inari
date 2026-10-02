from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import ClassVar, Protocol

from ...config import NetworkPrinterConfig
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

    def submit_raw_job(
        self, printer: PrinterDevice, payload: bytes, *, document_name: str
    ) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.RAW)
        bytes_written = self._send(printer.name, payload)
        return PrintJobResult(
            printer=printer, transport=PrinterTransport.RAW, bytes_written=bytes_written
        )

    def submit_document_job(
        self,
        printer: PrinterDevice,
        payload: bytes,
        *,
        media_type: str,
        document_name: str,
        dpi: int,
    ) -> PrintJobResult:
        del payload, media_type, document_name
        raise PrinterServiceError(
            "UNSUPPORTED_TRANSPORT",
            f"Printer {printer.name!r} has no document spooler.",
        )

    def open_cash_drawer(self, printer: PrinterDevice) -> PrintJobResult:
        self._ensure_transport_supported(printer, PrinterTransport.RAW)
        bytes_written = self._send(printer.name, EscPosCommands.DRAWER_PULSE)
        return PrintJobResult(
            printer=printer, transport=PrinterTransport.RAW, bytes_written=bytes_written
        )

    def _build_device(self, config: NetworkPrinterConfig) -> PrinterDevice:
        return PrinterDevice(
            name=config.name,
            driver_key=self.metadata.key,
            identity=DeviceIdentity(
                transport=DeviceTransport.NETWORK,
                os_instance_id=f"tcp://{config.host}:{config.port}",
            ),
            is_default=config.is_default,
            preferred_transport=PrinterTransport(config.preferred_transport),
            capabilities=PrinterCapabilities(
                raw=True,
                text=config.text_enabled,
                documents=config.document_enabled,
                cash_drawer=config.cash_drawer,
            ),
            metadata={
                "source": "network_config",
                "host": config.host,
                "port": config.port,
                "encoding": config.encoding,
            },
        )

    def _require_config(self, printer_name: str) -> NetworkPrinterConfig:
        normalized = printer_name.casefold()
        for config in self.configured_printers:
            if config.name.casefold() == normalized:
                return config
        raise PrinterServiceError(
            "PRINTER_NOT_FOUND", f"Printer {printer_name!r} is not configured."
        )

    def _send(self, printer_name: str, payload: bytes) -> int:
        config = self._require_config(printer_name)
        try:
            with self.socket_factory(
                (config.host, config.port), timeout=self.connect_timeout_seconds
            ) as connection:
                connection.sendall(payload)
        except Exception as exc:
            raise PrinterServiceError(
                "PRINT_FAILED",
                f"Unable to send data to network printer {config.name!r} at {config.host}:{config.port}.",
            ) from exc
        return len(payload)

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
