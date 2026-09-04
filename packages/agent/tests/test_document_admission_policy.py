from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from PIL import Image

from inari.device_authority import AuthorityError, AuthorityErrorCode
from inari.documents import (
    AdmissionAccepted,
    AdmissionGrant,
    AdmissionRequest,
    DocumentAdmissionError,
    DocumentAdmissionService,
    DocumentKind,
    DocumentWork,
    DurableAdmission,
    LabelDocument,
    ManagedAdmissionAuthorization,
    ManagedAdmissionScope,
    ManagedSubmissionContext,
    PosPrintOrigin,
    ReceiptImage,
    RecordsReportSource,
    ReportBinding,
    ReportPrintOrigin,
    ReportPdf,
    SubmissionContext,
)
from tests.support.device_authority import StaticAdmissionAuthority
from inari.device_authority import ScopeKind

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def jpeg(*, width: int = 2, height: int = 2) -> bytes:
    image = Image.new("L", (width, height), color=255)
    output = BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


@dataclass(slots=True)
class RecordingStore:
    admission: DurableAdmission | None = None

    async def accept(self, admission: DurableAdmission) -> AdmissionAccepted:
        self.admission = admission
        return AdmissionAccepted(
            print_intent_id=admission.work.context.print_intent_id,
            print_job_id="job_01",
            device_id=admission.work.context.device_id,
            accepted_at=NOW,
            state_version=1,
            replayed=False,
        )


def receipt_work(
    document: ReceiptImage | ReportPdf | LabelDocument | None = None,
    **context_changes: object,
) -> DocumentWork:
    origin = PosPrintOrigin(
        database="odoo",
        pos_configuration_id="pos_config_7",
        pos_session_id="pos_session_42",
        offline_order_id="01991a84-d0c2-7a49-89ad-2fd14bdbe501",
        server_order_id=None,
        document_kind="customer_receipt",
        content_revision="sha256:receipt-revision",
    )
    values: dict[str, object] = {
        "contract_major": 1,
        "organization_id": "org_1",
        "site_id": "site_1",
        "paired_client_id": "client_1",
        "print_intent_id": "pi_v1_test",
        "origin_submission_key": "osk_v1_test",
        "origin": origin,
        "binding_revision_id": "binding_revision_9",
        "device_id": "dev_receipt_1",
        "actor_id": "res.users:7",
        "authorization_digest": "sha256:authorization",
        "copy_ordinal": 1,
    }
    values.update(context_changes)
    return DocumentWork(
        idempotency_key="pi_v1_test",
        context=SubmissionContext(**values),
        document=document if document is not None else ReceiptImage(content=jpeg()),
    )


def grant(**changes: object) -> AdmissionGrant:
    values: dict[str, object] = {
        "grant_id": "grant_1",
        "pairing_id": "pairing_1",
        "generation": 1,
        "organization_id": "org_1",
        "site_id": "site_1",
        "database": "odoo",
        "pos_configuration_id": "pos_config_7",
        "paired_client_id": "client_1",
        "actor_id": "res.users:7",
        "device_id": "dev_receipt_1",
        "binding_revision_id": "binding_revision_9",
        "operation": DocumentKind.RECEIPT_IMAGE,
        "authorization_digest": "sha256:authorization",
    }
    values.update(changes)
    return AdmissionGrant(**values)


def managed_work(document: ReportPdf | LabelDocument) -> DocumentWork:
    binding = ReportBinding(
        report_binding_id="report-binding-1",
        binding_revision_id="binding_revision_9",
        report_action_id="stock.action_report_delivery",
        report_contract_digest="contract-digest",
        template_digest="template-digest",
        command_profile_id=None,
        layout_profile_id=None,
        hardware_matrix_digest=None,
    )
    context = ManagedSubmissionContext(
        contract_major=1,
        database="odoo",
        company_id="7",
        organization_id="org_1",
        site_id="site_1",
        managed_work_id="mw_1",
        print_intent_id="pi_report_1",
        origin_submission_key="report:origin-1",
        origin=ReportPrintOrigin(
            binding=binding,
            route="manual",
            source=RecordsReportSource(
                model="stock.picking",
                ordered_ids=(17,),
            ),
            rendered_document_index=0,
            copy_ordinal=1,
        ),
        binding_revision_id=binding.binding_revision_id,
        device_id="dev_receipt_1",
        actor_id="controller:primary",
        authorization_digest="dispatch-authorization",
        copy_ordinal=1,
    )
    return DocumentWork(
        idempotency_key="mw_1",
        context=context,
        document=document,
    )


