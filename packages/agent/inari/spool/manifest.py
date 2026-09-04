from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import rfc8785
from sqlalchemy.engine import RowMapping

from ..core.failures import ProblemCode, ProblemDetails
from ..device_authority import AuthorityProof
from ..documents import (
    AdmissionGrantScope,
    DocumentKind,
    DurableAdmission,
    LabelDocument,
    ManagedAdmissionScope,
    ManagedSubmissionContext,
    PreparationPrintOrigin,
    ReceiptImage,
    RecordsReportSource,
    ReportPdf,
    SubmissionContext,
    WizardReportSource,
)
from ..documents.fingerprint import (
    DeviceWorkFingerprintInput,
    fingerprint_device_work,
)
from .errors import SpoolAdmissionError
from .types import AdmissionManifest


DatabaseRow = Mapping[str, Any] | RowMapping


def manifest_from_admission(admission: DurableAdmission) -> AdmissionManifest:
    if not isinstance(admission, DurableAdmission):
        raise SpoolAdmissionError(ProblemCode.PAYLOAD_INVALID)
    work = admission.work
    expected_document = {
        DocumentKind.RECEIPT_IMAGE: (ReceiptImage, "image/jpeg"),
        DocumentKind.REPORT_PDF: (ReportPdf, "application/pdf"),
        DocumentKind.LABEL_DOCUMENT: (
            LabelDocument,
            "application/vnd.zebra-zpl",
        ),
    }[work.operation]
    if (
        not isinstance(work.document, expected_document[0])
        or admission.media_type != expected_document[1]
    ):
        raise SpoolAdmissionError(ProblemCode.DOCUMENT_POLICY_REJECTED)
    if len(admission.payload_fingerprint) != 32 or not work.document.content:
        raise SpoolAdmissionError(ProblemCode.PAYLOAD_INVALID)
    context = work.context
    authorization = admission.authorization_scope
    proof = admission.authority_proof
    if isinstance(context, SubmissionContext) and isinstance(
        authorization, AdmissionGrantScope
    ):
        origin = context.origin
        preparation = isinstance(origin, PreparationPrintOrigin)
        if (
            authorization.organization_id != context.organization_id
            or authorization.site_id != context.site_id
            or authorization.database != origin.database
            or authorization.pos_configuration_id != origin.pos_configuration_id
            or authorization.paired_client_id != context.paired_client_id
            or authorization.actor_id != context.actor_id
            or authorization.device_id != context.device_id
            or authorization.binding_revision_id != context.binding_revision_id
            or authorization.operation is not work.operation
            or authorization.authorization_digest != context.authorization_digest
        ):
            raise SpoolAdmissionError(ProblemCode.PERMISSION_DENIED)
        scope_kind = "paired_client"
        managed_work_id = None
        database = origin.database
        pos_configuration_id = origin.pos_configuration_id
        paired_client_id = context.paired_client_id
        expected_purpose = "pos_preparation" if preparation else "pos_receipt"
        origin_kind = "preparation" if preparation else "pos"
        origin_values = _local_origin_values(context)
        grant_id = authorization.grant_id
        grant_pairing_id = authorization.pairing_id
        grant_generation = authorization.generation
        authorization_values = {
            "actor_id": authorization.actor_id,
            "authorization_digest": authorization.authorization_digest,
            "binding_revision_id": authorization.binding_revision_id,
            "database": authorization.database,
            "device_id": authorization.device_id,
            "operation": authorization.operation.value,
            "organization_id": authorization.organization_id,
            "paired_client_id": authorization.paired_client_id,
            "pos_configuration_id": authorization.pos_configuration_id,
            "site_id": authorization.site_id,
        }
    elif isinstance(context, ManagedSubmissionContext) and isinstance(
        authorization, ManagedAdmissionScope
    ):
        if (
            authorization.managed_work_id != context.managed_work_id
            or authorization.organization_id != context.organization_id
            or authorization.site_id != context.site_id
            or authorization.database != context.database
            or authorization.actor_id != context.actor_id
            or authorization.device_id != context.device_id
            or authorization.binding_revision_id != context.binding_revision_id
            or authorization.operation is not work.operation
            or authorization.authorization_digest != context.authorization_digest
        ):
            raise SpoolAdmissionError(ProblemCode.PERMISSION_DENIED)
        scope_kind = "device_manager"
        managed_work_id = context.managed_work_id
        database = context.database
        pos_configuration_id = None
        paired_client_id = None
        expected_purpose = work.operation.value
        origin_kind = "report"
        origin_values = _managed_origin_values(context)
        grant_id = None
        grant_pairing_id = None
        grant_generation = None
        authorization_values = {
            "actor_id": authorization.actor_id,
            "authorization_digest": authorization.authorization_digest,
            "binding_revision_id": authorization.binding_revision_id,
            "database": authorization.database,
            "device_id": authorization.device_id,
            "managed_work_id": authorization.managed_work_id,
            "operation": authorization.operation.value,
            "organization_id": authorization.organization_id,
            "site_id": authorization.site_id,
        }
    else:
        raise SpoolAdmissionError(ProblemCode.PERMISSION_DENIED)
    if not isinstance(proof, AuthorityProof):
        raise SpoolAdmissionError(ProblemCode.PERMISSION_DENIED)
    normalized_options_digest = hashlib.sha256(admission.normalized_options).hexdigest()
    if (
        proof.binding_revision_id != context.binding_revision_id
        or proof.device_id != context.device_id
        or proof.purpose != expected_purpose
        or proof.operation != work.operation.value
        or proof.media_type != admission.media_type
        or proof.contract_major != context.contract_major
        or proof.options_digest != normalized_options_digest
        or proof.valid_until < admission.deadline.expires_at
        or proof.issued_at >= proof.valid_until
    ):
        raise SpoolAdmissionError(ProblemCode.PERMISSION_DENIED)
    expected_fingerprint = fingerprint_device_work(
        DeviceWorkFingerprintInput(
            contract_major=context.contract_major,
            operation=work.operation,
            device_id=context.device_id,
            media_type=admission.media_type,
            document=work.document.content,
            options={},
            expires_at=admission.deadline.expires_at,
        ),
        canonicalize_options=lambda _: admission.normalized_options,
    )
    if expected_fingerprint != admission.payload_fingerprint:
        raise SpoolAdmissionError(ProblemCode.PAYLOAD_INVALID)
    origin_json = canonical_json(origin_values)
    return AdmissionManifest(
        scope_kind=scope_kind,
        managed_work_id=managed_work_id,
        grant_id=grant_id,
        grant_pairing_id=grant_pairing_id,
        grant_generation=grant_generation,
        idempotency_key=work.idempotency_key,
        database=database,
        organization_id=context.organization_id,
        site_id=context.site_id,
        pos_configuration_id=pos_configuration_id,
        paired_client_id=paired_client_id,
        actor_id=context.actor_id,
        device_id=context.device_id,
        binding_revision_id=context.binding_revision_id,
        authorization_digest=hashlib.sha256(
            context.authorization_digest.encode("utf-8")
        ).digest(),
        operation=work.operation.value,
        media_type=admission.media_type,
        normalized_options_digest=bytes.fromhex(normalized_options_digest),
        grant_scope_digest=hashlib.sha256(
            rfc8785.dumps(authorization_values)
        ).digest(),
        origin_submission_key=context.origin_submission_key,
        origin_kind=origin_kind,
        origin_json=origin_json,
        contract_major=context.contract_major,
        copy_ordinal=context.copy_ordinal,
        intent_id=context.print_intent_id,
        fingerprint=admission.payload_fingerprint,
        deadline_at=timestamp(admission.deadline.expires_at),
        original_size_bytes=len(work.document.content),
        authority_proof=proof,
    )


