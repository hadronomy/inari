from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .binary_payloads import decode_base64_payload
from .print_jobs import (
    HtmlDocumentContent,
    PdfDocumentContent,
    PrintJob,
    RawDocumentContent,
    ReceiptImageContent,
    StructuredReceiptContent,
    TextDocumentContent,
)
from .printers import PrinterTransport


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorResponse(APIModel):
    ok: Literal[False] = False
    code: str
    message: str


class HealthResponse(APIModel):
    ok: Literal[True] = True
    status: Literal["healthy"] = "healthy"
    default_printer: str | None = None
    printer_count: int
    drawer_supported: bool


class PrinterInfoResponse(APIModel):
    name: str
    driver: str
    is_default: bool = False
    mode: PrinterTransport
    supports_raw: bool
    supports_documents: bool
    supports_cash_drawer: bool


class PrintersResponse(APIModel):
    ok: Literal[True] = True
    printers: list[PrinterInfoResponse]


class ActionResponse(APIModel):
    ok: Literal[True] = True
    printer_name: str | None = None
    driver: str | None = None
    mode: PrinterTransport | None = None
    bytes_written: int | None = None
    detail: str | None = None


class StructuredReceiptContentInput(APIModel):
    kind: Literal["structured_receipt"] = "structured_receipt"
    data: dict[str, Any]
    document_name: str = "Receipt"

    def to_domain(self) -> StructuredReceiptContent:
        return StructuredReceiptContent(
            payload=self.data,
            document_name=self.document_name,
        )


class ReceiptImageContentInput(APIModel):
    kind: Literal["receipt_image"] = "receipt_image"
    data_base64: str
    mime_type: str = "image/jpeg"
    document_name: str = "Receipt"

    def to_domain(self) -> ReceiptImageContent:
        return ReceiptImageContent(
            image_bytes=decode_base64_payload(self.data_base64, label="receipt image"),
            mime_type=self.mime_type,
            document_name=self.document_name,
        )


class TextDocumentContentInput(APIModel):
    kind: Literal["text"] = "text"
    text: str
    document_name: str = "Text Document"

    def to_domain(self) -> TextDocumentContent:
        return TextDocumentContent(
            text=self.text,
            document_name=self.document_name,
        )


class HtmlDocumentContentInput(APIModel):
    kind: Literal["html"] = "html"
    html: str
    title: str = "HTML Document"

    def to_domain(self) -> HtmlDocumentContent:
        return HtmlDocumentContent(
            html=self.html,
            title=self.title,
        )


class PdfDocumentContentInput(APIModel):
    kind: Literal["pdf"] = "pdf"
    data_base64: str
    title: str = "PDF Document"

    def to_domain(self) -> PdfDocumentContent:
        return PdfDocumentContent(
            pdf_bytes=decode_base64_payload(self.data_base64, label="PDF document"),
            title=self.title,
        )


class RawDocumentContentInput(APIModel):
    kind: Literal["raw"] = "raw"
    data_base64: str
    data_type: str = "RAW"
    document_name: str = "Raw Document"

    def to_domain(self) -> RawDocumentContent:
        return RawDocumentContent(
            payload=decode_base64_payload(self.data_base64, label="raw document"),
            data_type=self.data_type,
            document_name=self.document_name,
        )


PrintContentInput = Annotated[
    StructuredReceiptContentInput
    | ReceiptImageContentInput
    | TextDocumentContentInput
    | HtmlDocumentContentInput
    | PdfDocumentContentInput
    | RawDocumentContentInput,
    Field(discriminator="kind"),
]


class PrintJobRequest(APIModel):
    content: PrintContentInput
    printer_name: str | None = None
    open_drawer: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    mode: PrinterTransport = PrinterTransport.AUTO

    def to_domain(self) -> PrintJob:
        return PrintJob(
            content=self.content.to_domain(),
            printer_name=self.printer_name,
            transport=self.mode,
            open_drawer=self.open_drawer,
            metadata=self.metadata,
        )


class PrintReceiptRequest(APIModel):
    source: str = "receipt"
    receipt: dict[str, Any] | str
    printer_name: str | None = None
    open_drawer: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    mode: PrinterTransport = PrinterTransport.AUTO
    mime_type: str = "image/jpeg"

    def to_domain(self) -> PrintJob:
        if isinstance(self.receipt, str):
            content = ReceiptImageContent(
                image_bytes=decode_base64_payload(self.receipt, label="receipt image"),
                mime_type=self.mime_type,
            )
        else:
            content = StructuredReceiptContent(payload=self.receipt)

        return PrintJob(
            content=content,
            printer_name=self.printer_name,
            transport=self.mode,
            open_drawer=self.open_drawer,
            metadata=self.metadata,
        )


class PrintHtmlRequest(APIModel):
    html: str
    printer_name: str | None = None
    open_drawer: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    title: str = "HTML Document"

    def to_domain(self) -> PrintJob:
        return PrintJob(
            content=HtmlDocumentContent(html=self.html, title=self.title),
            printer_name=self.printer_name,
            open_drawer=self.open_drawer,
            metadata=self.metadata,
        )


class DrawerRequest(APIModel):
    printer_name: str | None = None


class TestPrintRequest(APIModel):
    printer_name: str | None = None
    mode: PrinterTransport = PrinterTransport.AUTO
