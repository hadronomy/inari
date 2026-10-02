from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Mapping

from pydantic import Field

from .base import APIModel
from .events import RuntimeEventResponse
from ...drivers import DeviceKind
from ...printing.commands import (
    CutPaper as CutPaperDomain,
    FeedDots as FeedDotsDomain,
    FeedLines as FeedLinesDomain,
    OpenCashDrawer as OpenCashDrawerDomain,
    PrintTestPage as PrintTestPageDomain,
)
from ...printing.protocols import CutMode, PrinterTransport
from ...runtime.models import JobAttemptRecord, JobKind, JobRecord, JobState
from ...runtime.jobs.operations import (
    DeviceTargetRef,
    QueuedDeviceCommandOperation,
)


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


class DeviceTargetInput(APIModel):
    device_id: str = Field(min_length=1, max_length=256)

    def to_domain(self) -> DeviceTargetRef:
        return DeviceTargetRef(device_id=self.device_id)


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


class OpenCashDrawerCommandInput(APIModel):
    kind: Literal["open_cash_drawer"] = "open_cash_drawer"

    def to_domain(self) -> OpenCashDrawerDomain:
        return OpenCashDrawerDomain()


class PrintTestPageCommandInput(APIModel):
    kind: Literal["print_test_page"] = "print_test_page"

    def to_domain(self) -> PrintTestPageDomain:
        return PrintTestPageDomain()


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
    target: DeviceTargetInput
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
    driver_key: str
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
                device_id=str(target["device_id"])
                if target.get("device_id") is not None
                else None,
                printer_name=str(target.get("printer_name", "")),
                driver_key=str(target.get("driver_key") or target.get("driver") or ""),
                is_default=bool(target.get("is_default", False)),
            ),
            transport=PrinterTransport(
                str(payload.get("transport", PrinterTransport.AUTO.value))
            ),
            bytes_written=int(payload.get("bytes_written", 0)),
            device_job_id=int(payload["device_job_id"])
            if payload.get("device_job_id") is not None
            else None,
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
            error = JobErrorResponse(
                code=job.last_error_code, detail=job.last_error_detail
            )
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
            error = JobErrorResponse(
                code=attempt.error_code, detail=attempt.error_detail
            )
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