def manifest_from_row(
    row: DatabaseRow, authority_proof: AuthorityProof
) -> AdmissionManifest:
    return AdmissionManifest(
        scope_kind=row["scope_kind"],
        managed_work_id=row["managed_work_id"],
        grant_id=row["grant_id"],
        grant_pairing_id=row["grant_pairing_id"],
        grant_generation=row["grant_generation"],
        idempotency_key=row["idempotency_key"],
        database=row["database"],
        organization_id=row["organization_id"],
        site_id=row["site_id"],
        pos_configuration_id=row["pos_configuration_id"],
        paired_client_id=row["paired_client_id"],
        actor_id=row["actor_id"],
        device_id=row["device_id"],
        binding_revision_id=row["binding_revision_id"],
        authorization_digest=row["authorization_digest"],
        operation=row["operation"],
        media_type=row["media_type"],
        normalized_options_digest=row["normalized_options_digest"],
        grant_scope_digest=row["grant_scope_digest"],
        origin_submission_key=row["origin_submission_key"],
        origin_kind=row["origin_kind"],
        origin_json=row["origin_json"],
        contract_major=row["contract_major"],
        copy_ordinal=row["copy_ordinal"],
        intent_id=row["intent_id"],
        fingerprint=row["fingerprint"],
        deadline_at=row["deadline_at"],
        original_size_bytes=row["original_size_bytes"],
        authority_proof=authority_proof,
    )


def assert_exact_replay(row: DatabaseRow, manifest: AdmissionManifest) -> None:
    expected = manifest_comparison(manifest)
    if {key: row[key] for key in expected} != expected:
        raise SpoolAdmissionError(ProblemCode.IDEMPOTENCY_CONFLICT)


def raise_if_aborted(row: DatabaseRow) -> None:
    if row["state"] == "aborted":
        raise SpoolAdmissionError(
            ProblemCode(row["failure_code"]),
            details=ProblemDetails(
                device_id=row["device_id"],
                job_id=row["planned_job_id"],
            ),
        )


