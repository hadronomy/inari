from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .binary_payloads import coerce_image_payload, coerce_pdf_payload, coerce_raw_payload
from .print_jobs import (
    HtmlDocumentContent,
    PdfDocumentContent,
    PrintContentKind,
    PrintJob,
    RawDocumentContent,
    ReceiptImageContent,
    StructuredReceiptContent,
    TextDocumentContent,
)
from .printers import CutMode, PrintJobResult, PrinterDevice, PrinterTransport


class APIModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class PrinterCommandKind(StrEnum):
    OPEN_CASH_DRAWER = "open_cash_drawer"
    PRINT_TEST_PAGE = "print_test_page"
    FEED_LINES = "feed_lines"
    FEED_DOTS = "feed_dots"
    CUT_PAPER = "cut_paper"


class ErrorSourceResponse(APIModel):
    pointer: str | None = None
    parameter: str | None = None
    header: str | None = None


class ErrorItemResponse(APIModel):
    code: str
    detail: str
    source: ErrorSourceResponse | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(APIModel):
    ok: Literal[False] = False
    type: str
    title: str
    status: int
    code: str
    detail: str
    source: ErrorSourceResponse | None = None
    details: dict[str, Any] | None = None
    errors: list[ErrorItemResponse] = Field(default_factory=list)


class ServiceDescriptorResponse(APIModel):
    name: str
    version: str


class PrinterCapabilitiesResponse(APIModel):
    raw: bool
    text: bool
    documents: bool
    cash_drawer: bool


class PrinterResponse(APIModel):
    name: str
    driver: str
    is_default: bool = False
    preferred_transport: PrinterTransport
    capabilities: PrinterCapabilitiesResponse

    @classmethod
    def from_domain(cls, printer: PrinterDevice) -> PrinterResponse:
        return cls(
            name=printer.name,
            driver=printer.driver_key,
            is_default=printer.is_default,
            preferred_transport=printer.preferred_transport,
            capabilities=PrinterCapabilitiesResponse(
                raw=printer.supports_raw,
                text=printer.supports_text,
                documents=printer.supports_documents,
                cash_drawer=printer.supports_cash_drawer,
            ),
        )


class PrinterDirectorySummaryResponse(APIModel):
    count: int
    default_printer_name: str | None = None
    raw_capable_count: int
    document_capable_count: int
    drawer_capable_count: int

    @classmethod
    def from_printers(cls, printers: list[PrinterDevice]) -> PrinterDirectorySummaryResponse:
        return cls(
            count=len(printers),
            default_printer_name=next((printer.name for printer in printers if printer.is_default), None),
            raw_capable_count=sum(1 for printer in printers if printer.supports_raw),
            document_capable_count=sum(1 for printer in printers if printer.supports_documents),
            drawer_capable_count=sum(1 for printer in printers if printer.supports_cash_drawer),
        )


class PrinterDirectoryResponse(APIModel):
    ok: Literal[True] = True
    printers: list[PrinterResponse]
    summary: PrinterDirectorySummaryResponse


class PrinterResourceResponse(APIModel):
    ok: Literal[True] = True
    printer: PrinterResponse


class SystemStatusResponse(APIModel):
    ok: Literal[True] = True
    status: Literal["healthy"] = "healthy"
    service: ServiceDescriptorResponse
    printers: PrinterDirectorySummaryResponse
    supported_content_kinds: tuple[PrintContentKind, ...]
    supported_printer_commands: tuple[PrinterCommandKind, ...]


class PrinterTargetInput(APIModel):
    printer_name: str | None = None


class PrinterTargetResponse(APIModel):
    printer_name: str
    driver: str
    is_default: bool

    @classmethod
    def from_domain(cls, printer: PrinterDevice) -> PrinterTargetResponse:
        return cls(
            printer_name=printer.name,
            driver=printer.driver_key,
            is_default=printer.is_default,
        )


class PrintExecutionOptionsInput(APIModel):
    transport: PrinterTransport = PrinterTransport.AUTO
    open_cash_drawer: bool = False


