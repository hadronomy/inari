from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO

import pytest
from PIL import Image

from inari.documents import (
    AdmissionAccepted,
    AdmissionGrant,
    AdmissionRequest,
    DocumentAdmissionError,
    DocumentAdmissionService,
    DocumentKind,
    DocumentWork,
    DurableAdmission,
    PosPrintOrigin,
    ReceiptImage,
    SubmissionContext,
)
from tests.support.device_authority import StaticAdmissionAuthority


NOW = datetime(2026, 8, 26, tzinfo=UTC)


def jpeg() -> bytes:
    image = Image.new("L", (2, 2), color=255)
    output = BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


@dataclass(slots=True)
class RecordingAdmissionStore:
    accepted: list[DurableAdmission] = field(default_factory=list)

    async def accept(self, admission: DurableAdmission) -> AdmissionAccepted:
        self.accepted.append(admission)
        return AdmissionAccepted(
            print_intent_id=admission.work.context.print_intent_id,
            print_job_id="job_01",
            device_id=admission.work.context.device_id,
            accepted_at=NOW,
            state_version=1,
            replayed=False,
        )


def receipt_work() -> DocumentWork:
    origin = PosPrintOrigin(
        database="odoo",
        pos_configuration_id="pos_config_7",
        pos_session_id="pos_session_42",
        offline_order_id="01991a84-d0c2-7a49-89ad-2fd14bdbe501",
        server_order_id=None,
        document_kind="customer_receipt",
        content_revision="sha256:receipt-revision",
    )
    context = SubmissionContext(
        contract_major=1,
        organization_id="org_1",
        site_id="site_1",
        paired_client_id="client_1",
        print_intent_id="pi_v1_test",
        origin_submission_key="osk_v1_test",
        origin=origin,
        binding_revision_id="binding_revision_9",
        device_id="dev_receipt_1",
        actor_id="res.users:7",
        authorization_digest="sha256:authorization",
        copy_ordinal=1,
    )
    return DocumentWork(
        idempotency_key="pi_v1_test",
        context=context,
        document=ReceiptImage(content=jpeg()),
    )


def admission_grant() -> AdmissionGrant:
    return AdmissionGrant(
        grant_id="grant_1",
        pairing_id="pairing_1",
        generation=1,
        organization_id="org_1",
        site_id="site_1",
        database="odoo",
        pos_configuration_id="pos_config_7",
        paired_client_id="client_1",
        actor_id="res.users:7",
        device_id="dev_receipt_1",
        binding_revision_id="binding_revision_9",
        operation=DocumentKind.RECEIPT_IMAGE,
        authorization_digest="sha256:authorization",
    )


def admission_request(work: DocumentWork) -> AdmissionRequest:
    return AdmissionRequest(
        work=work,
        grant=admission_grant(),
        media_type="image/jpeg",
        options={},
    )


@pytest.mark.anyio
async def test_document_admission_returns_content_free_acceptance() -> None:
    store = RecordingAdmissionStore()
    authority = StaticAdmissionAuthority()
    admission = DocumentAdmissionService(
        store=store,
        authority=authority,
        clock=lambda: NOW,
        monotonic_clock=lambda: 100.0,
    )

    work = receipt_work()
    accepted = await admission.admit(admission_request(work))

    assert accepted == AdmissionAccepted(
        print_intent_id="pi_v1_test",
        print_job_id="job_01",
        device_id="dev_receipt_1",
        accepted_at=NOW,
        state_version=1,
        replayed=False,
    )
    assert [item.work for item in store.accepted] == [work]
    assert store.accepted[0].authority_proof == authority.proof
    assert len(authority.targets) == 1
    target = authority.targets[0]
    assert target.scope.database == "odoo"
    assert target.scope.pos_configuration_id == "pos_config_7"
    assert target.purpose == "pos_receipt"
    assert target.device_id == "dev_receipt_1"
    assert target.binding_revision_id == "binding_revision_9"
    assert target.operation == "receipt_image"
    assert target.options_digest == hashlib.sha256(b"{}").hexdigest()
    assert not hasattr(accepted, "content")


@pytest.mark.anyio
async def test_document_admission_rejects_non_jpeg_receipt_before_store() -> None:
    store = RecordingAdmissionStore()
    authority = StaticAdmissionAuthority()
    admission = DocumentAdmissionService(
        store=store,
        authority=authority,
        clock=lambda: NOW,
        monotonic_clock=lambda: 100.0,
    )
    invalid = receipt_work()
    invalid = DocumentWork(
        idempotency_key=invalid.idempotency_key,
        context=invalid.context,
        document=ReceiptImage(content=b"not-a-jpeg"),
    )

    with pytest.raises(DocumentAdmissionError, match="valid JPEG"):
        await admission.admit(admission_request(invalid))

    assert store.accepted == []
    assert authority.targets == []


def test_contract_major_one_document_kinds_are_closed() -> None:
    assert tuple(DocumentKind) == (
        DocumentKind.RECEIPT_IMAGE,
        DocumentKind.REPORT_PDF,
        DocumentKind.LABEL_DOCUMENT,
    )