def same_origin_submission(row: DatabaseRow, manifest: AdmissionManifest) -> bool:
    expected = origin_comparison(manifest)
    return {key: row[key] for key in expected} == expected


def manifest_comparison(manifest: AdmissionManifest) -> dict[str, object]:
    return {
        "scope_kind": manifest.scope_kind,
        "managed_work_id": manifest.managed_work_id,
        "grant_id": manifest.grant_id,
        "grant_pairing_id": manifest.grant_pairing_id,
        "grant_generation": manifest.grant_generation,
        "actor_id": manifest.actor_id,
        "authorization_digest": manifest.authorization_digest,
        "binding_revision_id": manifest.binding_revision_id,
        "contract_major": manifest.contract_major,
        "copy_ordinal": manifest.copy_ordinal,
        "database": manifest.database,
        "deadline_at": manifest.deadline_at,
        "device_id": manifest.device_id,
        "fingerprint": manifest.fingerprint,
        "grant_scope_digest": manifest.grant_scope_digest,
        "intent_id": manifest.intent_id,
        "media_type": manifest.media_type,
        "normalized_options_digest": manifest.normalized_options_digest,
        "operation": manifest.operation,
        "organization_id": manifest.organization_id,
        "origin_json": manifest.origin_json,
        "origin_kind": manifest.origin_kind,
        "origin_submission_key": manifest.origin_submission_key,
        "original_size_bytes": manifest.original_size_bytes,
        "paired_client_id": manifest.paired_client_id,
        "pos_configuration_id": manifest.pos_configuration_id,
        "site_id": manifest.site_id,
    }


def _local_origin_values(context: SubmissionContext) -> dict[str, object]:
    origin = context.origin
    values: dict[str, object] = {
        "content_revision": origin.content_revision,
        "database": origin.database,
        "document_kind": origin.document_kind,
        "offline_order_id": origin.offline_order_id,
        "organization_id": context.organization_id,
        "paired_client_id": context.paired_client_id,
        "pos_configuration_id": origin.pos_configuration_id,
        "pos_session_id": origin.pos_session_id,
        "server_order_id": origin.server_order_id,
        "site_id": context.site_id,
    }
    if isinstance(origin, PreparationPrintOrigin):
        values.update(
            {
                "preparation_revision": origin.preparation_revision,
                "segment_index": origin.segment_index,
                "segment_kind": origin.segment_kind,
            }
        )
    return values


def _managed_origin_values(context: ManagedSubmissionContext) -> dict[str, object]:
    origin = context.origin
    binding = origin.binding
    source = origin.source
    if not isinstance(source, RecordsReportSource | WizardReportSource):
        raise SpoolAdmissionError(ProblemCode.PAYLOAD_INVALID)
    source_values = (
        {
            "kind": "records",
            "model": source.model,
            "ordered_ids": list(source.ordered_ids),
        }
        if isinstance(source, RecordsReportSource)
        else {
            "kind": "wizard",
            "model": source.model,
            "input_digest": source.input_digest,
        }
    )
    return {
        "binding": {
            "binding_revision_id": binding.binding_revision_id,
            "command_profile_id": binding.command_profile_id,
            "hardware_matrix_digest": binding.hardware_matrix_digest,
            "layout_profile_id": binding.layout_profile_id,
            "report_action_id": binding.report_action_id,
            "report_binding_id": binding.report_binding_id,
            "report_contract_digest": binding.report_contract_digest,
            "template_digest": binding.template_digest,
        },
        "company_id": context.company_id,
        "database": context.database,
        "managed_work_id": context.managed_work_id,
        "organization_id": context.organization_id,
        "rendered_document_index": origin.rendered_document_index,
        "route": origin.route,
        "site_id": context.site_id,
        "source": source_values,
    }


def origin_comparison(manifest: AdmissionManifest) -> dict[str, object]:
    comparison = manifest_comparison(manifest)
    comparison.pop("deadline_at")
    comparison.pop("intent_id")
    return comparison


def canonical_json(value: Mapping[str, Any]) -> str:
    try:
        encoded = rfc8785.dumps(dict(value))
    except (TypeError, ValueError, OverflowError):
        raise SpoolAdmissionError(ProblemCode.PAYLOAD_INVALID) from None
    return encoded.decode("utf-8")


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SpoolAdmissionError(ProblemCode.INTERNAL_ERROR)
    return value.astimezone(UTC)


def timestamp(value: datetime) -> str:
    return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (AttributeError, ValueError):
        raise SpoolAdmissionError(ProblemCode.INTERNAL_ERROR) from None


__all__ = [
    "assert_exact_replay",
    "canonical_json",
    "manifest_from_admission",
    "manifest_from_row",
    "parse_timestamp",
    "raise_if_aborted",
    "same_origin_submission",
    "timestamp",
    "utc",
]
