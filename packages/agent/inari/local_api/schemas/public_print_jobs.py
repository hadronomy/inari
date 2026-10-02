from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from ...print_jobs import (
    OutputEvidence,
    PosPrintOrigin,
    PreparationPrintOrigin,
    PrintJob,
    PrintJobState,
    ReportPrintOrigin,
)
from .base import APIModel


PrintIntentId = Annotated[
    str,
    Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
]


class PrintJobQueryRequest(APIModel):
    print_intent_ids: list[PrintIntentId] = Field(min_length=1, max_length=100)


class PosPrintOriginResponse(APIModel):
    kind: Literal["pos"] = "pos"
    organization_id: str
    site_id: str
    database: str
    paired_client_id: str
    pos_configuration_id: str
    pos_session_id: str
    offline_order_id: str
    server_order_id: str | None
    document_kind: str
    content_revision: str

    @classmethod
    def from_domain(cls, origin: PosPrintOrigin) -> PosPrintOriginResponse:
        return cls(
            organization_id=origin.organization_id,
            site_id=origin.site_id,
            database=origin.database,
            paired_client_id=origin.paired_client_id,
            pos_configuration_id=origin.pos_configuration_id,
            pos_session_id=origin.pos_session_id,
            offline_order_id=origin.offline_order_id,
            server_order_id=origin.server_order_id,
            document_kind=origin.document_kind,
            content_revision=origin.content_revision,
        )


class PreparationPrintOriginResponse(APIModel):
    kind: Literal["preparation"] = "preparation"
    organization_id: str
    site_id: str
    database: str
    paired_client_id: str
    pos_configuration_id: str
    pos_session_id: str
    offline_order_id: str
    server_order_id: str | None
    document_kind: str
    content_revision: str
    segment_kind: str
    segment_index: int
    preparation_revision: str

    @classmethod
    def from_domain(
        cls, origin: PreparationPrintOrigin
    ) -> PreparationPrintOriginResponse:
        return cls(
            organization_id=origin.organization_id,
            site_id=origin.site_id,
            database=origin.database,
            paired_client_id=origin.paired_client_id,
            pos_configuration_id=origin.pos_configuration_id,
            pos_session_id=origin.pos_session_id,
            offline_order_id=origin.offline_order_id,
            server_order_id=origin.server_order_id,
            document_kind=origin.document_kind,
            content_revision=origin.content_revision,
            segment_kind=origin.segment_kind,
            segment_index=origin.segment_index,
            preparation_revision=origin.preparation_revision,
        )


class ReportPrintOriginResponse(APIModel):
    kind: Literal["report"] = "report"
    organization_id: str
    site_id: str
    database: str
    company_id: str
    report_binding_id: str
    report_route: str
    report_action: str
    source_model: str
    record_ids: tuple[str, ...]
    wizard_input_digest: str | None
    rendered_document_index: int

    @classmethod
    def from_domain(cls, origin: ReportPrintOrigin) -> ReportPrintOriginResponse:
        return cls(
            organization_id=origin.organization_id,
            site_id=origin.site_id,
            database=origin.database,
            company_id=origin.company_id,
            report_binding_id=origin.report_binding_id,
            report_route=origin.report_route,
            report_action=origin.report_action,
            source_model=origin.source_model,
            record_ids=origin.record_ids,
            wizard_input_digest=origin.wizard_input_digest,
            rendered_document_index=origin.rendered_document_index,
        )


PrintOriginResponse = Annotated[
    PosPrintOriginResponse | PreparationPrintOriginResponse | ReportPrintOriginResponse,
    Field(discriminator="kind"),
]

PrintJobStateResponse = Literal[
    "accepted",
    "in_progress",
    "output_confirmed",
    "failed",
    "outcome_unknown",
    "expired",
    "canceled",
]
OutputEvidenceResponse = Literal["device", "spooler", "transport"]
_STATE_RESPONSES: dict[PrintJobState, PrintJobStateResponse] = {
    PrintJobState.ACCEPTED: "accepted",
    PrintJobState.IN_PROGRESS: "in_progress",
    PrintJobState.OUTPUT_CONFIRMED: "output_confirmed",
    PrintJobState.FAILED: "failed",
    PrintJobState.OUTCOME_UNKNOWN: "outcome_unknown",
    PrintJobState.EXPIRED: "expired",
    PrintJobState.CANCELED: "canceled",
}
_EVIDENCE_RESPONSES: dict[OutputEvidence, OutputEvidenceResponse] = {
    OutputEvidence.DEVICE: "device",
    OutputEvidence.SPOOLER: "spooler",
    OutputEvidence.TRANSPORT: "transport",
}


class PublicPrintJobResponse(APIModel):
    print_job_id: str
    print_intent_id: str
    device_id: str
    origin: PrintOriginResponse
    state: PrintJobStateResponse
    state_version: int
    accepted_at: datetime
    started_at: datetime | None
    terminal_at: datetime | None
    expires_at: datetime
    retryable: bool
    error_code: str | None
    message_key: str | None
    confirmation_evidence: OutputEvidenceResponse | None
    managed_work_id: str | None
    contract_version: str

    @classmethod
    def from_domain(cls, job: PrintJob) -> PublicPrintJobResponse:
        origin: PrintOriginResponse
        if isinstance(job.origin, PreparationPrintOrigin):
            origin = PreparationPrintOriginResponse.from_domain(job.origin)
        elif isinstance(job.origin, PosPrintOrigin):
            origin = PosPrintOriginResponse.from_domain(job.origin)
        else:
            origin = ReportPrintOriginResponse.from_domain(job.origin)
        return cls(
            print_job_id=job.job_id,
            print_intent_id=job.intent_id,
            device_id=job.device_id,
            origin=origin,
            state=_state_response(job.state),
            state_version=job.state_version,
            accepted_at=job.accepted_at,
            started_at=job.started_at,
            terminal_at=job.terminal_at,
            expires_at=job.expires_at,
            retryable=job.retryable,
            error_code=job.error_code,
            message_key=job.message_key,
            confirmation_evidence=_evidence_response(job.confirmation_evidence),
            managed_work_id=job.managed_work_id,
            contract_version=job.contract_version,
        )


class PrintJobQueryResponse(APIModel):
    jobs: list[PublicPrintJobResponse]
    missing_print_intent_ids: list[str]
    high_water_mark: int


def _state_response(state: PrintJobState) -> PrintJobStateResponse:
    return _STATE_RESPONSES[state]


def _evidence_response(
    evidence: OutputEvidence | str | None,
) -> OutputEvidenceResponse | None:
    if evidence is None:
        return None
    return _EVIDENCE_RESPONSES[OutputEvidence(evidence)]


__all__ = [
    "PrintJobQueryRequest",
    "PrintJobQueryResponse",
    "PublicPrintJobResponse",
]
