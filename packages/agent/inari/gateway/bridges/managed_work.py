from __future__ import annotations

from hashlib import sha256

import rfc8785

from ...documents import (
    AdmissionRequest,
    DocumentKind,
    DocumentWork,
    LabelDocument,
    ManagedAdmissionAuthorization,
    ManagedSubmissionContext,
    RecordsReportSource,
    ReportBinding,
    ReportPdf,
    ReportPrintOrigin,
    WizardReportSource,
)
from ..managed_dispatch import VerifiedManagedDispatch
from ..models import GatewayEnrollmentRecord


def managed_admission_request(
    dispatch: VerifiedManagedDispatch,
    *,
    enrollment: GatewayEnrollmentRecord,
) -> AdmissionRequest:
    trust = enrollment.managed_dispatch
    if trust is None:
        raise ValueError("Managed dispatch trust is required.")
    work = dispatch.work
    binding_claim = work.origin.binding
    binding = ReportBinding(
        report_binding_id=binding_claim.report_binding_id,
        binding_revision_id=binding_claim.binding_revision_id,
        report_action_id=binding_claim.report_action_id,
        report_contract_digest=binding_claim.report_contract_digest,
        template_digest=binding_claim.template_digest,
        command_profile_id=binding_claim.command_profile_id,
        layout_profile_id=binding_claim.layout_profile_id,
        hardware_matrix_digest=binding_claim.hardware_matrix_digest,
    )
    source_payload = work.origin.source
    source = (
        RecordsReportSource(
            model=source_payload.model,
            ordered_ids=tuple(source_payload.ordered_ids),
        )
        if source_payload.kind == "records"
        else WizardReportSource(
            model=source_payload.model,
            input_digest=source_payload.input_digest,
        )
    )
    origin = ReportPrintOrigin(
        binding=binding,
        route=work.origin.route,
        source=source,
        rendered_document_index=work.origin.rendered_document_index,
        copy_ordinal=work.origin.copy_ordinal,
    )
    authorization_digest = sha256(
        rfc8785.dumps(
            {
                "dispatch_epoch": dispatch.dispatch_epoch,
                "issuer": trust.issuer,
                "managed_work_id": dispatch.managed_work_id,
                "report_binding": binding_claim.model_dump(mode="json"),
            }
        )
    ).hexdigest()
    context = ManagedSubmissionContext(
        contract_major=work.contract_major,
        database=work.scope.database,
        company_id=work.scope.company_id,
        organization_id=work.scope.organization_id,
        site_id=work.scope.site_id,
        managed_work_id=dispatch.managed_work_id,
        print_intent_id=work.print_intent_id,
        origin_submission_key=_origin_submission_key(dispatch),
        origin=origin,
        binding_revision_id=binding.binding_revision_id,
        device_id=work.device_id,
        actor_id=f"controller:{trust.issuer}",
        authorization_digest=authorization_digest,
        copy_ordinal=origin.copy_ordinal,
    )
    operation = (
        DocumentKind.REPORT_PDF
        if work.document.operation == "report_pdf"
        else DocumentKind.LABEL_DOCUMENT
    )
    document = (
        ReportPdf(content=dispatch.document_bytes)
        if operation is DocumentKind.REPORT_PDF
        else LabelDocument(content=dispatch.document_bytes)
    )
    authorization = ManagedAdmissionAuthorization(
        managed_work_id=dispatch.managed_work_id,
        organization_id=context.organization_id,
        site_id=context.site_id,
        database=context.database,
        actor_id=context.actor_id,
        device_id=context.device_id,
        binding_revision_id=context.binding_revision_id,
        operation=operation,
        authorization_digest=authorization_digest,
    )
    return AdmissionRequest(
        work=DocumentWork(
            idempotency_key=dispatch.idempotency_key,
            context=context,
            document=document,
        ),
        authorization=authorization,
        media_type=(
            "application/pdf"
            if operation is DocumentKind.REPORT_PDF
            else "application/vnd.zebra-zpl"
        ),
        options=dict(work.normalized_device_options),
        trusted_managed_expires_at=dispatch.expires_at,
    )


def _origin_submission_key(dispatch: VerifiedManagedDispatch) -> str:
    origin = dispatch.work.origin
    digest = sha256(
        rfc8785.dumps(
            {
                "copy_ordinal": origin.copy_ordinal,
                "print_intent_id": dispatch.work.print_intent_id,
                "rendered_document_index": origin.rendered_document_index,
            }
        )
    ).hexdigest()
    return f"report:{digest}"


__all__ = ["managed_admission_request"]
