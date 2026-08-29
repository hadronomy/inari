from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, TypeAlias

from ..device_authority import AuthorityProof


class DocumentKind(StrEnum):
    RECEIPT_IMAGE = "receipt_image"
    REPORT_PDF = "report_pdf"
    LABEL_DOCUMENT = "label_document"


@dataclass(frozen=True, slots=True)
class ReceiptImage:
    content: bytes

    kind: ClassVar[DocumentKind] = DocumentKind.RECEIPT_IMAGE


@dataclass(frozen=True, slots=True)
class ReportPdf:
    content: bytes

    kind: ClassVar[DocumentKind] = DocumentKind.REPORT_PDF


@dataclass(frozen=True, slots=True)
class LabelDocument:
    content: bytes

    kind: ClassVar[DocumentKind] = DocumentKind.LABEL_DOCUMENT


Document: TypeAlias = ReceiptImage | ReportPdf | LabelDocument


@dataclass(frozen=True, slots=True)
class PosPrintOrigin:
    database: str
    pos_configuration_id: str
    pos_session_id: str
    offline_order_id: str
    server_order_id: str | None
    document_kind: str
    content_revision: str


@dataclass(frozen=True, slots=True)
class SubmissionContext:
    contract_major: int
    organization_id: str
    site_id: str
    paired_client_id: str
    print_intent_id: str
    origin_submission_key: str
    origin: PosPrintOrigin
    binding_revision_id: str
    device_id: str
    actor_id: str
    authorization_digest: str
    copy_ordinal: int


@dataclass(frozen=True, slots=True)
class DocumentWork:
    idempotency_key: str
    context: SubmissionContext
    document: Document

    @property
    def operation(self) -> DocumentKind:
        return self.document.kind


@dataclass(frozen=True, slots=True)
class AdmissionAccepted:
    print_intent_id: str
    print_job_id: str
    device_id: str
    accepted_at: datetime
    state_version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class AdmissionGrantScope:
    """The exact content-free scope persisted with an admitted work item."""

    organization_id: str
    site_id: str
    database: str
    pos_configuration_id: str
    paired_client_id: str
    actor_id: str
    device_id: str
    binding_revision_id: str
    operation: DocumentKind
    authorization_digest: str


@dataclass(frozen=True, slots=True)
class AdmissionGrant:
    """One fail-closed grant for one exact Device Work scope."""

    organization_id: str
    site_id: str
    database: str
    pos_configuration_id: str
    paired_client_id: str
    actor_id: str
    device_id: str
    binding_revision_id: str
    operation: DocumentKind
    authorization_digest: str

    def scope(self) -> AdmissionGrantScope:
        return AdmissionGrantScope(
            organization_id=self.organization_id,
            site_id=self.site_id,
            database=self.database,
            pos_configuration_id=self.pos_configuration_id,
            paired_client_id=self.paired_client_id,
            actor_id=self.actor_id,
            device_id=self.device_id,
            binding_revision_id=self.binding_revision_id,
            operation=self.operation,
            authorization_digest=self.authorization_digest,
        )


@dataclass(frozen=True, slots=True)
class AdmissionDeadline:
    """The resolved UTC and process-local deadline for one admission."""

    expires_at: datetime
    monotonic_deadline: float

    def is_expired(self, *, utc_now: datetime, monotonic_now: float) -> bool:
        return utc_now >= self.expires_at or monotonic_now >= self.monotonic_deadline


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    """The complete immutable input to the one Document Admission seam."""

    work: DocumentWork
    grant: AdmissionGrant
    media_type: str
    options: Mapping[str, Any]
    trusted_managed_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DurableAdmission:
    """The validated content-bearing value accepted by the durable store."""

    work: DocumentWork
    grant_scope: AdmissionGrantScope
    deadline: AdmissionDeadline
    payload_fingerprint: bytes
    media_type: str
    normalized_options: bytes
    authority_proof: AuthorityProof


class DocumentAdmissionError(ValueError):
    """A safe, typed rejection from document policy before durable admission."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details) if details else None
