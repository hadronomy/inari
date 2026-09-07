from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from .http_client import JsonHttpClient, RemoteServiceError


_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,127}\Z")


def openbao_name(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise RemoteServiceError(f"{name} is invalid.")
    return value


class OpenBaoClient:
    def __init__(self, http: JsonHttpClient, *, namespace: str | None = None) -> None:
        self._http = http
        self._namespace = namespace

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        headers = {}
        if token:
            headers["X-Vault-Token"] = token
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        return self._http.request(method, path, headers=headers, json_body=json_body)

    def close(self) -> None:
        self._http.close()


class KubernetesTokenProvider:
    """Exchange the Odoo pod identity for a short-lived OpenBao token."""

    def __init__(
        self,
        *,
        client: OpenBaoClient,
        role: str,
        auth_mount: str,
        service_account_token_file: Path,
        clock: Any = time.monotonic,
    ) -> None:
        self._client = client
        self._role = openbao_name(role, name="OpenBao Kubernetes role")
        self._auth_mount = openbao_name(
            auth_mount, name="OpenBao Kubernetes auth mount"
        )
        self._token_file = service_account_token_file
        self._clock = clock
        self._token: str | None = None
        self._refresh_at = 0.0

    def token(self) -> str:
        now = self._clock()
        if self._token and now < self._refresh_at:
            return self._token
        try:
            with self._token_file.open(encoding="utf-8") as token_file:
                workload_jwt = token_file.read(32_769).strip()
        except (OSError, UnicodeError) as exc:
            raise RemoteServiceError(
                "The Odoo workload identity is not available."
            ) from exc
        if not workload_jwt or len(workload_jwt) > 32_768:
            raise RemoteServiceError("The Odoo workload identity is invalid.")
        response = self._client.request(
            "POST",
            f"/v1/auth/{quote(self._auth_mount, safe='')}/login",
            json_body={"role": self._role, "jwt": workload_jwt},
        )
        auth = response.get("auth")
        if not isinstance(auth, Mapping):
            raise RemoteServiceError("OpenBao returned an invalid login response.")
        token = auth.get("client_token")
        lease_duration = auth.get("lease_duration")
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(lease_duration, int)
            or isinstance(lease_duration, bool)
            or lease_duration <= 0
        ):
            raise RemoteServiceError("OpenBao returned an invalid login response.")
        self._token = token
        self._refresh_at = now + max(0, min(lease_duration * 0.8, lease_duration - 5))
        return token

    def invalidate(self) -> None:
        self._token = None
        self._refresh_at = 0


def build_openbao_client() -> tuple[OpenBaoClient, KubernetesTokenProvider]:
    address = os.environ.get("INARI_OPENBAO_ADDR", "")
    role = os.environ.get("INARI_OPENBAO_KUBERNETES_ROLE", "")
    if not address or not role:
        raise RemoteServiceError("OpenBao workload authentication is not configured.")
    certificate = os.environ.get("INARI_OPENBAO_CLIENT_CERT_FILE")
    private_key = os.environ.get("INARI_OPENBAO_CLIENT_KEY_FILE")
    if bool(certificate) != bool(private_key):
        raise RemoteServiceError("OpenBao client certificate settings are incomplete.")
    http = JsonHttpClient(
        address,
        ca_certificate=os.environ.get("INARI_OPENBAO_CACERT", True),
        client_certificate=(certificate, private_key)
        if certificate and private_key
        else None,
        response_limit=65536,
    )
    client = OpenBaoClient(
        http, namespace=os.environ.get("INARI_OPENBAO_NAMESPACE") or None
    )
    tokens = KubernetesTokenProvider(
        client=client,
        role=role,
        auth_mount=os.environ.get("INARI_OPENBAO_AUTH_MOUNT", "kubernetes"),
        service_account_token_file=Path(
            os.environ.get(
                "INARI_OPENBAO_SERVICE_ACCOUNT_TOKEN_FILE",
                "/var/run/secrets/kubernetes.io/serviceaccount/token",
            )
        ),
    )
    return client, tokens
