from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from urllib.parse import quote, quote_plus, urlencode, urlsplit, urlunsplit

from requests.auth import HTTPBasicAuth

from .http_client import JsonHttpClient, RemoteServiceError, https_url
from .openbao import KubernetesTokenProvider, OpenBaoClient, openbao_name


@dataclass(frozen=True, slots=True)
class WorkloadScope:
    database: str
    company_id: str
    organization_id: str

    def __post_init__(self) -> None:
        for value, limit in (
            (self.database, 128),
            (self.company_id, 64),
            (self.organization_id, 128),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= limit
                or any(ord(character) < 32 for character in value)
            ):
                raise RemoteServiceError(
                    "The Organization Workload Identity scope is invalid."
                )

    def matches(self, value: object) -> bool:
        return isinstance(value, dict) and all(
            value.get(field) == getattr(self, field)
            for field in ("database", "company_id", "organization_id")
        )


def credential_path(scope: WorkloadScope) -> str:
    database_digest = hashlib.sha256(scope.database.encode()).hexdigest()
    return f"inari/odoo/{database_digest}/companies/{quote(scope.company_id, safe='')}/workload"


class WorkloadTokenProvider:
    """Keep one company's credential and short-lived access token in memory."""

    def __init__(
        self,
        *,
        scope: WorkloadScope,
        issuer: str,
        client_id: str,
        openbao: OpenBaoClient,
        openbao_tokens: KubernetesTokenProvider,
        http: JsonHttpClient,
        credential_mount: str = "secret",
        clock=time.monotonic,
    ) -> None:
        self.scope = scope
        self._issuer = https_url(issuer)
        issuer_parts = urlsplit(self._issuer)
        if http.address != urlunsplit(("https", issuer_parts.netloc, "", "", "")):
            raise RemoteServiceError("The OIDC client does not match its issuer.")
        if not client_id or len(client_id) > 256:
            raise RemoteServiceError(
                "The Organization Workload Identity client is invalid."
            )
        self._client_id = client_id
        self._openbao = openbao
        self._openbao_tokens = openbao_tokens
        self._http = http
        self._mount = openbao_name(credential_mount, name="OpenBao credential mount")
        self._clock = clock
        self._access_token: str | None = None
        self._refresh_at = 0.0

    def close(self) -> None:
        self._http.close()
        self._openbao.close()

    def invalidate(self) -> None:
        self._access_token = None
        self._refresh_at = 0.0

    def token(self) -> str:
        if self._access_token and self._clock() < self._refresh_at:
            return self._access_token
        self.invalidate()
        discovery = self._http.request(
            "GET",
            urlsplit(self._issuer).path.rstrip("/")
            + "/.well-known/openid-configuration",
        )
        if discovery.get("issuer") != self._issuer:
            raise RemoteServiceError(
                "OIDC discovery does not match the configured issuer."
            )
        endpoint = discovery.get("token_endpoint")
        if not isinstance(endpoint, str):
            raise RemoteServiceError("OIDC discovery has no token endpoint.")
        endpoint = https_url(endpoint)
        endpoint_parts = urlsplit(endpoint)
        if (
            urlunsplit(("https", endpoint_parts.netloc, "", "", ""))
            != self._http.address
        ):
            raise RemoteServiceError(
                "The OIDC token endpoint is outside its issuer origin."
            )
        methods = discovery.get(
            "token_endpoint_auth_methods_supported", ["client_secret_basic"]
        )
        if not isinstance(methods, list) or "client_secret_basic" not in methods:
            raise RemoteServiceError(
                "OIDC client-secret authentication is not supported."
            )
        for attempt in range(2):
            try:
                secret = self._openbao.request(
                    "GET",
                    f"/v1/{self._mount}/data/{credential_path(self.scope)}",
                    token=self._openbao_tokens.token(),
                ).get("data")
                break
            except RemoteServiceError as error:
                if error.status not in {401, 403} or attempt:
                    raise
                self._openbao_tokens.invalidate()
        secret = secret.get("data") if isinstance(secret, dict) else None
        if (
            not isinstance(secret, dict)
            or not self.scope.matches(secret)
            or secret.get("client_id") != self._client_id
            or secret.get("issuer_url") != self._issuer
        ):
            raise RemoteServiceError(
                "The OpenBao credential does not match this company identity."
            )
        password = secret.get("client_secret")
        scopes = secret.get("scopes")
        if not isinstance(password, str) or not 1 <= len(password) <= 8192:
            raise RemoteServiceError("The OpenBao workload credential is invalid.")
        if (
            not isinstance(scopes, list)
            or not 1 <= len(scopes) <= 16
            or any(
                not isinstance(scope, str)
                or not 1 <= len(scope) <= 256
                or any(character.isspace() for character in scope)
                for scope in scopes
            )
            or not {"managed_work:read", "managed_work:write"}.issubset(scopes)
        ):
            raise RemoteServiceError("The workload credential scopes are invalid.")
        # RFC 6749 encodes each credential before HTTP Basic authentication.
        auth = HTTPBasicAuth(quote_plus(self._client_id), quote_plus(password))
        started_at = self._clock()
        response = self._http.request(
            "POST",
            endpoint_parts.path or "/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=urlencode(
                {"grant_type": "client_credentials", "scope": " ".join(scopes)}
            ),
            auth=auth,
        )
        access_token = response.get("access_token")
        lifetime = response.get("expires_in")
        token_type = response.get("token_type")
        if (
            not isinstance(token_type, str)
            or token_type.lower() != "bearer"
            or not isinstance(access_token, str)
            or not 1 <= len(access_token) <= 32768
            or any(character.isspace() for character in access_token)
            or type(lifetime) is not int
            or not 5 < lifetime <= 900
        ):
            raise RemoteServiceError(
                "OIDC returned an invalid short-lived access token."
            )
        self._refresh_at = started_at + lifetime - min(30, lifetime / 5)
        if self._clock() >= self._refresh_at:
            raise RemoteServiceError(
                "The OIDC access token expired during authentication."
            )
        self._access_token = access_token
        return access_token
