from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import ValidationError
from starlette.requests import Request

from ..client_trust import AuthorizedRequest, ClientTrustError, Permission
from ..core.failures import (
    DomainFailure,
    FieldViolation,
    FieldViolationCode,
    ProblemCode,
    ProblemDetails,
)
from ..documents import (
    AdmissionAccepted,
    AdmissionGrant,
    AdmissionRequest,
    DocumentAdmission,
    DocumentAdmissionError,
    DocumentKind,
    DocumentWork,
    PosPrintOrigin,
    PreparationPrintOrigin,
    ReceiptImage,
    SubmissionContext,
)
from .header_authorization import AUTHORIZATION_RESULT_STATE_KEY
from .ingress import DeviceWorkIngress
from .schemas.device_work import PreparationPrintOriginInput, ReceiptImageEnvelope


_DOCUMENT_FAILURES = {
    "payload_invalid": ProblemCode.PAYLOAD_INVALID,
    "contract_mismatch": ProblemCode.CONTRACT_MISMATCH,
    "document_policy_rejected": ProblemCode.DOCUMENT_POLICY_REJECTED,
    "permission_denied": ProblemCode.PERMISSION_DENIED,
    "capability_changed": ProblemCode.CAPABILITY_CHANGED,
    "certification_required": ProblemCode.CERTIFICATION_REQUIRED,
    "device_unavailable": ProblemCode.DEVICE_UNAVAILABLE,
    "expired": ProblemCode.EXPIRED,
    "service_unavailable": ProblemCode.SERVICE_UNAVAILABLE,
}


@dataclass(frozen=True, slots=True)
class DeviceWorkSubmission:
    """Admit one authorized, bounded Local Device Work request."""

    admission: DocumentAdmission
    ingress: DeviceWorkIngress = field(default_factory=DeviceWorkIngress)

    async def submit(
        self,
        request: Request,
        authorization: AuthorizedRequest,
    ) -> AdmissionAccepted:
        """Parse the body only after the caller supplies accepted Client Trust."""

        try:
            authorization.require(Permission.RECEIPT_IMAGE)
        except ClientTrustError as error:
            raise DomainFailure(ProblemCode.PERMISSION_DENIED) from error
        idempotency_key = _idempotency_key(request)
        parsed = await self.ingress.parse(request)
        envelope = _envelope(parsed.envelope)
        if envelope.context.print_intent_id != idempotency_key:
            raise DomainFailure(
                ProblemCode.PAYLOAD_INVALID,
                details=ProblemDetails(
                    field_violations=(
                        FieldViolation(
                            pointer="/context/print_intent_id",
                            code=FieldViolationCode.CONFLICT,
                            message_key="print_intent_id_mismatch",
                        ),
                    )
                ),
            )

        request_value = _admission_request(
            envelope=envelope,
            document=parsed.document,
            idempotency_key=idempotency_key,
            authorization=authorization,
        )
        try:
            return await self.admission.admit(request_value)
        except DocumentAdmissionError as error:
            raise DomainFailure(
                _DOCUMENT_FAILURES.get(error.code, ProblemCode.INTERNAL_ERROR)
            ) from error


def authorized_device_work_request(request: Request) -> AuthorizedRequest:
    """Read the authorization result that the header middleware stored."""

    state = request.scope.get("state")
    result = (
        state.get(AUTHORIZATION_RESULT_STATE_KEY)
        if isinstance(state, Mapping)
        else None
    )
    if not isinstance(result, AuthorizedRequest):
        raise DomainFailure(ProblemCode.TRUST_REQUIRED)
    return result


def _idempotency_key(request: Request) -> str:
    values = [
        value
        for name, value in request.scope.get("headers", ())
        if bytes(name).lower() == b"idempotency-key"
    ]
    if len(values) != 1:
        raise DomainFailure(ProblemCode.REQUEST_MALFORMED)
    try:
        value = bytes(values[0]).decode("ascii")
    except (UnicodeDecodeError, TypeError, ValueError) as error:
        raise DomainFailure(ProblemCode.REQUEST_MALFORMED) from error
    if not value or len(value) > 256 or not value.isascii() or not value.isprintable():
        raise DomainFailure(ProblemCode.REQUEST_MALFORMED)
    if value != value.strip() or any(character.isspace() for character in value):
        raise DomainFailure(ProblemCode.REQUEST_MALFORMED)
    return value


