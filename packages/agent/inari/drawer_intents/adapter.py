from __future__ import annotations

from dataclasses import dataclass

from ..core.exceptions import AgentError, PrinterServiceError
from ..drivers import DeviceKind
from ..printing.protocols import PrintJobResult
from ..printing.service import PrinterService
from ..runtime.devices.service import DeviceCatalog


@dataclass(frozen=True, slots=True)
class PrinterCashDrawerPort:
    """Resolve one catalog Device and delegate the pulse to PrinterService."""

    catalog: DeviceCatalog
    printer_service: PrinterService

    def ensure_ready(self, device_id: str) -> None:
        device = self.catalog.get_device(device_id)
        if device is None:
            raise AgentError(
                "DEVICE_NOT_FOUND",
                "The cash drawer device was not found.",
                status_code=404,
            )
        if device.kind is not DeviceKind.PRINTER:
            raise PrinterServiceError(
                "DRAWER_NOT_SUPPORTED",
                "The selected device is not a receipt printer.",
            )
        printer = self.printer_service.get_printer_info(device.name)
        if not printer.supports_cash_drawer:
            raise PrinterServiceError(
                "DRAWER_NOT_SUPPORTED",
                "The selected receipt printer does not support a cash drawer.",
            )

    def open_cash_drawer(self, device_id: str) -> PrintJobResult:
        device = self.catalog.get_device(device_id)
        if device is None:
            raise AgentError(
                "DEVICE_NOT_FOUND",
                "The cash drawer device was not found.",
                status_code=404,
            )
        return self.printer_service.open_cash_drawer(printer_name=device.name)


__all__ = ["PrinterCashDrawerPort"]
