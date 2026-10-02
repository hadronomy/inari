from __future__ import annotations

from enum import StrEnum
from typing import Mapping


class AuthorityErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "authority_projection_missing"
    SIGNATURE_INVALID = "signature_invalid"
    SIGNER_PURPOSE_MISMATCH = "signer_purpose_mismatch"
    REVOKED = "capability_revoked"
    EXPIRED = "capability_expired"
    SCOPE_MISMATCH = "scope_mismatch"
    GRAPH_MISMATCH = "capability_graph_mismatch"
    CERTIFICATION_REQUIRED = "certification_required"
    TEST_REQUIRED = "device_test_required"
    OBSERVATION_UNAVAILABLE = "device_observation_unavailable"
    OBSERVATION_DRIFT = "device_observation_drift"
    DEVICE_NOT_READY = "device_not_ready"
    AUTHORITY_UNAVAILABLE = "authority_unavailable"


class AuthorityError(ValueError):
    """A fail-closed rejection from the Device Capability authority."""

    def __init__(
        self,
        code: AuthorityErrorCode,
        message: str,
        *,
        details: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