def _envelope(value: object) -> ReceiptImageEnvelope:
    try:
        return ReceiptImageEnvelope.model_validate(value)
    except ValidationError as error:
        violations = tuple(_field_violation(item) for item in error.errors()[:32])
        raise DomainFailure(
            ProblemCode.PAYLOAD_INVALID,
            details=ProblemDetails(field_violations=violations),
        ) from error


def _field_violation(item: Mapping[str, object]) -> FieldViolation:
    raw_location = item.get("loc", ())
    location = raw_location if isinstance(raw_location, tuple | list) else ()
    pointer = "/" + "/".join(
        str(value)[:64].replace("~", "~0").replace("/", "~1") for value in location[:16]
    )
    if len(pointer) > 512:
        pointer = "/request"
    raw_type = str(item.get("type", "invalid"))
    code = (
        FieldViolationCode.REQUIRED
        if raw_type == "missing"
        else FieldViolationCode.OUT_OF_RANGE
        if any(token in raw_type for token in ("greater", "less", "range"))
        else FieldViolationCode.INVALID
    )
    return FieldViolation(
        pointer=pointer,
        code=code,
        message_key=f"request_field_{code.value}",
    )


def _admission_request(
    *,
    envelope: ReceiptImageEnvelope,
    document: bytes,
    idempotency_key: str,
    authorization: AuthorizedRequest,
) -> AdmissionRequest:
    grant = authorization.grant
    business = grant.scope.business
    pos_configuration_id = business.pos_configuration_id
    if pos_configuration_id is None:
        raise DomainFailure(ProblemCode.BINDING_REQUIRED)

    context_input = envelope.context
    origin_input = context_input.origin
    origin_values = {
        "database": business.database,
        "pos_configuration_id": pos_configuration_id,
        "pos_session_id": origin_input.pos_session_id,
        "offline_order_id": origin_input.offline_order_id,
        "server_order_id": origin_input.server_order_id,
        "document_kind": origin_input.document_kind,
        "content_revision": origin_input.content_revision,
    }
    origin = (
        PreparationPrintOrigin(
            **origin_values,
            segment_kind=origin_input.segment_kind,
            segment_index=origin_input.segment_index,
            preparation_revision=origin_input.preparation_revision,
        )
        if isinstance(origin_input, PreparationPrintOriginInput)
        else PosPrintOrigin(**origin_values)
    )
    context = SubmissionContext(
        contract_major=envelope.contract_major,
        organization_id=business.organization_id,
        site_id=business.site_id,
        paired_client_id=grant.pairing_id,
        print_intent_id=context_input.print_intent_id,
        origin_submission_key=context_input.origin_submission_key,
        origin=origin,
        binding_revision_id=context_input.binding_revision_id,
        device_id=context_input.device_id,
        actor_id=grant.actor_id,
        authorization_digest=grant.authorization_digest,
        copy_ordinal=context_input.copy_ordinal,
    )
    admission_grant = AdmissionGrant(
        grant_id=grant.grant_id,
        pairing_id=grant.pairing_id,
        generation=grant.generation,
        organization_id=business.organization_id,
        site_id=business.site_id,
        database=business.database,
        pos_configuration_id=pos_configuration_id,
        paired_client_id=grant.pairing_id,
        actor_id=grant.actor_id,
        device_id=context.device_id,
        binding_revision_id=context.binding_revision_id,
        operation=DocumentKind.RECEIPT_IMAGE,
        authorization_digest=grant.authorization_digest,
    )
    return AdmissionRequest(
        work=DocumentWork(
            idempotency_key=idempotency_key,
            context=context,
            document=ReceiptImage(content=document),
        ),
        grant=admission_grant,
        media_type=envelope.media_type,
        options={},
    )


__all__ = ["DeviceWorkSubmission", "authorized_device_work_request"]
