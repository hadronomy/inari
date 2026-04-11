from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .binary_payloads import coerce_image_payload, coerce_pdf_payload, coerce_raw_payload
from .device_commands import (
    CutPaper as CutPaperDomain,
    DeviceCommandKind,
    FeedDots as FeedDotsDomain,
    FeedLines as FeedLinesDomain,
    OpenCashDrawer as OpenCashDrawerDomain,
    PrintTestPage as PrintTestPageDomain,
)
from .drivers import DeviceKind
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
from .printers import CutMode, PrinterTransport
from .runtime.operations import DeviceTargetRef, QueuedDeviceCommandOperation, QueuedPrintOperation
from .runtime.models import (
    DeviceConnectionState,
    DeviceRecord,
    JobAttemptRecord,
    JobKind,
    JobRecord,
    JobState,
    RuntimeEvent,
)


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


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


class QueueSummaryResponse(APIModel):
    total: int
    queued: int = 0
    dispatched: int = 0
    running: int = 0
    retry_scheduled: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0

    @classmethod
    def from_counts(cls, counts: dict[str, int]) -> QueueSummaryResponse:
        payload = {key: int(value) for key, value in counts.items()}
        payload["total"] = sum(payload.values())
        return cls(**payload)


class DeviceDirectorySummaryResponse(APIModel):
    count: int
    online_count: int
    offline_count: int
    kind_counts: dict[str, int]
    default_device_id: str | None = None

    @classmethod
    def from_devices(cls, devices: list[DeviceRecord]) -> DeviceDirectorySummaryResponse:
        kind_counts: dict[str, int] = {}
        for device in devices:
            kind_counts[device.kind.value] = kind_counts.get(device.kind.value, 0) + 1
        default_device = next((device for device in devices if device.is_default), None)
        return cls(
            count=len(devices),
            online_count=sum(1 for device in devices if device.connection_state is DeviceConnectionState.ONLINE),
            offline_count=sum(1 for device in devices if device.connection_state is DeviceConnectionState.OFFLINE),
            kind_counts=kind_counts,
            default_device_id=default_device.id if default_device is not None else None,
        )


class PrinterCapabilitiesResponse(APIModel):
    raw: bool
    text: bool
    documents: bool
    cash_drawer: bool


class PrinterDetailsResponse(APIModel):
    is_default: bool
    preferred_transport: PrinterTransport | None = None
    capabilities: PrinterCapabilitiesResponse


class DeviceResponse(APIModel):
    id: str
    kind: DeviceKind
    name: str
    driver: str
    connection_state: DeviceConnectionState
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime
    printer: PrinterDetailsResponse | None = None

    @classmethod
    def from_domain(cls, device: DeviceRecord) -> DeviceResponse:
        printer_details: PrinterDetailsResponse | None = None
        if device.kind is DeviceKind.PRINTER:
            printer_details = PrinterDetailsResponse(
                is_default=device.is_default,
                preferred_transport=device.preferred_transport,
                capabilities=PrinterCapabilitiesResponse(
                    raw=bool(device.capabilities.get("raw", False)),
                    text=bool(device.capabilities.get("text", False)),
                    documents=bool(device.capabilities.get("documents", False)),
                    cash_drawer=bool(device.capabilities.get("cash_drawer", False)),
                ),
            )
        return cls(
            id=device.id,
            kind=device.kind,
            name=device.name,
            driver=device.driver_key,
            connection_state=device.connection_state,
            first_seen_at=device.first_seen_at,
            last_seen_at=device.last_seen_at,
            updated_at=device.updated_at,
            printer=printer_details,
        )


class DeviceDirectoryResponse(APIModel):
    ok: Literal[True] = True
    devices: list[DeviceResponse]
    summary: DeviceDirectorySummaryResponse


class DeviceResourceResponse(APIModel):
    ok: Literal[True] = True
    device: DeviceResponse


class RuntimeEventResponse(APIModel):
    sequence: int
    resource_kind: str
    resource_id: str
    event_type: str
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, event: RuntimeEvent) -> RuntimeEventResponse:
        return cls(
            sequence=event.sequence,
            resource_kind=event.resource_kind,
            resource_id=event.resource_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            payload=dict(event.payload),
        )


class DeviceEventCollectionResponse(APIModel):
    ok: Literal[True] = True
    events: list[RuntimeEventResponse]


class SystemStatusResponse(APIModel):
    ok: Literal[True] = True
    status: Literal["healthy"] = "healthy"
    service: ServiceDescriptorResponse
    devices: DeviceDirectorySummaryResponse
    queue: QueueSummaryResponse
    supported_content_kinds: tuple[PrintContentKind, ...]
    supported_device_commands: tuple[DeviceCommandKind, ...]


class DeviceTargetInput(APIModel):
    device_id: str | None = None
    printer_name: str | None = None

    def to_domain(self) -> DeviceTargetRef:
        return DeviceTargetRef(
            device_id=self.device_id,
            printer_name=self.printer_name,
        )


