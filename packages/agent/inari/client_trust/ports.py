from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from .models import (
    AccessTokenClaims,
    AcceptedDPoPProof,
    AcceptedRenewalDPoPProof,
    ClientGrant,
    ClientPairing,
    PairingAssertion,
    PairingAssertionClaims,
    PairingRequest,
    RequestTarget,
)
from .permissions import Permission


class DPoPNonceConsumption(StrEnum):
    ACCEPTED = "accepted"
    INVALID = "invalid"
    REPLAY = "replay"


class ClientTrustStore(Protocol):
    """Durable seam for Client Pairing, grants, and one-time proof identities."""

    def get_pairing_request(self, request_id: str) -> PairingRequest | None: ...

    def save_pairing_request(self, request: PairingRequest) -> None: ...

    def get_pairing(self, pairing_id: str) -> ClientPairing | None: ...

    def save_pairing(self, pairing: ClientPairing) -> None: ...

    def get_grant(self, grant_id: str) -> ClientGrant | None: ...

    def save_grant(self, grant: ClientGrant) -> None: ...

    def complete_pairing(
        self,
        *,
        request: PairingRequest,
        pairing: ClientPairing,
        grant: ClientGrant,
        assertion_jti: str,
        at: datetime,
    ) -> bool: ...

    def save_dpop_nonce(
        self, nonce: str, *, issued_at: datetime, expires_at: datetime
    ) -> None: ...

    def consume_dpop_nonce(
        self,
        nonce: str,
        *,
        jti: str,
        at: datetime,
        replay_expires_at: datetime,
    ) -> DPoPNonceConsumption: ...


class PairingAssertionVerifierPort(Protocol):
    """Verifies the company-signed Pairing Assertion at the trust seam."""

    def verify(
        self,
        assertion: PairingAssertion,
        *,
        request: PairingRequest,
        at: datetime,
    ) -> PairingAssertionClaims: ...


class AccessTokenVerifierPort(Protocol):
    """Verifies an access token and returns content-free claims."""

    def verify(self, token: str, *, at: datetime) -> AccessTokenClaims: ...


class DPoPVerifierPort(Protocol):
    """Verifies one DPoP proof after the request headers are available."""

    def verify(
        self,
        proof: str,
        *,
        target: RequestTarget,
        claims: AccessTokenClaims,
        access_token: str,
        at: datetime,
    ) -> AcceptedDPoPProof: ...


class RenewalDPoPVerifierPort(Protocol):
    """Verifies a key-bound DPoP proof for offline Grant renewal."""

    def verify(
        self,
        proof: str,
        *,
        target: RequestTarget,
        jwk_thumbprint: str,
        at: datetime,
    ) -> AcceptedRenewalDPoPProof: ...


class AccessTokenIssuerPort(Protocol):
    def issue(
        self,
        grant: ClientGrant,
        *,
        issued_at: datetime,
        expires_at: datetime,
    ) -> tuple[str, AccessTokenClaims]: ...


class PermissionPolicy(Protocol):
    def require(self, grant: ClientGrant, permission: Permission | str) -> None: ...


class TrustClock(Protocol):
    def now(self) -> datetime: ...


__all__ = [
    "AccessTokenIssuerPort",
    "AccessTokenVerifierPort",
    "ClientTrustStore",
    "DPoPNonceConsumption",
    "DPoPVerifierPort",
    "PairingAssertionVerifierPort",
    "PermissionPolicy",
    "RenewalDPoPVerifierPort",
    "TrustClock",
]
