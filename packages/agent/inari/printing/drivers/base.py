from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ...drivers.base import DeviceDriver
from ..protocols.types import (
    PrintJobResult,
    PrinterDevice,
    PrinterTransport,
    RenderedDocument,
)


@runtime_checkable
class PrinterDriver(DeviceDriver, Protocol):
    def list_devices(self) -> Sequence[PrinterDevice]:
        """Return the printers exposed by this driver."""

    def get_device(self, printer_name: str) -> PrinterDevice:
        """Return a printer descriptor for a known printer name."""

    def get_default_device_name(self) -> str | None:
        """Return the system default printer name, if one is configured."""

    def resolve_transport(
        self,
        printer: PrinterDevice,
        requested: PrinterTransport,
    ) -> PrinterTransport:
        """Resolve AUTO into a concrete transport and validate explicit requests."""

    def submit_raw_job(
        self,
        printer: PrinterDevice,
        payload: bytes,
        *,
        document_name: str,
    ) -> PrintJobResult:
        """Send printer-native bytes."""

    def submit_text_job(
        self,
        printer: PrinterDevice,
        text: str,
        *,
        document_name: str,
    ) -> PrintJobResult:
        """Send plain text through the platform print processor."""

    def submit_document_job(
        self,
        printer: PrinterDevice,
        document: RenderedDocument,
    ) -> PrintJobResult:
        """Send a pre-rendered print document."""

    def open_cash_drawer(self, printer: PrinterDevice) -> PrintJobResult:
        """Pulse the cash drawer for printers that support RAW control commands."""
