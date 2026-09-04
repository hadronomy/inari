from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pyhpke import AEADId, KDFId, KEMId, CipherSuite
from pydantic import ValidationError

from ..core.exceptions import AgentError
from ..security.dispatch_keys import DispatchEncryptionKeyService
from .models import ControllerAction, GatewayEnrollmentRecord
from .protocol import (
    ControllerDispatchDeviceWorkMessage,
    GatewayProtocolModel,
    ManagedDispatchClaimsPayload,
    ManagedDeviceWorkPayload,
)

_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_DISPATCH_JWS_TYPE = "application/inari-dispatch+jws"
_MAX_PDF_BYTES = 10 * 1024 * 1024
_MAX_LABEL_BYTES = 2 * 1024 * 1024


class _DispatchJwsHeader(GatewayProtocolModel):
    alg: str
    kid: str
    typ: str


@dataclass(frozen=True, slots=True)
class VerifiedManagedDispatch:
    managed_work_id: str
    idempotency_key: str
    work: ManagedDeviceWorkPayload
    document_bytes: bytes
    dispatch_epoch: int
    sequence: int
    expires_at: datetime


class ManagedDispatchVerifier:
    def __init__(self, dispatch_keys: DispatchEncryptionKeyService) -> None:
        self._dispatch_keys = dispatch_keys
        self._suite = CipherSuite.new(
            KEMId.DHKEM_X25519_HKDF_SHA256,
            KDFId.HKDF_SHA256,
            AEADId.AES256_GCM,
        )

    def verify(
        self,
        message: ControllerDispatchDeviceWorkMessage,
        *,
        enrollment: GatewayEnrollmentRecord,
        now: datetime | None = None,
    ) -> VerifiedManagedDispatch:
        trust = enrollment.managed_dispatch
        if (
            ControllerAction.MANAGED_WORK_DISPATCH not in enrollment.controller_actions
            or trust is None
        ):
            raise _dispatch_error(
                "MANAGED_DISPATCH_SCOPE_DENIED",
                "The Controller is not authorized to dispatch Managed Device Work.",
                status_code=403,
            )

        aad = message.payload.authenticated_data
        if message.payload.managed_work_id != aad.managed_work_id:
            raise _dispatch_error(
                "MANAGED_DISPATCH_BINDING_INVALID",
                "The outer Managed Work ID does not match the authenticated data.",
            )
        if message.sequence != aad.sequence:
            raise _dispatch_error(
                "MANAGED_DISPATCH_SEQUENCE_INVALID",
                "The command sequence does not match the authenticated data.",
            )
        if int(message.issued_at.timestamp()) != aad.issued_at:
            raise _dispatch_error(
                "MANAGED_DISPATCH_ISSUED_AT_INVALID",
                "The command issue time does not match the authenticated data.",
            )
        if (
            aad.organization_id != trust.scope.organization_id
            or aad.site_id != trust.scope.site_id
            or aad.agent_id != trust.scope.agent_id
        ):
            raise _dispatch_error(
                "MANAGED_DISPATCH_SCOPE_MISMATCH",
                "The authenticated dispatch scope does not match Agent enrollment.",
                status_code=403,
            )
        if aad.dispatch_epoch != trust.epoch:
            raise _dispatch_error(
                "MANAGED_DISPATCH_EPOCH_INVALID",
                "The dispatch epoch does not match Agent enrollment.",
            )

        checked_at = (now or datetime.now(tz=UTC)).astimezone(UTC)
        if aad.expires_at <= int(checked_at.timestamp()):
            raise _dispatch_error(
                "MANAGED_DISPATCH_EXPIRED",
                "The managed dispatch envelope has expired.",
            )
        if aad.issued_at > int(checked_at.timestamp()) + 30:
            raise _dispatch_error(
                "MANAGED_DISPATCH_NOT_YET_VALID",
                "The managed dispatch issue time is in the future.",
            )
        if aad.expires_at <= aad.issued_at:
            raise _dispatch_error(
                "MANAGED_DISPATCH_DEADLINE_INVALID",
                "The managed dispatch deadline is invalid.",
            )

        signed_envelope = self._decrypt(message)
        claims = self._verify_jws(signed_envelope, enrollment=enrollment)
        if claims.authenticated_data != aad:
            raise _dispatch_error(
                "MANAGED_DISPATCH_BINDING_INVALID",
                "The signed claims do not match the authenticated data.",
            )
        if claims.iss != trust.issuer or claims.aud != trust.scope.agent_id:
            raise _dispatch_error(
                "MANAGED_DISPATCH_IDENTITY_INVALID",
                "The signed Controller identity does not match Agent enrollment.",
                status_code=403,
            )

        work = claims.work
        if (
            work.scope.organization_id != aad.organization_id
            or work.scope.site_id != aad.site_id
            or work.scope.agent_id != aad.agent_id
        ):
            raise _dispatch_error(
                "MANAGED_DISPATCH_WORK_MISMATCH",
                "The signed work does not match the authenticated dispatch scope.",
            )
        document_bytes = _decode_document(work)
        if sha256(document_bytes).hexdigest() != aad.payload_fingerprint:
            raise _dispatch_error(
                "MANAGED_DISPATCH_FINGERPRINT_MISMATCH",
                "The signed document does not match its payload fingerprint.",
            )

        return VerifiedManagedDispatch(
            managed_work_id=aad.managed_work_id,
            idempotency_key=aad.idempotency_key,
            work=work,
            document_bytes=document_bytes,
            dispatch_epoch=aad.dispatch_epoch,
            sequence=aad.sequence,
            expires_at=datetime.fromtimestamp(aad.expires_at, tz=UTC),
        )

    def _decrypt(self, message: ControllerDispatchDeviceWorkMessage) -> bytes:
        envelope = message.payload.sealed_envelope
        key_pair = self._dispatch_keys.get_or_create()
        if envelope.key_id != key_pair.key_id:
            raise _dispatch_error(
                "MANAGED_DISPATCH_KEY_INVALID",
                "The dispatch envelope targets a different Agent key.",
            )

        private_bytes = key_pair.private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        try:
            private_key = self._suite.kem.deserialize_private_key(private_bytes)
            context = self._suite.create_recipient_context(
                _decode_base64url(envelope.encapsulated_key_base64url),
                private_key,
                info=_dispatch_info(
                    message.payload.authenticated_data.agent_id,
                    envelope.key_id,
                ),
            )
            return context.open(
                _decode_base64url(envelope.ciphertext_base64url),
                aad=rfc8785.dumps(
                    message.payload.authenticated_data.model_dump(mode="json")
                ),
            )
        except AgentError:
            raise
        except Exception as exc:
            raise _dispatch_error(
                "MANAGED_DISPATCH_DECRYPTION_FAILED",
                "The managed dispatch envelope could not be authenticated.",
            ) from exc

    def _verify_jws(
        self,
        signed_envelope: bytes,
        *,
        enrollment: GatewayEnrollmentRecord,
    ) -> ManagedDispatchClaimsPayload:
        try:
            compact = signed_envelope.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _dispatch_error(
                "MANAGED_DISPATCH_JWS_INVALID",
                "The decrypted dispatch is not a compact JWS.",
            ) from exc
        segments = compact.split(".")
        if len(segments) != 3 or not all(segments):
            raise _dispatch_error(
                "MANAGED_DISPATCH_JWS_INVALID",
                "The decrypted dispatch is not a compact JWS.",
            )

        protected_bytes = _decode_base64url(segments[0])
        payload_bytes = _decode_base64url(segments[1])
        signature = _decode_base64url(segments[2])
        header = _parse_canonical_model(protected_bytes, _DispatchJwsHeader)
        trust = enrollment.managed_dispatch
        assert trust is not None
        if (
            header.alg != "EdDSA"
            or header.typ != _DISPATCH_JWS_TYPE
            or header.kid != trust.verification_jwk.kid
        ):
            raise _dispatch_error(
                "MANAGED_DISPATCH_JWS_HEADER_INVALID",
                "The managed dispatch JWS header is not trusted.",
            )

        public_key_bytes = _decode_base64url(trust.verification_jwk.x)
        if len(public_key_bytes) != 32 or len(signature) != 64:
            raise _dispatch_error(
                "MANAGED_DISPATCH_SIGNATURE_INVALID",
                "The managed dispatch signature is invalid.",
            )
        try:
            Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
                signature,
                f"{segments[0]}.{segments[1]}".encode("ascii"),
            )
        except (InvalidSignature, ValueError) as exc:
            raise _dispatch_error(
                "MANAGED_DISPATCH_SIGNATURE_INVALID",
                "The managed dispatch signature is invalid.",
            ) from exc

        return _parse_canonical_model(payload_bytes, ManagedDispatchClaimsPayload)


