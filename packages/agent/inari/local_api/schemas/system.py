from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from .base import APIModel
from .devices import DeviceDirectorySummaryResponse
from .events import RuntimeEventResponse
from .jobs import QueueSummaryResponse
from .public_print_jobs import PrintJobStateResponse
from ...print_jobs import OutputEvidence, PrintJob, ReportPrintOrigin
from ...printing.commands import DeviceCommandKind
from ...documents import DocumentKind


class ServiceDescriptorResponse(APIModel):
    name: str
    version: str


class SystemStatusResponse(APIModel):
    ok: Literal[True] = True
    status: Literal["healthy"] = "healthy"
    service: ServiceDescriptorResponse
    devices: DeviceDirectorySummaryResponse
    queue: QueueSummaryResponse
    supported_document_kinds: tuple[DocumentKind, ...]
    supported_device_commands: tuple[DeviceCommandKind, ...]


class NativePrintJobResponse(APIModel):
    """Content-free history for the authenticated Agent Host monitor."""

    print_job_id: str
    print_intent_id: str
    device_id: str
    origin_kind: Literal["pos", "preparation", "report"]
    database: str
    document_kind: str | None
    pos_configuration_id: str | None
    state: PrintJobStateResponse
    state_version: int = Field(gt=0)
    accepted_at: datetime
    started_at: datetime | None
    terminal_at: datetime | None
    confirmation_evidence: OutputEvidence | None

    @classmethod
    def from_domain(cls, job: PrintJob) -> NativePrintJobResponse:
        return cls(
            print_job_id=job.job_id,
            print_intent_id=job.intent_id,
            device_id=job.device_id,
            origin_kind=job.origin.kind.value,
            database=job.origin.database,
            document_kind=(
                None
                if isinstance(job.origin, ReportPrintOrigin)
                else job.origin.document_kind
            ),
            pos_configuration_id=(
                None
                if isinstance(job.origin, ReportPrintOrigin)
                else job.origin.pos_configuration_id
            ),
            state=job.state.value,
            state_version=job.state_version,
            accepted_at=job.accepted_at,
            started_at=job.started_at,
            terminal_at=job.terminal_at,
            confirmation_evidence=(
                None
                if job.confirmation_evidence is None
                else OutputEvidence(job.confirmation_evidence)
            ),
        )


class LiveSnapshotResponse(APIModel):
    kind: Literal["snapshot"] = "snapshot"
    status: SystemStatusResponse
    print_jobs: list[NativePrintJobResponse] = Field(max_length=100)


class LiveEventUpdateResponse(APIModel):
    kind: Literal["event_update"] = "event_update"
    status: SystemStatusResponse
    event: RuntimeEventResponse
    print_jobs: list[NativePrintJobResponse] = Field(max_length=100)


LiveUpdateMessage = Annotated[
    LiveSnapshotResponse | LiveEventUpdateResponse,
    Field(discriminator="kind"),
]
