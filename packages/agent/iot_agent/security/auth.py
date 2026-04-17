from __future__ import annotations

from collections.abc import Iterable

from starlette.requests import HTTPConnection

from ..exceptions import AgentError, ErrorSourcePayload
from .models import (
    AccessScope,
    AuthenticatedPrincipal,
    IssuedToken,
    LOCAL_OPERATOR_SCOPES,
)
from .policies import SecurityPolicyService
from .tokens import TokenService


class AuthorizationService:
    def __init__(
        self,
        *,
        token_service: TokenService,
        policy_service: SecurityPolicyService,
    ) -> None:
        self.token_service = token_service
        self.policy_service = policy_service

    def issue_loopback_token(
        self,
        connection: HTTPConnection,
        *,
        client_name: str,
        requested_scopes: Iterable[AccessScope] | None = None,
    ) -> IssuedToken:
        self.policy_service.assert_loopback_client(connection)
        allowed = set(LOCAL_OPERATOR_SCOPES)
        requested = set(requested_scopes or LOCAL_OPERATOR_SCOPES)
        scopes = tuple(sorted(allowed & requested, key=lambda scope: scope.value))
        if not scopes:
            raise AgentError(
                "INVALID_SCOPE_REQUEST",
                "No supported scopes were requested for the local token.",
                status_code=400,
            )
        return self.token_service.issue_local_token(
            client_name=client_name, scopes=scopes
        )

    def authenticate_connection(
        self, connection: HTTPConnection
    ) -> AuthenticatedPrincipal:
        token = extract_bearer_token(connection)
        if token is None:
            raise AgentError(
                "AUTHENTICATION_REQUIRED",
                "A bearer access token is required for this endpoint.",
                status_code=401,
                source=ErrorSourcePayload(header="Authorization"),
            )
        return self.token_service.authenticate_token(token)

    def require_scopes(
        self,
        principal: AuthenticatedPrincipal,
        required_scopes: Iterable[AccessScope],
    ) -> AuthenticatedPrincipal:
        required = tuple(required_scopes)
        if not principal.has_scopes(required):
            raise AgentError(
                "INSUFFICIENT_SCOPE",
                "The access token does not include the required scopes for this endpoint.",
                status_code=403,
                details={
                    "required_scopes": [scope.value for scope in required],
                    "granted_scopes": [
                        scope.value
                        for scope in sorted(
                            principal.scopes, key=lambda scope: scope.value
                        )
                    ],
                },
            )
        return principal


def extract_bearer_token(connection: HTTPConnection) -> str | None:
    authorization = connection.headers.get("authorization")
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.casefold() == "bearer" and value.strip():
            return value.strip()
    query_token = connection.query_params.get("access_token")
    if query_token:
        return query_token.strip()
    return None
