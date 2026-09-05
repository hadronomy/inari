from __future__ import annotations

import asyncio
import hashlib
import io
import re
import time
import warnings
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, assert_never

import rfc8785
from PIL import Image, UnidentifiedImageError
from PIL.Image import DecompressionBombError, DecompressionBombWarning
from inari_print_contracts import LabelContractError, ZplLayout

from ..device_authority import (
    AdmissionAuthorizer,
    AuthorityError,
    AuthorityErrorCode,
    AuthorityScope,
    CapabilityAdmissionTarget,
    ScopeKind,
)

from .fingerprint import DeviceWorkFingerprintInput, fingerprint_device_work
from .models import (
    AdmissionAccepted,
    AdmissionDeadline,
    AdmissionGrant,
    AdmissionGrantScope,
    AdmissionRequest,
    Document,
    DocumentAdmissionError,
    DocumentKind,
    DocumentWork,
    DurableAdmission,
    LabelDocument,
    LocalPrintOrigin,
    ManagedAdmissionAuthorization,
    ManagedAdmissionScope,
    ManagedSubmissionContext,
    PosPrintOrigin,
    PreparationPrintOrigin,
    ReceiptImage,
    RecordsReportSource,
    ReportPrintOrigin,
    ReportPdf,
    SubmissionContext,
    WizardReportSource,
)
from .ports import DocumentAdmissionStore
from .validators import QpdfDocumentValidator, validate_label_document