def managed_authorization(operation: DocumentKind) -> ManagedAdmissionAuthorization:
    return ManagedAdmissionAuthorization(
        managed_work_id="mw_1",
        organization_id="org_1",
        site_id="site_1",
        database="odoo",
        actor_id="controller:primary",
        device_id="dev_receipt_1",
        binding_revision_id="binding_revision_9",
        operation=operation,
        authorization_digest="dispatch-authorization",
    )


def request(
    work: DocumentWork | None = None,
    *,
    admission_grant: AdmissionGrant | None = None,
    managed_expires_at: datetime | None = None,
    media_type: str = "image/jpeg",
    options: dict[str, object] | None = None,
) -> AdmissionRequest:
    return AdmissionRequest(
        work=work or receipt_work(),
        authorization=admission_grant or grant(),
        media_type=media_type,
        options=options if options is not None else {"density": 203, "cut": True},
        trusted_managed_expires_at=managed_expires_at,
    )


def service(
    *,
    authority: StaticAdmissionAuthority | None = None,
    now: datetime = NOW,
    monotonic_now: float = 100.0,
    pdf_validator=None,
) -> tuple[DocumentAdmissionService, RecordingStore, StaticAdmissionAuthority]:
    store = RecordingStore()
    selected_authority = authority or StaticAdmissionAuthority()
    admission = DocumentAdmissionService(
        store=store,
        authority=selected_authority,
        clock=lambda: now,
        monotonic_clock=lambda: monotonic_now,
        pdf_validator=pdf_validator,
    )
    return admission, store, selected_authority


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("document", "operation", "media_type", "purpose"),
    [
        (
            ReportPdf(content=b"%PDF-1.7\n%%EOF"),
            DocumentKind.REPORT_PDF,
            "application/pdf",
            "report_pdf",
        ),
        (
            LabelDocument(content=b"^XA^FO20,20^FDInari^FS^XZ"),
            DocumentKind.LABEL_DOCUMENT,
            "application/vnd.zebra-zpl",
            "label_document",
        ),
    ],
)
async def test_admission_accepts_managed_report_documents_with_site_authority(
    document,
    operation: DocumentKind,
    media_type: str,
    purpose: str,
) -> None:
    admission, store, authority = service(pdf_validator=lambda content: None)
    work = managed_work(document)

    accepted = await admission.admit(
        AdmissionRequest(
            work=work,
            authorization=managed_authorization(operation),
            media_type=media_type,
            options={"copies": 1},
            trusted_managed_expires_at=NOW + timedelta(minutes=2),
        )
    )

    assert accepted.print_job_id == "job_01"
    assert store.admission is not None
    assert isinstance(store.admission.authorization_scope, ManagedAdmissionScope)
    assert authority.targets[0].scope.kind is ScopeKind.SITE
    assert authority.targets[0].purpose == purpose


@pytest.mark.anyio
async def test_admission_accepts_after_document_grant_and_authority_checks() -> None:
    admission, store, authority = service()

    accepted = await admission.admit(request())

    assert accepted.print_job_id == "job_01"
    assert len(authority.targets) == 1
    assert store.admission is not None
    assert store.admission.authorization_scope == grant().scope()
    assert store.admission.deadline.expires_at == NOW + timedelta(minutes=5)
    assert store.admission.deadline.monotonic_deadline == 400.0
    assert len(store.admission.payload_fingerprint) == 32
    assert store.admission.media_type == "image/jpeg"
    assert store.admission.normalized_options == b'{"cut":true,"density":203}'
    assert store.admission.authority_proof == authority.proof
    assert (
        authority.targets[0].options_digest
        == hashlib.sha256(store.admission.normalized_options).hexdigest()
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("document", "code"),
    [
        (ReportPdf(content=b"%PDF-1.7"), "document_policy_rejected"),
        (LabelDocument(content=b"^XA^XZ"), "document_policy_rejected"),
        (ReceiptImage(content=b"not a jpeg"), "payload_invalid"),
    ],
)
async def test_admission_rejects_closed_or_invalid_documents(
    document: ReceiptImage | ReportPdf | LabelDocument, code: str
) -> None:
    admission, store, authority = service()

    with pytest.raises(DocumentAdmissionError) as error:
        await admission.admit(request(receipt_work(document)))

    assert error.value.code == code
    assert authority.targets == []
    assert store.admission is None


