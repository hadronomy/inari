from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol

import httpx
from joserfc import jwk, jwt

from ..config import AgentSettings
from ..exceptions import AgentError
from .models import GatewayEnrollmentRecord, UpstreamAuthMode

ZITADEL_ASSERTION_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"


@dataclass(slots=True, frozen=True)
class UpstreamAuthorization:
    access_token: str
    token_type: str = "Bearer"
    expires_at: datetime | None = None

    def as_headers(self) -> dict[str, str]:
        return {"Authorization": f"{self.token_type} {self.access_token}"}


class UpstreamAuthProvider(Protocol):
    mode: UpstreamAuthMode

    async def headers_for_enrollment(self) -> dict[str, str]: ...

    async def headers_for_upstream(
        self, enrollment: GatewayEnrollmentRecord | None
    ) -> dict[str, str]: ...

    async def invalidate(self) -> None: ...


class ControllerAccessTokenAuthProvider:
    mode = UpstreamAuthMode.CONTROLLER

    async def headers_for_enrollment(self) -> dict[str, str]:
        return {}

    async def headers_for_upstream(
        self, enrollment: GatewayEnrollmentRecord | None
    ) -> dict[str, str]:
        if enrollment is None or enrollment.access_token is None:
            return {}
        return UpstreamAuthorization(access_token=enrollment.access_token).as_headers()

    async def invalidate(self) -> None:
        return None


@dataclass(slots=True, frozen=True)
class ZitadelServiceAccountCredentials:
    user_id: str
    key_id: str
    private_key_pem: str

    @classmethod
    def from_settings(cls, settings: AgentSettings) -> ZitadelServiceAccountCredentials:
        key_file = settings.zitadel_service_account_key_path
        if key_file is not None:
            payload = json.loads(key_file.read_text(encoding="utf-8"))
            return cls(
                user_id=str(payload["userId"]),
                key_id=str(payload["keyId"]),
                private_key_pem=str(payload["key"]),
            )

        if (
            settings.zitadel_service_user_id is None
            or settings.zitadel_key_id is None
            or settings.zitadel_private_key_path is None
        ):
            raise RuntimeError(
                "ZITADEL auth mode requires either a service-account key JSON file or explicit user, key id, and private key path settings."
            )
        return cls(
            user_id=settings.zitadel_service_user_id,
            key_id=settings.zitadel_key_id,
            private_key_pem=settings.zitadel_private_key_path.read_text(
                encoding="utf-8"
            ),
        )


class ZitadelServiceAccountAuthProvider:
    mode = UpstreamAuthMode.ZITADEL_SERVICE_ACCOUNT

    def __init__(
        self,
        *,
        settings: AgentSettings,
        http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.settings = settings
        self._http_client_factory = http_client_factory or httpx.AsyncClient
        self._credentials = ZitadelServiceAccountCredentials.from_settings(settings)
        self._cached: UpstreamAuthorization | None = None
        self._lock = asyncio.Lock()

    async def headers_for_enrollment(self) -> dict[str, str]:
        return (await self._authorization()).as_headers()

    async def headers_for_upstream(
        self, enrollment: GatewayEnrollmentRecord | None
    ) -> dict[str, str]:
        return (await self._authorization()).as_headers()

    async def invalidate(self) -> None:
        async with self._lock:
            self._cached = None

    async def _authorization(self) -> UpstreamAuthorization:
        async with self._lock:
            if self._cached is not None and not _is_expiring(
                self._cached.expires_at,
                skew_seconds=self.settings.zitadel_token_refresh_skew_seconds,
            ):
                return self._cached
            authorization = await self._request_access_token()
            self._cached = authorization
            return authorization

    async def _request_access_token(self) -> UpstreamAuthorization:
        token_url = self._token_url()
        audience = self.settings.zitadel_audience or self.settings.zitadel_base_url
        if not token_url or not audience:
            raise RuntimeError(
                "ZITADEL auth mode requires a base URL or explicit token URL and audience."
            )

        issued_at = _utc_now()
        expires_at = issued_at + timedelta(minutes=5)
        assertion = jwt.encode(
            {
                "alg": self.settings.zitadel_assertion_algorithm,
                "kid": self._credentials.key_id,
            },
            {
                "iss": self._credentials.user_id,
                "sub": self._credentials.user_id,
                "aud": audience,
                "iat": int(issued_at.timestamp()),
                "exp": int(expires_at.timestamp()),
            },
            jwk.import_key(self._credentials.private_key_pem, "RSA"),
            algorithms=[self.settings.zitadel_assertion_algorithm],
        )
        scopes = tuple(
            dict.fromkeys(("openid", *self.settings.zitadel_requested_scopes))
        )
        async with self._http_client_factory(
            timeout=self.settings.gateway_reconnect_delay_seconds
        ) as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": ZITADEL_ASSERTION_GRANT_TYPE,
                    "scope": " ".join(scopes),
                    "assertion": assertion,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            payload = response.json()
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise AgentError(
                "ZITADEL_TOKEN_EXCHANGE_FAILED",
                "ZITADEL did not return an access token for the service account assertion.",
                status_code=502,
            )
        expires_in = int(payload.get("expires_in") or 300)
        return UpstreamAuthorization(
            access_token=access_token,
            token_type=str(payload.get("token_type") or "Bearer"),
            expires_at=_utc_now() + timedelta(seconds=max(expires_in, 1)),
        )

    def _token_url(self) -> str | None:
        if self.settings.zitadel_token_url:
            return self.settings.zitadel_token_url
        if self.settings.zitadel_base_url:
            return f"{self.settings.zitadel_base_url}/oauth/v2/token"
        return None


def build_upstream_auth_provider(
    settings: AgentSettings,
    *,
    http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
) -> UpstreamAuthProvider:
    if settings.upstream_auth_mode is UpstreamAuthMode.ZITADEL_SERVICE_ACCOUNT:
        return ZitadelServiceAccountAuthProvider(
            settings=settings,
            http_client_factory=http_client_factory,
        )
    return ControllerAccessTokenAuthProvider()


def _is_expiring(expires_at: datetime | None, *, skew_seconds: int) -> bool:
    if expires_at is None:
        return False
    return expires_at <= _utc_now() + timedelta(seconds=skew_seconds)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
