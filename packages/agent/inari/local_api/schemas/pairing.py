from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ...client_trust.models import (
    ClientGrant,
    ClientPairing,
    PairingRequest,
    PairingResult,
    RenewalResult,
)
from .base import APIModel


class PublicEd25519JwkInput(APIModel):
    kty: Literal["OKP"]
    crv: Literal["Ed25519"]
    x: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


class PairingBusinessScopeInput(APIModel):
    database: str = Field(min_length=1, max_length=256)
    company_id: str = Field(min_length=1, max_length=256)
    organization_id: str = Field(min_length=1, max_length=256)
    site_id: str = Field(min_length=1, max_length=256)
    pos_configuration_id: str | None = Field(default=None, min_length=1, max_length=256)


class PairingRequestCreateInput(APIModel):
    browser_jwk: PublicEd25519JwkInput
    business: PairingBusinessScopeInput
    requested_permissions: tuple[str, ...] = Field(min_length=1, max_length=8)


class PairingScopeResponse(APIModel):
    agent_id: str
    browser_origin: str
    agent_endpoint: str
    database: str
    company_id: str
    organization_id: str
    site_id: str
    pos_configuration_id: str | None
    audience: str


class PairingRequestResponse(APIModel):
    request_id: str
    scope: PairingScopeResponse
    browser_jwk_thumbprint: str
    requested_permissions: tuple[str, ...]
    session_nonce: str
    phrase: str
    approval_uri: str
    created_at: datetime
    expires_at: datetime
    state: str

    @classmethod
    def from_domain(cls, request: PairingRequest) -> PairingRequestResponse:
        scope = request.scope
        return cls(
            request_id=request.request_id,
            scope=PairingScopeResponse(
                agent_id=scope.agent_id,
                browser_origin=scope.browser_origin.value,
                agent_endpoint=scope.agent_endpoint.value,
                database=scope.business.database,
                company_id=scope.business.company_id,
                organization_id=scope.business.organization_id,
                site_id=scope.business.site_id,
                pos_configuration_id=scope.business.pos_configuration_id,
                audience=scope.audience,
            ),
            browser_jwk_thumbprint=request.browser_jwk_thumbprint,
            requested_permissions=tuple(
                permission.value
                for permission in sorted(
                    request.requested_permissions, key=lambda item: item.value
                )
            ),
            session_nonce=request.session_nonce,
            phrase=request.phrase,
            approval_uri=f"inari://pairing/{request.request_id}",
            created_at=request.created_at,
            expires_at=request.expires_at,
            state=request.state.value,
        )


class PairingDecisionInput(APIModel):
    decision: Literal["approve", "deny"]


class PairingAdmissionInput(APIModel):
    assertion: str = Field(min_length=5, max_length=16384)


class ClientPairingResponse(APIModel):
    pairing_id: str
    pairing_request_id: str
    jwk_thumbprint: str
    actor_id: str
    role: str
    permissions: tuple[str, ...]
    created_at: datetime

    @classmethod
    def from_domain(cls, pairing: ClientPairing) -> ClientPairingResponse:
        return cls(
            pairing_id=pairing.pairing_id,
            pairing_request_id=pairing.pairing_request_id,
            jwk_thumbprint=pairing.jwk_thumbprint,
            actor_id=pairing.actor_id,
            role=pairing.role,
            permissions=tuple(
                item.value
                for item in sorted(pairing.permissions, key=lambda item: item.value)
            ),
            created_at=pairing.created_at,
        )


class ClientGrantResponse(APIModel):
    grant_id: str
    pairing_id: str
    authorization_digest: str
    generation: int
    permissions: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    offline_renewal_until: datetime | None

    @classmethod
    def from_domain(cls, grant: ClientGrant) -> ClientGrantResponse:
        return cls(
            grant_id=grant.grant_id,
            pairing_id=grant.pairing_id,
            authorization_digest=grant.authorization_digest,
            generation=grant.generation,
            permissions=tuple(
                item.value
                for item in sorted(grant.permissions, key=lambda item: item.value)
            ),
            issued_at=grant.issued_at,
            expires_at=grant.expires_at,
            offline_renewal_until=grant.offline_renewal_until,
        )


class PairingAdmissionResponse(APIModel):
    pairing: ClientPairingResponse
    grant: ClientGrantResponse
    access_token: str
    token_type: Literal["DPoP"] = "DPoP"
    expires_at: datetime

    @classmethod
    def from_domain(cls, result: PairingResult) -> PairingAdmissionResponse:
        return cls(
            pairing=ClientPairingResponse.from_domain(result.pairing),
            grant=ClientGrantResponse.from_domain(result.grant),
            access_token=result.access_token,
            expires_at=result.claims.expires_at,
        )


class ClientGrantRenewalInput(APIModel):
    pairing_id: str = Field(min_length=1, max_length=256)
    grant_id: str = Field(min_length=1, max_length=256)


class ClientGrantRenewalResponse(APIModel):
    grant: ClientGrantResponse
    access_token: str
    token_type: Literal["DPoP"] = "DPoP"
    expires_at: datetime

    @classmethod
    def from_domain(cls, result: RenewalResult) -> ClientGrantRenewalResponse:
        return cls(
            grant=ClientGrantResponse.from_domain(result.grant),
            access_token=result.access_token,
            expires_at=result.claims.expires_at,
        )


__all__ = [
    "ClientGrantRenewalInput",
    "ClientGrantRenewalResponse",
    "PairingAdmissionInput",
    "PairingAdmissionResponse",
    "PairingDecisionInput",
    "PairingRequestCreateInput",
    "PairingRequestResponse",
]
