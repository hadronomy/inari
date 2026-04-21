from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class PrinterTransport(StrEnum):
    AUTO = "auto"
    RAW = "raw"
    TEXT = "text"
    DOCUMENT = "document"


class CutMode(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


@dataclass(slots=True, frozen=True)
class PrinterCapabilities:
    raw: bool = False
    text: bool = True
    documents: bool = True
    cash_drawer: bool = False


@dataclass(slots=True, frozen=True)
class PrinterDevice:
    name: str
    driver_key: str
    is_default: bool = False
    preferred_transport: PrinterTransport = PrinterTransport.DOCUMENT
    capabilities: PrinterCapabilities = field(default_factory=PrinterCapabilities)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def supports_raw(self) -> bool:
        return self.capabilities.raw

    @property
    def supports_text(self) -> bool:
        return self.capabilities.text

    @property
    def supports_documents(self) -> bool:
        return self.capabilities.documents

    @property
    def supports_cash_drawer(self) -> bool:
        return self.capabilities.cash_drawer


@dataclass(slots=True, frozen=True)
class RenderedDocument:
    content: bytes
    data_type: str = "RAW"
    document_name: str = "Document"


@dataclass(slots=True, frozen=True)
class PrintJobResult:
    printer: PrinterDevice
    transport: PrinterTransport
    bytes_written: int
    job_id: int | None = None

    @property
    def printer_name(self) -> str:
        return self.printer.name