_RECEIPT_IMAGE_MAX_BYTES = 2 * 1024 * 1024
_RECEIPT_IMAGE_MAX_PIXELS = 32_000_000
_ADMISSION_TTL = timedelta(minutes=5)
_MAX_IDENTIFIER_LENGTH = 256
_MEDIA_TYPE_PATTERN = re.compile(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+\Z")


class DocumentAdmissionService:
    """Validate one Device Work item before its durable store accepts it."""

    def __init__(
        self,
        *,
        store: DocumentAdmissionStore,
        authority: AdmissionAuthorizer,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
        monotonic_clock: Callable[[], float] = time.monotonic,
        pdf_validator: Callable[[bytes, int], None] | None = None,
    ) -> None:
        self._store = store
        self._authority = authority
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._pdf_validator = pdf_validator or (
            lambda content, dpi: QpdfDocumentValidator().validate(content, dpi=dpi)
        )

    async def admit(self, request: AdmissionRequest) -> AdmissionAccepted:
        """Validate, fingerprint, and durably accept one Device Work item."""

        if not isinstance(request, AdmissionRequest):
            raise DocumentAdmissionError(
                "payload_invalid", "The admission request has an invalid shape."
            )

        work = request.work
        self._validate_context(work)
        operation = _document_kind(work.document)
        media_type = _normalize_media_type(request.media_type)
        normalized_options = _normalize_options(request.options)

        # Validate content before reading mutable device facts. This keeps
        # malformed payloads from causing device lookups or driver work.
        await asyncio.to_thread(self._validate_document, work, request.options)
        expected_media_type = {
            DocumentKind.RECEIPT_IMAGE: "image/jpeg",
            DocumentKind.REPORT_PDF: "application/pdf",
            DocumentKind.LABEL_DOCUMENT: "application/vnd.zebra-zpl",
        }[operation]
        if media_type != expected_media_type:
            raise DocumentAdmissionError(
                "document_policy_rejected",
                "The media type does not match the document operation.",
            )

        authorization_scope = _authorization_scope(request.authorization, work)

        deadline = self._resolve_deadline(request.trusted_managed_expires_at)
        if deadline.is_expired(
            utc_now=_utc(self._clock()), monotonic_now=self._monotonic_clock()
        ):
            raise DocumentAdmissionError(
                "expired", "The Device Work deadline has passed."
            )

        now = _utc(self._clock())
        if deadline.is_expired(utc_now=now, monotonic_now=self._monotonic_clock()):
            raise DocumentAdmissionError(
                "expired", "The Device Work deadline has passed."
            )

        context = work.context
        origin = context.origin
        authority_scope = _authority_scope(context)
        try:
            permit = self._authority.authorize(
                CapabilityAdmissionTarget(
                    scope=authority_scope,
                    purpose=_device_purpose(operation, origin),
                    device_id=context.device_id,
                    binding_revision_id=context.binding_revision_id,
                    operation=operation.value,
                    media_type=media_type,
                    contract_major=context.contract_major,
                    options_digest=hashlib.sha256(normalized_options).hexdigest(),
                    requested_expires_at=deadline.expires_at,
                ),
                now=now,
            )
        except AuthorityError as error:
            raise _document_authority_error(error) from error

        deadline = _cap_deadline(
            deadline,
            permit.authority_proof.valid_until,
            utc_now=now,
            monotonic_now=self._monotonic_clock(),
        )
        payload_fingerprint = fingerprint_device_work(
            DeviceWorkFingerprintInput(
                contract_major=work.context.contract_major,
                operation=operation,
                device_id=work.context.device_id,
                media_type=media_type,
                document=work.document.content,
                options=request.options,
                expires_at=deadline.expires_at,
            ),
            canonicalize_options=lambda _: normalized_options,
        )

        return await self._store.accept(
            DurableAdmission(
                work=work,
                authorization_scope=authorization_scope,
                deadline=deadline,
                payload_fingerprint=payload_fingerprint,
                media_type=media_type,
                normalized_options=normalized_options,
                authority_proof=permit.authority_proof,
            )
        )

    def _resolve_deadline(
        self, managed_expires_at: datetime | None
    ) -> AdmissionDeadline:
        now = _utc(self._clock())
        monotonic_now = self._monotonic_clock()
        local_expires_at = now + _ADMISSION_TTL
        if managed_expires_at is not None:
            managed_expires_at = _utc(managed_expires_at)
            expires_at = min(local_expires_at, managed_expires_at)
        else:
            expires_at = local_expires_at
        seconds = max(0.0, (expires_at - now).total_seconds())
        return AdmissionDeadline(
            expires_at=expires_at,
            monotonic_deadline=monotonic_now + seconds,
        )

    @staticmethod
    def _validate_context(work: DocumentWork) -> None:
        if not isinstance(work, DocumentWork):
            raise DocumentAdmissionError(
                "payload_invalid", "The Device Work has an invalid shape."
            )
        context = work.context
        if not isinstance(context.contract_major, int) or isinstance(
            context.contract_major, bool
        ):
            raise DocumentAdmissionError(
                "payload_invalid", "Contract Major must be an integer."
            )
        if context.contract_major != 1:
            raise DocumentAdmissionError(
                "contract_mismatch", "Device Work requires Contract Major 1."
            )

        _validate_string("idempotency key", work.idempotency_key)
        for label, value in {
            "organization": context.organization_id,
            "site": context.site_id,
            "print intent": context.print_intent_id,
            "origin submission": context.origin_submission_key,
            "binding revision": context.binding_revision_id,
            "device": context.device_id,
            "actor": context.actor_id,
            "authorization digest": context.authorization_digest,
        }.items():
            _validate_string(label, value)
        if (
            not isinstance(context.copy_ordinal, int)
            or isinstance(context.copy_ordinal, bool)
            or context.copy_ordinal < 1
        ):
            raise DocumentAdmissionError(
                "payload_invalid", "Device Work copy ordinal must be positive."
            )

        if isinstance(context, SubmissionContext):
            if not isinstance(work.document, ReceiptImage):
                raise DocumentAdmissionError(
                    "document_policy_rejected",
                    "Local client work only accepts receipt images.",
                )
            DocumentAdmissionService._validate_local_context(context)
            return
        if isinstance(context, ManagedSubmissionContext):
            if not isinstance(work.document, ReportPdf | LabelDocument):
                raise DocumentAdmissionError(
                    "document_policy_rejected",
                    "Managed report work only accepts PDF or label documents.",
                )
            DocumentAdmissionService._validate_managed_context(context)
            return
        raise DocumentAdmissionError(
            "payload_invalid", "The Device Work context has an invalid shape."
        )

    @staticmethod
    def _validate_local_context(context: SubmissionContext) -> None:
        _validate_string("paired client", context.paired_client_id)
        origin = context.origin
        if not isinstance(origin, PosPrintOrigin | PreparationPrintOrigin):
            raise DocumentAdmissionError(
                "payload_invalid", "The print origin has an invalid shape."
            )
        for label, value in {
            "database": origin.database,
            "POS configuration": origin.pos_configuration_id,
            "POS session": origin.pos_session_id,
            "offline order": origin.offline_order_id,
            "document kind": origin.document_kind,
            "content revision": origin.content_revision,
        }.items():
            _validate_string(label, value)
        if origin.server_order_id is not None:
            _validate_string("server order", origin.server_order_id)
        if isinstance(origin, PreparationPrintOrigin):
            if origin.document_kind != "preparation_ticket":
                raise DocumentAdmissionError(
                    "payload_invalid",
                    "A preparation origin must identify a preparation ticket.",
                )
            _validate_string("preparation segment", origin.segment_kind)
            _validate_string("preparation revision", origin.preparation_revision)
            if origin.segment_kind not in {
                "new",
                "cancelled",
                "note_update",
                "notes",
            }:
                raise DocumentAdmissionError(
                    "payload_invalid", "The preparation segment is invalid."
                )
            if (
                not isinstance(origin.segment_index, int)
                or isinstance(origin.segment_index, bool)
                or origin.segment_index < 0
                or origin.segment_index > 63
            ):
                raise DocumentAdmissionError(
                    "payload_invalid",
                    "Preparation segment index must be between 0 and 63.",
                )

    @staticmethod
    def _validate_managed_context(context: ManagedSubmissionContext) -> None:
        if not isinstance(context.origin, ReportPrintOrigin):
            raise DocumentAdmissionError(
                "payload_invalid", "The report origin has an invalid shape."
            )
        for label, value in {
            "database": context.database,
            "company": context.company_id,
            "Managed Work": context.managed_work_id,
            "report binding": context.origin.binding.report_binding_id,
            "report action": context.origin.binding.report_action_id,
            "report contract digest": context.origin.binding.report_contract_digest,
            "template digest": context.origin.binding.template_digest,
        }.items():
            _validate_string(label, value)
        for label, value in {
            "command profile": context.origin.binding.command_profile_id,
            "layout profile": context.origin.binding.layout_profile_id,
            "hardware matrix digest": context.origin.binding.hardware_matrix_digest,
        }.items():
            if value is not None:
                _validate_string(label, value)
        if context.binding_revision_id != context.origin.binding.binding_revision_id:
            raise DocumentAdmissionError(
                "payload_invalid",
                "The report origin Binding Revision does not match the work context.",
            )
        if context.origin.route not in {"manual", "automatic"}:
            raise DocumentAdmissionError(
                "payload_invalid", "The report route is invalid."
            )
        if (
            not isinstance(context.origin.rendered_document_index, int)
            or isinstance(context.origin.rendered_document_index, bool)
            or context.origin.rendered_document_index < 0
            or context.origin.copy_ordinal != context.copy_ordinal
        ):
            raise DocumentAdmissionError(
                "payload_invalid", "The report document or copy ordinal is invalid."
            )
        source = context.origin.source
        _validate_string("report source model", source.model)
        if isinstance(source, WizardReportSource):
            _validate_string("report wizard digest", source.input_digest)
        elif (
            not isinstance(source, RecordsReportSource)
            or not source.ordered_ids
            or any(
                isinstance(record_id, bool) or record_id < 1
                for record_id in source.ordered_ids
            )
        ):
            raise DocumentAdmissionError(
                "payload_invalid", "The report record source is invalid."
            )

    def _validate_document(
        self, work: DocumentWork, options: Mapping[str, Any]
    ) -> None:
        match work.document:
            case ReceiptImage(content=content):
                self._validate_receipt_image(content)
            case ReportPdf(content=content):
                dpi = options.get("dpi")
                if (
                    set(options) != {"dpi"}
                    or type(dpi) is not int
                    or dpi not in {150, 203, 300}
                ):
                    raise DocumentAdmissionError(
                        "document_policy_rejected",
                        "Report PDF options require one supported DPI.",
                    )
                self._pdf_validator(content, dpi)
            case LabelDocument(content=content):
                layout = _label_layout(work, options)
                validate_label_document(content, layout=layout)
            case _:
                assert_never(work.document)

    @staticmethod
    def _validate_receipt_image(content: bytes) -> None:
        _validate_bytes(content, label="Receipt image")
        if len(content) > _RECEIPT_IMAGE_MAX_BYTES:
            raise DocumentAdmissionError(
                "payload_invalid", "Receipt image exceeds the 2 MiB limit."
            )

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if image.format != "JPEG":
                        raise DocumentAdmissionError(
                            "payload_invalid", "Receipt image must be a valid JPEG."
                        )
                    width, height = image.size
                    if width <= 0 or height <= 0:
                        raise DocumentAdmissionError(
                            "payload_invalid",
                            "Receipt image dimensions must be positive.",
                        )
                    if width * height > _RECEIPT_IMAGE_MAX_PIXELS:
                        raise DocumentAdmissionError(
                            "payload_invalid",
                            "Receipt image exceeds the 32 megapixels limit.",
                        )
                    image.verify()
                with Image.open(io.BytesIO(content)) as image:
                    image.load()
        except DocumentAdmissionError:
            raise
        except (DecompressionBombError, OSError, UnidentifiedImageError) as exc:
            raise DocumentAdmissionError(
                "payload_invalid", "Receipt image must be a valid JPEG."
            ) from exc


def _label_layout(work: DocumentWork, options: Mapping[str, Any]) -> ZplLayout:
    try:
        if set(options) != {"layout"} or not isinstance(options["layout"], dict):
            raise LabelContractError("Label Document options require an exact layout.")
        layout = ZplLayout(**options["layout"])
        context = work.context
        if not isinstance(context, ManagedSubmissionContext):
            raise LabelContractError("A Label Document requires Managed Device Work.")
        binding = context.origin.binding
        if (
            layout.profile_id != binding.layout_profile_id
            or binding.command_profile_id != "zpl_v1"
            or not binding.hardware_matrix_digest
        ):
            raise LabelContractError(
                "The label layout does not match its Report Binding."
            )
        return layout
    except (LabelContractError, TypeError) as error:
        raise DocumentAdmissionError(
            "document_policy_rejected", "The Label Document layout contract is invalid."
        ) from error


def _authorization_scope(
    authorization: AdmissionGrant | ManagedAdmissionAuthorization,
    work: DocumentWork,
) -> AdmissionGrantScope | ManagedAdmissionScope:
    if isinstance(authorization, AdmissionGrant):
        return _grant_scope(authorization, work)
    if isinstance(authorization, ManagedAdmissionAuthorization):
        return _managed_scope(authorization, work)
    raise DocumentAdmissionError(
        "permission_denied", "The admission authorization has an invalid shape."
    )


def _grant_scope(grant: AdmissionGrant, work: DocumentWork) -> AdmissionGrantScope:
    if not isinstance(work.context, SubmissionContext):
        raise DocumentAdmissionError(
            "permission_denied", "The Client Grant does not permit this Device Work."
        )
    context = work.context
    origin = context.origin
    if (
        not isinstance(grant.operation, DocumentKind)
        or grant.organization_id != context.organization_id
        or grant.site_id != context.site_id
        or grant.database != origin.database
        or grant.pos_configuration_id != origin.pos_configuration_id
        or grant.paired_client_id != context.paired_client_id
        or grant.actor_id != context.actor_id
        or grant.device_id != context.device_id
        or grant.binding_revision_id != context.binding_revision_id
        or grant.operation is not work.operation
        or grant.authorization_digest != context.authorization_digest
    ):
        raise DocumentAdmissionError(
            "permission_denied", "The Client Grant does not permit this Device Work."
        )
    return grant.scope()


def _managed_scope(
    authorization: ManagedAdmissionAuthorization,
    work: DocumentWork,
) -> ManagedAdmissionScope:
    context = work.context
    if (
        not isinstance(context, ManagedSubmissionContext)
        or not isinstance(authorization.operation, DocumentKind)
        or authorization.managed_work_id != context.managed_work_id
        or authorization.organization_id != context.organization_id
        or authorization.site_id != context.site_id
        or authorization.database != context.database
        or authorization.actor_id != context.actor_id
        or authorization.device_id != context.device_id
        or authorization.binding_revision_id != context.binding_revision_id
        or authorization.operation is not work.operation
        or authorization.authorization_digest != context.authorization_digest
    ):
        raise DocumentAdmissionError(
            "permission_denied",
            "The Controller authorization does not permit this Managed Device Work.",
        )
    return authorization.scope()


def _device_purpose(
    operation: DocumentKind,
    origin: LocalPrintOrigin | ReportPrintOrigin,
) -> str:
    if operation is DocumentKind.RECEIPT_IMAGE:
        return (
            "pos_preparation"
            if isinstance(origin, PreparationPrintOrigin)
            else "pos_receipt"
        )
    if operation is DocumentKind.REPORT_PDF:
        return "report_pdf"
    if operation is DocumentKind.LABEL_DOCUMENT:
        return "label_document"
    raise DocumentAdmissionError(
        "document_policy_rejected", "Unknown document operation."
    )


def _authority_scope(
    context: SubmissionContext | ManagedSubmissionContext,
) -> AuthorityScope:
    if isinstance(context, ManagedSubmissionContext):
        return AuthorityScope(
            database=context.database,
            organization_id=context.organization_id,
            site_id=context.site_id,
            kind=ScopeKind.SITE,
            pos_configuration_id=None,
        )
    return AuthorityScope(
        database=context.origin.database,
        organization_id=context.organization_id,
        site_id=context.site_id,
        kind=ScopeKind.POS_CONFIGURATION,
        pos_configuration_id=context.origin.pos_configuration_id,
    )


def _document_authority_error(error: AuthorityError) -> DocumentAdmissionError:
    code = {
        AuthorityErrorCode.INVALID_REQUEST: "payload_invalid",
        AuthorityErrorCode.SCOPE_MISMATCH: "permission_denied",
        AuthorityErrorCode.NOT_FOUND: "capability_changed",
        AuthorityErrorCode.SIGNATURE_INVALID: "service_unavailable",
        AuthorityErrorCode.SIGNER_PURPOSE_MISMATCH: "service_unavailable",
        AuthorityErrorCode.REVOKED: "capability_changed",
        AuthorityErrorCode.EXPIRED: "expired",
        AuthorityErrorCode.GRAPH_MISMATCH: "capability_changed",
        AuthorityErrorCode.CERTIFICATION_REQUIRED: "certification_required",
        AuthorityErrorCode.TEST_REQUIRED: "certification_required",
        AuthorityErrorCode.OBSERVATION_UNAVAILABLE: "device_unavailable",
        AuthorityErrorCode.OBSERVATION_DRIFT: "capability_changed",
        AuthorityErrorCode.DEVICE_NOT_READY: "device_unavailable",
        AuthorityErrorCode.AUTHORITY_UNAVAILABLE: "service_unavailable",
    }[error.code]
    return DocumentAdmissionError(code, error.message)


def _cap_deadline(
    deadline: AdmissionDeadline,
    authority_valid_until: datetime,
    *,
    utc_now: datetime,
    monotonic_now: float,
) -> AdmissionDeadline:
    valid_until = _utc(authority_valid_until)
    expires_at = min(deadline.expires_at, valid_until)
    if expires_at <= utc_now:
        raise DocumentAdmissionError(
            "expired", "The Device Capability authority deadline has passed."
        )
    if expires_at == deadline.expires_at:
        return deadline
    return AdmissionDeadline(
        expires_at=expires_at,
        monotonic_deadline=monotonic_now + (expires_at - utc_now).total_seconds(),
    )


def _document_kind(document: Document) -> DocumentKind:
    match document:
        case ReceiptImage():
            return DocumentKind.RECEIPT_IMAGE
        case ReportPdf():
            return DocumentKind.REPORT_PDF
        case LabelDocument():
            return DocumentKind.LABEL_DOCUMENT
        case _:
            assert_never(document)


def _normalize_options(options: Mapping[str, Any]) -> bytes:
    if not isinstance(options, Mapping):
        raise DocumentAdmissionError(
            "payload_invalid", "Device options must be a JSON object."
        )
    try:
        value = rfc8785.dumps(dict(options))
    except (TypeError, ValueError, OverflowError) as exc:
        raise DocumentAdmissionError(
            "payload_invalid", "Device options must contain finite JSON values."
        ) from exc
    if not isinstance(value, bytes):
        raise DocumentAdmissionError(
            "payload_invalid", "Device options could not be normalized."
        )
    return value


def _normalize_media_type(value: str) -> str:
    _validate_string("media type", value)
    normalized = value.strip().lower()
    if not _MEDIA_TYPE_PATTERN.fullmatch(normalized):
        raise DocumentAdmissionError(
            "payload_invalid", "Media type must be a normalized type/subtype value."
        )
    return normalized


def _validate_bytes(content: bytes, *, label: str) -> None:
    if not isinstance(content, bytes) or not content:
        raise DocumentAdmissionError(
            "payload_invalid", f"{label} content must be non-empty bytes."
        )


def _validate_string(label: str, value: object) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_IDENTIFIER_LENGTH
    ):
        raise DocumentAdmissionError(
            "payload_invalid", f"Device Work {label} must be a non-empty string."
        )


def _utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise DocumentAdmissionError(
            "payload_invalid", "Admission timestamps must include a UTC offset."
        )
    return value.astimezone(UTC)
