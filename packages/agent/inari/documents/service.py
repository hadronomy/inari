from __future__ import annotations

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
    ReceiptImage,
    ReportPdf,
)
from .ports import DocumentAdmissionStore

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
    ) -> None:
        self._store = store
        self._authority = authority
        self._clock = clock
        self._monotonic_clock = monotonic_clock

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
        self._validate_document(work.document)
        if operation is not DocumentKind.RECEIPT_IMAGE or media_type != "image/jpeg":
            raise DocumentAdmissionError(
                "document_policy_rejected",
                "Only receipt-image JPEG Device Work is enabled.",
            )

        grant_scope = _grant_scope(request.grant, work)

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
        try:
            permit = self._authority.authorize(
                CapabilityAdmissionTarget(
                    scope=AuthorityScope(
                        database=origin.database,
                        organization_id=context.organization_id,
                        site_id=context.site_id,
                        kind=ScopeKind.POS_CONFIGURATION,
                        pos_configuration_id=origin.pos_configuration_id,
                    ),
                    purpose=_device_purpose(operation),
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
                grant_scope=grant_scope,
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
            "paired client": context.paired_client_id,
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

        origin = context.origin
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

    @classmethod
    def _validate_document(cls, document: Document) -> None:
        match document:
            case ReceiptImage(content=content):
                cls._validate_receipt_image(content)
            case ReportPdf() | LabelDocument():
                raise DocumentAdmissionError(
                    "document_policy_rejected",
                    "This document operation is not enabled.",
                )
            case _:
                assert_never(document)

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


def _grant_scope(grant: AdmissionGrant, work: DocumentWork) -> AdmissionGrantScope:
    if not isinstance(grant, AdmissionGrant):
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


def _device_purpose(operation: DocumentKind) -> str:
    if operation is DocumentKind.RECEIPT_IMAGE:
        return "pos_receipt"
    raise DocumentAdmissionError(
        "document_policy_rejected", "This document operation is not enabled."
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