class JobTargetResponse(APIModel):
    device_id: str
    device_kind: DeviceKind
    device_name: str

    @classmethod
    def from_job(cls, job: JobRecord) -> JobTargetResponse:
        return cls(
            device_id=job.device_id,
            device_kind=job.device_kind,
            device_name=job.device_name,
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
        return StructuredReceiptContent(payload=self.data, document_name=self.document_name)


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
        return TextDocumentContent(text=self.text, document_name=self.document_name)


class HtmlDocumentContentInput(APIModel):
    kind: Literal["html"] = "html"
    html: str
    document_name: str = "HTML Document"

    def to_domain(self) -> HtmlDocumentContent:
        return HtmlDocumentContent(html=self.html, document_name=self.document_name)


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
    target: DeviceTargetInput = Field(default_factory=DeviceTargetInput)
    options: PrintExecutionOptionsInput = Field(default_factory=PrintExecutionOptionsInput)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_operation(self) -> QueuedPrintOperation:
        return QueuedPrintOperation(
            target=self.target.to_domain(),
            job=PrintJob(
                content=self.content.to_domain(),
                printer_name=self.target.printer_name,
                transport=self.options.transport,
                open_drawer=self.options.open_cash_drawer,
                metadata=self.metadata,
            ),
        )


class OpenCashDrawerCommandInput(APIModel):
    kind: Literal["open_cash_drawer"] = "open_cash_drawer"

    def to_domain(self) -> OpenCashDrawerDomain:
        return OpenCashDrawerDomain()


class PrintTestPageCommandInput(APIModel):
    kind: Literal["print_test_page"] = "print_test_page"
    transport: PrinterTransport = PrinterTransport.AUTO

    def to_domain(self) -> PrintTestPageDomain:
        return PrintTestPageDomain(transport=self.transport)


class FeedLinesCommandInput(APIModel):
    kind: Literal["feed_lines"] = "feed_lines"
    count: int = Field(gt=0, le=24)

    def to_domain(self) -> FeedLinesDomain:
        return FeedLinesDomain(count=self.count)


class FeedDotsCommandInput(APIModel):
    kind: Literal["feed_dots"] = "feed_dots"
    count: int = Field(gt=0, le=255)

    def to_domain(self) -> FeedDotsDomain:
        return FeedDotsDomain(count=self.count)


class CutPaperCommandInput(APIModel):
    kind: Literal["cut_paper"] = "cut_paper"
    mode: CutMode = CutMode.PARTIAL

    def to_domain(self) -> CutPaperDomain:
        return CutPaperDomain(mode=self.mode)


DeviceCommandInput = Annotated[
    OpenCashDrawerCommandInput
    | PrintTestPageCommandInput
    | FeedLinesCommandInput
    | FeedDotsCommandInput
    | CutPaperCommandInput,
    Field(discriminator="kind"),
]


class DeviceCommandRequest(APIModel):
    target: DeviceTargetInput = Field(default_factory=DeviceTargetInput)
    command: DeviceCommandInput
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_operation(self) -> QueuedDeviceCommandOperation:
        return QueuedDeviceCommandOperation(
            target=self.target.to_domain(),
            command=self.command.to_domain(),
            metadata=self.metadata,
        )


class JobExecutionTargetResponse(APIModel):
    device_id: str | None = None
    printer_name: str
    driver: str
    is_default: bool


class JobExecutionResultResponse(APIModel):
    target: JobExecutionTargetResponse
    transport: PrinterTransport
    bytes_written: int
    device_job_id: int | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> JobExecutionResultResponse:
        target = payload.get("printer", {})
        if not isinstance(target, Mapping):
            target = {}
        return cls(
            target=JobExecutionTargetResponse(
                device_id=str(target["device_id"]) if target.get("device_id") is not None else None,
                printer_name=str(target.get("printer_name", "")),
                driver=str(target.get("driver", "")),
                is_default=bool(target.get("is_default", False)),
            ),
            transport=PrinterTransport(str(payload.get("transport", PrinterTransport.AUTO.value))),
            bytes_written=int(payload.get("bytes_written", 0)),
            device_job_id=int(payload["device_job_id"]) if payload.get("device_job_id") is not None else None,
        )


class JobErrorResponse(APIModel):
    code: str
    detail: str


class JobResponse(APIModel):
    id: str
    kind: JobKind
    operation: str
    state: JobState
    target: JobTargetResponse
    content_kind: str | None = None
    command_kind: str | None = None
    attempt_count: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    queued_at: datetime
    next_run_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: JobExecutionResultResponse | None = None
    error: JobErrorResponse | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, job: JobRecord) -> JobResponse:
        result = (
            JobExecutionResultResponse.from_payload(job.result_payload)
            if job.result_payload is not None
            else None
        )
        error = None
        if job.last_error_code is not None and job.last_error_detail is not None:
            error = JobErrorResponse(code=job.last_error_code, detail=job.last_error_detail)
        return cls(
            id=job.id,
            kind=job.kind,
            operation=job.operation,
            state=job.state,
            target=JobTargetResponse.from_job(job),
            content_kind=job.content_kind,
            command_kind=job.command_kind,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            created_at=job.created_at,
            updated_at=job.updated_at,
            queued_at=job.queued_at,
            next_run_at=job.next_run_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            result=result,
            error=error,
            metadata=dict(job.request_metadata),
        )


class JobResourceResponse(APIModel):
    ok: Literal[True] = True
    job: JobResponse


class JobCollectionResponse(APIModel):
    ok: Literal[True] = True
    jobs: list[JobResponse]
    queue: QueueSummaryResponse


class JobAttemptResponse(APIModel):
    id: int
    attempt_number: int
    state: JobState
    started_at: datetime
    finished_at: datetime | None = None
    error: JobErrorResponse | None = None

    @classmethod
    def from_domain(cls, attempt: JobAttemptRecord) -> JobAttemptResponse:
        error = None
        if attempt.error_code is not None and attempt.error_detail is not None:
            error = JobErrorResponse(code=attempt.error_code, detail=attempt.error_detail)
        return cls(
            id=attempt.id,
            attempt_number=attempt.attempt_number,
            state=attempt.state,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            error=error,
        )


class JobHistoryResponse(APIModel):
    ok: Literal[True] = True
    job: JobResponse
    attempts: list[JobAttemptResponse]
    events: list[RuntimeEventResponse]
