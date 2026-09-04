from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from inari.documents import (
    AdmissionDeadline,
    DocumentKind,
    DocumentWork,
    DurableAdmission,
    LabelDocument,
    ManagedAdmissionScope,
    ManagedSubmissionContext,
    RecordsReportSource,
    ReportBinding,
    ReportPrintOrigin,
)
from inari.documents.fingerprint import (
    DeviceWorkFingerprintInput,
    fingerprint_device_work,
)
from inari.spool.manifest import manifest_from_admission
from tests.support.device_authority import authority_proof

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


def test_managed_manifest_keeps_controller_and_report_identity_without_a_client_grant() -> None:
    binding = ReportBinding(
        report_binding_id="report-binding-1",
        binding_revision_id="binding_revision_9",
        report_action_id="stock.action_report_delivery",
        report_contract_digest="contract-digest",
        template_digest="template-digest",
        command_profile_id=None,
        layout_profile_id=None,
        hardware_matrix_digest="matrix-digest",
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
            route="automatic",
            source=RecordsReportSource(
                model="stock.picking",
                ordered_ids=(17, 19),
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
    work = DocumentWork(
        idempotency_key="mw_1",
        context=context,
        document=LabelDocument(content=b"^XA^FDInari^FS^XZ"),
    )
    deadline = AdmissionDeadline(
        expires_at=NOW + timedelta(minutes=2),
        monotonic_deadline=120.0,
    )
    fingerprint = fingerprint_device_work(
        DeviceWorkFingerprintInput(
            contract_major=1,
            operation=DocumentKind.LABEL_DOCUMENT,
            device_id=context.device_id,
            media_type="application/vnd.zebra-zpl",
            document=work.document.content,
            options={},
            expires_at=deadline.expires_at,
        )
    )
    admission = DurableAdmission(
        work=work,
        authorization_scope=ManagedAdmissionScope(
            managed_work_id=context.managed_work_id,
            organization_id=context.organization_id,
            site_id=context.site_id,
            database=context.database,
            actor_id=context.actor_id,
            device_id=context.device_id,
            binding_revision_id=context.binding_revision_id,
            operation=DocumentKind.LABEL_DOCUMENT,
            authorization_digest=context.authorization_digest,
        ),
        deadline=deadline,
        payload_fingerprint=fingerprint,
        media_type="application/vnd.zebra-zpl",
        normalized_options=b"{}",
        authority_proof=replace(
            authority_proof(),
            binding_revision_id=context.binding_revision_id,
            device_id=context.device_id,
            purpose="label_document",
            operation="label_document",
            media_type="application/vnd.zebra-zpl",
            options_digest=hashlib.sha256(b"{}").hexdigest(),
            valid_until=NOW + timedelta(minutes=5),
        ),
    )

    manifest = manifest_from_admission(admission)

    assert manifest.scope_kind == "device_manager"
    assert manifest.managed_work_id == "mw_1"
    assert manifest.grant_id is None
    assert manifest.grant_pairing_id is None
    assert manifest.paired_client_id is None
    assert manifest.pos_configuration_id is None
    assert manifest.origin_kind == "report"
    assert json.loads(manifest.origin_json)["source"] == {
        "kind": "records",
        "model": "stock.picking",
        "ordered_ids": [17, 19],
    }