class BinaryContentInput(APIModel):
    base64: str
    declared_mime_type: str | None = None


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
    binary: BinaryContentInput
    document_name: str = "Receipt"

    def to_domain(self) -> ReceiptImageContent:
        return ReceiptImageContent(
            binary_payload=coerce_image_payload(
                self.binary.base64,
                label="receipt image",
                declared_mime_type=self.binary.declared_mime_type,
            ),
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
    document_name: str = "HTML Document"

    def to_domain(self) -> HtmlDocumentContent:
        return HtmlDocumentContent(
            html=self.html,
            document_name=self.document_name,
        )


class PdfDocumentContentInput(APIModel):
    kind: Literal["pdf"] = "pdf"
    binary: BinaryContentInput
    document_name: str = "PDF Document"

    def to_domain(self) -> PdfDocumentContent:
        return PdfDocumentContent(
            binary_payload=coerce_pdf_payload(
                self.binary.base64,
                label="PDF document",
                declared_mime_type=self.binary.declared_mime_type,
            ),
            document_name=self.document_name,
        )


class RawDocumentContentInput(APIModel):
    kind: Literal["raw"] = "raw"
    binary: BinaryContentInput
    data_type: str = "RAW"
    document_name: str = "Raw Document"

    def to_domain(self) -> RawDocumentContent:
        return RawDocumentContent(
            binary_payload=coerce_raw_payload(
                self.binary.base64,
                label="raw document",
                declared_mime_type=self.binary.declared_mime_type,
            ),
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
    target: PrinterTargetInput = Field(default_factory=PrinterTargetInput)
    options: PrintExecutionOptionsInput = Field(default_factory=PrintExecutionOptionsInput)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> PrintJob:
        return PrintJob(
            content=self.content.to_domain(),
            printer_name=self.target.printer_name,
            transport=self.options.transport,
            open_drawer=self.options.open_cash_drawer,
            metadata=self.metadata,
        )


class OpenCashDrawerCommandInput(APIModel):
    kind: Literal["open_cash_drawer"] = "open_cash_drawer"


class PrintTestPageCommandInput(APIModel):
    kind: Literal["print_test_page"] = "print_test_page"
    transport: PrinterTransport = PrinterTransport.AUTO


class FeedLinesCommandInput(APIModel):
    kind: Literal["feed_lines"] = "feed_lines"
    count: int = Field(gt=0, le=24)


class FeedDotsCommandInput(APIModel):
    kind: Literal["feed_dots"] = "feed_dots"
    count: int = Field(gt=0, le=255)


class CutPaperCommandInput(APIModel):
    kind: Literal["cut_paper"] = "cut_paper"
    mode: CutMode = CutMode.PARTIAL


PrinterCommandInput = Annotated[
    OpenCashDrawerCommandInput
    | PrintTestPageCommandInput
    | FeedLinesCommandInput
    | FeedDotsCommandInput
    | CutPaperCommandInput,
    Field(discriminator="kind"),
]


class PrinterCommandRequest(APIModel):
    target: PrinterTargetInput = Field(default_factory=PrinterTargetInput)
    command: PrinterCommandInput
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperationResultResponse(APIModel):
    printer: PrinterTargetResponse
    transport: PrinterTransport
    bytes_written: int
    job_id: int | None = None

    @classmethod
    def from_domain(cls, result: PrintJobResult) -> OperationResultResponse:
        return cls(
            printer=PrinterTargetResponse.from_domain(result.printer),
            transport=result.transport,
            bytes_written=result.bytes_written,
            job_id=result.job_id,
        )


class OperationResponse(APIModel):
    ok: Literal[True] = True
    operation: str
    result: OperationResultResponse
    message: str | None = None

    @classmethod
    def from_domain(
        cls,
        *,
        operation: str,
        result: PrintJobResult,
        message: str | None = None,
    ) -> OperationResponse:
        return cls(
            operation=operation,
            result=OperationResultResponse.from_domain(result),
            message=message,
        )
