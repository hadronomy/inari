from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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
    WizardReportSource,
)
from inari_print_contracts.fingerprint import (
    DeviceWorkFingerprintInput,
    fingerprint_device_work,
)
from inari.spool.manifest import manifest_from_admission
from inari.db.schema import metadata
from inari.print_jobs import (
    PrintIntentQuery,
    ReportPrintOrigin as PublicReportPrintOrigin,
    SiteManagerScope,
)
from inari.print_jobs.sqlite import SqlitePrintJobReader
from inari.runtime.store import RuntimeStore
from tests.support.device_authority import authority_proof

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


@pytest.mark.anyio
@pytest.mark.parametrize("wizard", [False, True])
async def test_managed_manifest_keeps_controller_and_report_identity_without_a_client_grant(
    tmp_path: Path,
    wizard: bool,
) -> None:
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
            source=(
                WizardReportSource(
                    model="stock.label.wizard", input_digest="sha256:" + "a" * 64
                )
                if wizard
                else RecordsReportSource(model="stock.picking", ordered_ids=(17, 19))
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
    assert json.loads(manifest.origin_json)["source"] == (
        {
            "kind": "wizard",
            "model": "stock.label.wizard",
            "input_digest": "sha256:" + "a" * 64,
        }
        if wizard
        else {"kind": "records", "model": "stock.picking", "ordered_ids": [17, 19]}
    )

    store = RuntimeStore(tmp_path / "agent.sqlite3")
    metadata.create_all(store.engine)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """INSERT INTO public_print_jobs (
                id, admission_id, intent_id, device_id, scope_kind,
                organization_id, site_id, origin_kind, origin_json, managed_work_id,
                state, state_version, accepted_at, expires_at, retryable, contract_version
            ) VALUES ('job_report', 'admission_report', ?, ?, 'device_manager',
                ?, ?, 'report', ?, ?, 'accepted', 1, ?, ?, 0, 'v1')""",
            (
                manifest.intent_id,
                manifest.device_id,
                manifest.organization_id,
                manifest.site_id,
                manifest.origin_json,
                manifest.managed_work_id,
                NOW.isoformat(),
                (NOW + timedelta(minutes=2)).isoformat(),
            ),
        )
    result = await SqlitePrintJobReader(store).reconcile(
        PrintIntentQuery.from_ids(
            [manifest.intent_id],
            scope=SiteManagerScope(organization_id="org_1", site_id="site_1"),
        )
    )
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.managed_work_id == "mw_1"
    assert isinstance(job.origin, PublicReportPrintOrigin)
    assert job.origin.report_binding_id == binding.report_binding_id
    assert job.origin.report_action == binding.report_action_id
    assert job.origin.report_route == "automatic"
    assert job.origin.source_model == (
        "stock.label.wizard" if wizard else "stock.picking"
    )
    assert job.origin.record_ids == (() if wizard else ("17", "19"))
    assert job.origin.wizard_input_digest == ("sha256:" + "a" * 64 if wizard else None)

    wrong_scope = json.loads(manifest.origin_json)
    wrong_scope["site_id"] = "site_other"
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE public_print_jobs SET origin_json = ? WHERE id = 'job_report'",
            (json.dumps(wrong_scope, sort_keys=True, separators=(",", ":")),),
        )
    with pytest.raises(RuntimeError, match="does not match its Print Job"):
        await SqlitePrintJobReader(store).reconcile(
            PrintIntentQuery.from_ids(
                [manifest.intent_id],
                scope=SiteManagerScope(organization_id="org_1", site_id="site_1"),
            )
        )