def _parse_canonical_model(payload: bytes, model_type):
    try:
        value = json.loads(payload)
        if rfc8785.dumps(value) != payload:
            raise ValueError("JSON is not in canonical form")
        return model_type.model_validate(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as exc:
        raise _dispatch_error(
            "MANAGED_DISPATCH_JWS_INVALID",
            "The managed dispatch JWS contains invalid canonical JSON.",
        ) from exc


def _decode_document(work: ManagedDeviceWorkPayload) -> bytes:
    try:
        encoded = work.document.content_base64.encode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise _dispatch_error(
            "MANAGED_DISPATCH_DOCUMENT_INVALID",
            "The managed document is not valid base64.",
        ) from exc
    if base64.b64encode(decoded) != encoded:
        raise _dispatch_error(
            "MANAGED_DISPATCH_DOCUMENT_INVALID",
            "The managed document base64 is not canonical.",
        )
    limit = (
        _MAX_PDF_BYTES if work.document.operation == "report_pdf" else _MAX_LABEL_BYTES
    )
    if len(decoded) > limit:
        raise _dispatch_error(
            "MANAGED_DISPATCH_DOCUMENT_TOO_LARGE",
            "The managed document exceeds its size limit.",
            status_code=413,
        )
    return decoded


def _decode_base64url(value: str) -> bytes:
    if not _BASE64URL.fullmatch(value):
        raise _dispatch_error(
            "MANAGED_DISPATCH_BASE64URL_INVALID",
            "The managed dispatch contains invalid base64url data.",
        )
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise _dispatch_error(
            "MANAGED_DISPATCH_BASE64URL_INVALID",
            "The managed dispatch contains invalid base64url data.",
        ) from exc
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise _dispatch_error(
            "MANAGED_DISPATCH_BASE64URL_INVALID",
            "The managed dispatch contains non-canonical base64url data.",
        )
    return decoded


def _dispatch_info(agent_id: str, key_id: str) -> bytes:
    return f"inari-managed-dispatch\0v1\0{agent_id}\0{key_id}".encode()


def _dispatch_error(
    code: str,
    message: str,
    *,
    status_code: int = 400,
) -> AgentError:
    return AgentError(code, message, status_code=status_code)


__all__ = ["ManagedDispatchVerifier", "VerifiedManagedDispatch"]
