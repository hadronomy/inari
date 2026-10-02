from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from .models import IssuedDPoPNonce


class ClientTrustErrorCode(StrEnum):
    INVALID_ORIGIN = "invalid_origin"
    INVALID_VALUE = "invalid_value"
    UNKNOWN_PERMISSION = "unknown_permission"
    PERMISSION_DENIED = "permission_denied"
    SCOPE_MISMATCH = "scope_mismatch"
    PAIRING_EXPIRED = "pairing_expired"
    GRANT_EXPIRED = "grant_expired"
    GRANT_REVOKED = "grant_revoked"
    INVALID_ASSERTION = "invalid_assertion"
    INVALID_DPOP_PROOF = "invalid_dpop_proof"
    REPLAY_DETECTED = "replay_detected"


class ClientTrustError(ValueError):
    """A safe, content-free rejection from the Client Trust domain."""

    def __init__(
        self,
        code: ClientTrustErrorCode,
        message: str,
        *,
        details: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


class InvalidOriginError(ClientTrustError):
    def __init__(self, message: str = "The browser origin is not allowed.") -> None:
        super().__init__(ClientTrustErrorCode.INVALID_ORIGIN, message)


class PermissionDeniedError(ClientTrustError):
    def __init__(self, permission: str) -> None:
        super().__init__(
            ClientTrustErrorCode.PERMISSION_DENIED,
            "The Client Grant does not permit this operation.",
            details={"permission": permission},
        )


class ScopeMismatchError(ClientTrustError):
    def __init__(
        self, message: str = "The request is outside the Client Grant scope."
    ) -> None:
        super().__init__(ClientTrustErrorCode.SCOPE_MISMATCH, message)


class ReplayDetectedError(ClientTrustError):
    def __init__(self, message: str = "The proof was already accepted.") -> None:
        super().__init__(ClientTrustErrorCode.REPLAY_DETECTED, message)


class DPoPNonceRequiredError(ClientTrustError):
    """Challenge an otherwise valid proof with a fresh one-time nonce."""

    def __init__(self, nonce: IssuedDPoPNonce) -> None:
        super().__init__(
            ClientTrustErrorCode.INVALID_DPOP_PROOF,
            "A fresh DPoP nonce is required.",
        )
        self.nonce = nonce
