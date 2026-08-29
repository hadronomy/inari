from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    AccessTokenClaims,
    AcceptedDPoPProof,
    ClientGrant,
    ClientPairing,
    PairingAssertion,
    PairingAssertionClaims,
    PairingRequest,
    RequestTarget,
)
from .permissions import Permission


class ClientTrustStore(Protocol):
    """Durable seam for Client Pairing, grants, and one-time proof identities."""

    def get_pairing(self, pairing_id: str) -> ClientPairing | None: ...

    def save_pairing(self, pairing: ClientPairing) -> None: ...

    def get_grant(self, grant_id: str) -> ClientGrant | None: ...

    def save_grant(self, grant: ClientGrant) -> None: ...

    def consume_pairing_assertion(self, assertion_jti: str, *, at: datetime) -> bool: ...

    def consume_dpop_jti(self, jti: str, *, expires_at: datetime) -> bool: ...


class PairingAssertionVerifier(Protocol):
    """Verifies the company-signed Pairing Assertion at the trust seam."""

    def verify(
        self,
        assertion: PairingAssertion,
        *,
        request: PairingRequest,
        at: datetime,
    ) -> PairingAssertionClaims: ...


class AccessTokenVerifier(Protocol):
    """Verifies an access token and returns content-free claims."""

    def verify(self, token: str, *, at: datetime) -> AccessTokenClaims: ...


class DPoPVerifier(Protocol):
    """Verifies one DPoP proof after the request headers are available."""

    def verify(
        self,
        proof: str,
        *,
        target: RequestTarget,
        claims: AccessTokenClaims,
        access_token: str,
        nonce: str,
        at: datetime,
    ) -> AcceptedDPoPProof: ...


class AccessTokenIssuer(Protocol):
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
    "AccessTokenIssuer",
    "AccessTokenVerifier",
    "ClientTrustStore",
    "DPoPVerifier",
    "PairingAssertionVerifier",
    "PermissionPolicy",
    "TrustClock",
]