@pytest.mark.anyio
async def test_receipt_image_enforces_byte_and_pixel_limits() -> None:
    admission, store, authority = service()
    oversized = ReceiptImage(content=b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(DocumentAdmissionError, match="2 MiB"):
        await admission.admit(request(receipt_work(oversized)))

    too_many_pixels = ReceiptImage(content=jpeg(width=6000, height=6000))
    with pytest.raises(DocumentAdmissionError, match="32 megapixels"):
        await admission.admit(request(receipt_work(too_many_pixels)))

    assert authority.targets == []
    assert store.admission is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("authority_code", "public_code"),
    [
        (AuthorityErrorCode.CERTIFICATION_REQUIRED, "certification_required"),
        (AuthorityErrorCode.TEST_REQUIRED, "certification_required"),
        (AuthorityErrorCode.OBSERVATION_UNAVAILABLE, "device_unavailable"),
        (AuthorityErrorCode.DEVICE_NOT_READY, "device_unavailable"),
        (AuthorityErrorCode.GRAPH_MISMATCH, "capability_changed"),
        (AuthorityErrorCode.REVOKED, "capability_changed"),
        (AuthorityErrorCode.OBSERVATION_DRIFT, "capability_changed"),
        (AuthorityErrorCode.AUTHORITY_UNAVAILABLE, "service_unavailable"),
        (AuthorityErrorCode.SIGNATURE_INVALID, "service_unavailable"),
        (AuthorityErrorCode.EXPIRED, "expired"),
    ],
)
async def test_admission_maps_authority_rejections(
    authority_code: AuthorityErrorCode,
    public_code: str,
) -> None:
    authority = StaticAdmissionAuthority(
        error=AuthorityError(authority_code, "The authority rejected Device Work.")
    )
    admission, store, _ = service(authority=authority)

    with pytest.raises(DocumentAdmissionError) as error:
        await admission.admit(request())

    assert error.value.code == public_code
    assert store.admission is None


@pytest.mark.anyio
async def test_admission_rejects_grant_mismatch_before_authority() -> None:
    admission, store, authority = service()

    with pytest.raises(DocumentAdmissionError) as error:
        await admission.admit(request(admission_grant=grant(site_id="other-site")))

    assert error.value.code == "permission_denied"
    assert authority.targets == []
    assert store.admission is None


@pytest.mark.anyio
async def test_managed_deadline_only_shortens_the_local_deadline() -> None:
    admission, store, _ = service()
    await admission.admit(
        request(managed_expires_at=NOW + timedelta(minutes=2, seconds=30))
    )
    assert store.admission is not None
    assert store.admission.deadline.expires_at == NOW + timedelta(minutes=2, seconds=30)

    admission, store, _ = service()
    await admission.admit(request(managed_expires_at=NOW + timedelta(minutes=10)))
    assert store.admission is not None
    assert store.admission.deadline.expires_at == NOW + timedelta(minutes=5)


@pytest.mark.anyio
async def test_authority_validity_shortens_the_durable_deadline() -> None:
    authority = StaticAdmissionAuthority()
    authority.proof = replace(
        authority.proof,
        valid_until=NOW + timedelta(minutes=1),
    )
    admission, store, _ = service(authority=authority)

    await admission.admit(request())

    assert store.admission is not None
    assert store.admission.deadline.expires_at == NOW + timedelta(minutes=1)
    assert store.admission.deadline.monotonic_deadline == 160.0
