from __future__ import annotations

from datetime import datetime

from .base import APIModel
from ...security.models import (
    AccessScope,
    AuthenticatedPrincipal,
    IssuedToken,
    PrincipalKind,
)


class LocalTokenRequest(APIModel):
    client_name: str = "local-client"
    requested_scopes: tuple[AccessScope, ...] | None = None


class TokenResponse(APIModel):
    access_token: str
    token_type: str
    expires_at: datetime
    scopes: tuple[AccessScope, ...]
    subject: str
    principal_kind: PrincipalKind

    @classmethod
    def from_issued_token(cls, token: IssuedToken) -> TokenResponse:
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
    def from_principal(cls, principal: AuthenticatedPrincipal) -> PrincipalResponse:
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
