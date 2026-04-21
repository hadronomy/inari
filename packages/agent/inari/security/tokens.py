from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Any, Iterable

from joserfc import jwt
from joserfc.jwk import OctKey

from ..core.exceptions import AgentError
from .identity import AgentIdentityService
from .models import AccessScope, AuthenticatedPrincipal, IssuedToken, PrincipalKind
from .secrets import SecretStore

LOCAL_SIGNING_SECRET_KEY = "local_signing_secret"


class TokenService:
    def __init__(
        self,
        *,
        secret_store: SecretStore,
        identity_service: AgentIdentityService,
        token_ttl_seconds: int,
        token_audience: str,
        token_issuer: str | None = None,
    ) -> None:
        self.secret_store = secret_store
        self.identity_service = identity_service
        self.token_ttl_seconds = token_ttl_seconds
        self.token_audience = token_audience
        self.token_issuer = token_issuer

    def issue_local_token(
        self,
        *,
        client_name: str,
        scopes: Iterable[AccessScope],
        principal_kind: PrincipalKind = PrincipalKind.LOCAL_CLIENT,
        metadata: Mapping[str, str] | None = None,
    ) -> IssuedToken:
        issued_at = utc_now()
        expires_at = issued_at + timedelta(seconds=self.token_ttl_seconds)
        normalized_scopes = tuple(sorted(set(scopes), key=lambda item: item.value))
        claims = {
            "iss": self.issuer,
            "aud": self.token_audience,
            "sub": f"local:{client_name}",
            "scope": [scope.value for scope in normalized_scopes],
            "kind": principal_kind.value,
            "iat": int(issued_at.timestamp()),
            "nbf": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": token_urlsafe(12),
        }
        if metadata:
            claims.update(metadata)
        token = jwt.encode({"alg": "HS256", "typ": "JWT"}, claims, self._signing_key())
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return IssuedToken(
            access_token=token,
            expires_at=expires_at,
            scopes=normalized_scopes,
            subject=str(claims["sub"]),
            principal_kind=principal_kind,
        )

    def authenticate_token(self, token: str) -> AuthenticatedPrincipal:
        try:
            claims_set = jwt.decode(token, self._signing_key(), algorithms=["HS256"])
        except Exception as exc:  # pragma: no cover - library error surface
            raise AgentError(
                "INVALID_ACCESS_TOKEN",
                "The supplied access token is invalid.",
                status_code=401,
            ) from exc
        claims = _claims_mapping(claims_set)
        self._validate_claims(claims)
        scopes = frozenset(AccessScope(value) for value in claims.get("scope", []))
        principal_kind = PrincipalKind(
            str(claims.get("kind", PrincipalKind.API_CLIENT.value))
        )
        expires_at = _as_datetime(claims.get("exp"))
        return AuthenticatedPrincipal(
            subject=str(claims.get("sub", "")),
            principal_kind=principal_kind,
            scopes=scopes,
            issuer=str(claims.get("iss", "")),
            audience=_normalize_audience(claims.get("aud")),
            token_id=str(claims["jti"]) if claims.get("jti") is not None else None,
            expires_at=expires_at,
            metadata={"claims": claims},
        )

    @property
    def issuer(self) -> str:
        if self.token_issuer:
            return self.token_issuer
        return f"inari://{self.identity_service.get_or_create_identity().agent_id}"

    def _signing_key(self) -> OctKey:
        secret = self.secret_store.get_secret(LOCAL_SIGNING_SECRET_KEY)
        if secret is None:
            secret = token_urlsafe(48)
            self.secret_store.set_secret(LOCAL_SIGNING_SECRET_KEY, secret)
        return OctKey.import_key(secret)

    def _validate_claims(self, claims: dict[str, Any]) -> None:
        issuer = str(claims.get("iss", ""))
        if issuer != self.issuer:
            raise AgentError(
                "INVALID_ACCESS_TOKEN",
                "The access token issuer is not trusted.",
                status_code=401,
            )
        audience = _normalize_audience(claims.get("aud"))
        if audience != self.token_audience:
            raise AgentError(
                "INVALID_ACCESS_TOKEN",
                "The access token audience is invalid.",
                status_code=401,
            )
        now = utc_now()
        expires_at = _as_datetime(claims.get("exp"))
        if expires_at is None or expires_at <= now:
            raise AgentError(
                "ACCESS_TOKEN_EXPIRED", "The access token has expired.", status_code=401
            )
        not_before = _as_datetime(claims.get("nbf"))
        if not_before is not None and now < not_before:
            raise AgentError(
                "INVALID_ACCESS_TOKEN",
                "The access token is not active yet.",
                status_code=401,
            )


def _claims_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    claims = getattr(value, "claims", None)
    if isinstance(claims, Mapping):
        return {str(key): item for key, item in claims.items()}
    raise AgentError(
        "INVALID_ACCESS_TOKEN",
        "The supplied access token claims could not be decoded.",
        status_code=401,
    )


def _as_datetime(value: datetime | int | float | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    return datetime.fromtimestamp(int(value), tz=UTC)


def _normalize_audience(value: object) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value)


def utc_now() -> datetime:
    return datetime.now(tz=UTC)
