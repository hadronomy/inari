from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Mapping, TypeAlias

from .binary_payloads import BinaryPayload
from .printers import PrinterTransport


class PrintContentKind(StrEnum):
    STRUCTURED_RECEIPT = "structured_receipt"
    RECEIPT_IMAGE = "receipt_image"
    TEXT = "text"
    HTML = "html"
    PDF = "pdf"
    RAW = "raw"


@dataclass(slots=True, frozen=True)
class StructuredReceiptContent:
    payload: Mapping[str, Any]
    document_name: str = "Receipt"
    kind: ClassVar[PrintContentKind] = PrintContentKind.STRUCTURED_RECEIPT


@dataclass(slots=True, frozen=True)
class ReceiptImageContent:
    binary_payload: BinaryPayload
    document_name: str = "Receipt"
    kind: ClassVar[PrintContentKind] = PrintContentKind.RECEIPT_IMAGE

    @property
    def image_bytes(self) -> bytes:
        return self.binary_payload.content

    @property
    def mime_type(self) -> str | None:
        return self.binary_payload.mime_type


@dataclass(slots=True, frozen=True)
class TextDocumentContent:
    text: str
    document_name: str = "Text Document"
    kind: ClassVar[PrintContentKind] = PrintContentKind.TEXT


@dataclass(slots=True, frozen=True)
class HtmlDocumentContent:
    html: str
    document_name: str = "HTML Document"
    kind: ClassVar[PrintContentKind] = PrintContentKind.HTML


@dataclass(slots=True, frozen=True)
class PdfDocumentContent:
    binary_payload: BinaryPayload
    document_name: str = "PDF Document"
    kind: ClassVar[PrintContentKind] = PrintContentKind.PDF

    @property
    def pdf_bytes(self) -> bytes:
        return self.binary_payload.content

    @property
    def mime_type(self) -> str | None:
        return self.binary_payload.mime_type


@dataclass(slots=True, frozen=True)
class RawDocumentContent:
    binary_payload: BinaryPayload
    data_type: str = "RAW"
    document_name: str = "Raw Document"
    kind: ClassVar[PrintContentKind] = PrintContentKind.RAW

    @property
    def payload(self) -> bytes:
        return self.binary_payload.content

    @property
    def mime_type(self) -> str | None:
        return self.binary_payload.mime_type


PrintContent: TypeAlias = (
    StructuredReceiptContent
    | ReceiptImageContent
    | TextDocumentContent
    | HtmlDocumentContent
    | PdfDocumentContent
    | RawDocumentContent
)


@dataclass(slots=True, frozen=True)
class PrintJob:
    content: PrintContent
    printer_name: str | None = None
    transport: PrinterTransport = PrinterTransport.AUTO
    open_drawer: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
