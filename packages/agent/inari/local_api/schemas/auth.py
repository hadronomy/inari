from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import Field

from .base import APIModel
from ...security.local_trust import (
    LocalChallenge,
    LocalChallengePurpose,
    LocalClientAttestation,
    LocalTrustLevel,
    LocalTrustState,
    TrustedLocalClient,
)
from ...security.models import (
    AccessScope,
    AuthenticatedPrincipal,
    IssuedToken,
    PrincipalKind,
)


class LocalClientAttestationInput(APIModel):
    client_id: str
    challenge_id: str
    signature: str
    origin: str | None = None

    def to_domain(self) -> LocalClientAttestation:
        return LocalClientAttestation(
            client_id=self.client_id,
            challenge_id=self.challenge_id,
            signature=self.signature,
            origin=self.origin,
        )


class LocalTokenRequest(APIModel):
    client_name: str = "local-client"
    requested_scopes: tuple[AccessScope, ...] | None = None
    attestation: LocalClientAttestationInput | None = None


class LocalChallengeRequest(APIModel):
    purpose: LocalChallengePurpose
    client_id: str | None = None


class LocalChallengeResponse(APIModel):
    challenge_id: str
    challenge: str
    purpose: LocalChallengePurpose
    expires_at: datetime

    @classmethod
    def from_challenge(cls, challenge: LocalChallenge) -> Self:
        return cls(
            challenge_id=challenge.id,
            challenge=challenge.challenge,
            purpose=challenge.purpose,
            expires_at=challenge.expires_at,
        )


class LocalPairingStartResponse(APIModel):
    pairing_secret: str
    expires_at: datetime


class LocalPairingCompleteRequest(APIModel):
    client_id: str
    client_name: str = "local-client"
    public_key_pem: str
    pairing_secret: str
    attestation: LocalClientAttestationInput
    origin: str | None = None


class TrustedLocalClientResponse(APIModel):
    client_id: str
    client_name: str
    trust_level: LocalTrustLevel
    origins: tuple[str, ...] = Field(
        description="Trusted browser origins for this local client. Native clients usually have none."
    )
    paired_at: datetime
    last_seen_at: datetime | None = None

    @classmethod
    def from_domain(cls, client: TrustedLocalClient) -> Self:
        return cls(
            client_id=client.client_id,
            client_name=client.client_name,
            trust_level=client.trust_level,
            origins=tuple(client.origins),
            paired_at=client.paired_at,
            last_seen_at=client.last_seen_at,
        )


class LocalPairingCompleteResponse(APIModel):
    client: TrustedLocalClientResponse


class LocalTrustStatusResponse(APIModel):
    pairing_required: bool
    paired: bool
    active_pairing_expires_at: datetime | None = None
    trusted_clients: tuple[TrustedLocalClientResponse, ...]

    @classmethod
    def from_state(cls, state: LocalTrustState, *, pairing_required: bool) -> Self:
        return cls(
            pairing_required=pairing_required,
            paired=state.paired,
            active_pairing_expires_at=(
                state.pairing_secret.expires_at
                if state.pairing_secret is not None
                else None
            ),
            trusted_clients=tuple(
                TrustedLocalClientResponse.from_domain(client)
                for client in state.trusted_clients
            ),
        )


class LocalPairingRevokeRequest(APIModel):
    client_id: str


class TokenResponse(APIModel):
    access_token: str
    token_type: str
    expires_at: datetime
    scopes: tuple[AccessScope, ...]
    subject: str
    principal_kind: PrincipalKind

    @classmethod
    def from_issued_token(cls, token: IssuedToken) -> Self:
        return cls(
            access_token=token.access_token,
            token_type=token.token_type,
            expires_at=token.expires_at,
            scopes=token.scopes,
            subject=token.subject,
            principal_kind=token.principal_kind,
        )


class PrincipalResponse(APIModel):
    subject: str
    principal_kind: PrincipalKind
    scopes: tuple[AccessScope, ...]
    issuer: str
    audience: str
    token_id: str | None = None
    expires_at: datetime | None = None

    @classmethod
    def from_principal(cls, principal: AuthenticatedPrincipal) -> Self:
        return cls(
            subject=principal.subject,
            principal_kind=principal.principal_kind,
            scopes=tuple(sorted(principal.scopes, key=lambda item: item.value)),
            issuer=principal.issuer,
            audience=principal.audience,
            token_id=principal.token_id,
            expires_at=principal.expires_at,
        )


class AuthenticatedPrincipalResponse(APIModel):
    principal: PrincipalResponse
