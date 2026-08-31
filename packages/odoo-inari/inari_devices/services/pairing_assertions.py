from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

import requests


_ASSERTION_TYPE = "application/inari-pairing-assertion+jws"
_ALGORITHM = "Ed25519"
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,127}\Z")
_SAFE_DATABASE = re.compile(r"[^a-z0-9]+")


class PairingSigningError(RuntimeError):
    """A content-free failure at the Pairing Assertion signing boundary."""


@dataclass(frozen=True, slots=True)
class SignedPairingAssertion:
    compact_jws: str
    signer_key_id: str


def _exact_https_origin(value: str, *, name: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise PairingSigningError(f"{name} is invalid.") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PairingSigningError(f"{name} must be an exact HTTPS origin.")
    host = parsed.hostname.lower().rstrip(".")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = host if port in {None, 443} else f"{host}:{port}"
    return urlunsplit(("https", authority, "", "", ""))


def _bounded_name(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise PairingSigningError(f"{name} is invalid.")
    return value


def _base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PairingSigningError("The Pairing Assertion claims are invalid.") from exc


def pairing_signing_key_name(database: str, company_id: int) -> str:
    """Return the deterministic per-database, per-company Transit key name."""

    safe_database = _SAFE_DATABASE.sub("-", database.lower()).strip("-")
    if not safe_database:
        raise PairingSigningError("The Odoo database name is invalid.")
    return _bounded_name(
        f"inari-odoo-pairing-{safe_database[:80]}-{company_id}",
        name="Pairing signing key name",
    )


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
        self._role = _bounded_name(role, name="OpenBao Kubernetes role")
        self._auth_mount = _bounded_name(
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
            workload_jwt = self._token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PairingSigningError(
                "The Odoo workload identity is not available."
            ) from exc
        if not workload_jwt or len(workload_jwt) > 32_768:
            raise PairingSigningError("The Odoo workload identity is invalid.")
        response = self._client.request(
            "POST",
            f"/v1/auth/{quote(self._auth_mount, safe='')}/login",
            json_body={"role": self._role, "jwt": workload_jwt},
        )
        auth = response.get("auth")
        if not isinstance(auth, Mapping):
            raise PairingSigningError("OpenBao returned an invalid login response.")
        token = auth.get("client_token")
        lease_duration = auth.get("lease_duration")
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(lease_duration, int)
            or isinstance(lease_duration, bool)
            or lease_duration <= 0
        ):
            raise PairingSigningError("OpenBao returned an invalid login response.")
        self._token = token
        self._refresh_at = now + max(1, min(lease_duration * 0.8, lease_duration - 5))
        return token


class OpenBaoClient:
    """Small authenticated JSON client for the exact Transit operations we use."""

    def __init__(
        self,
        *,
        address: str,
        session: requests.Session | None = None,
        ca_certificate: str | bool = True,
        client_certificate: tuple[str, str] | None = None,
        namespace: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.address = _exact_https_origin(address, name="OpenBao address")
        self._session = session or requests.Session()
        self._verify = ca_certificate
        self._certificate = client_certificate
        self._namespace = namespace
        self._timeout = timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        headers = {"Accept": "application/json"}
        if token:
            headers["X-Vault-Token"] = token
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        try:
            response = self._session.request(
                method,
                f"{self.address}{path}",
                headers=headers,
                json=json_body,
                timeout=self._timeout,
                verify=self._verify,
                cert=self._certificate,
            )
        except requests.RequestException as exc:
            raise PairingSigningError("OpenBao is not available.") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise PairingSigningError("OpenBao rejected the signing operation.")
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise PairingSigningError("OpenBao returned an invalid response.") from exc
        if not isinstance(payload, Mapping):
            raise PairingSigningError("OpenBao returned an invalid response.")
        return payload


class PairingAssertionSigner:
    """Create strict compact Pairing Assertions with one OpenBao Transit key."""

    def __init__(
        self,
        *,
        client: OpenBaoClient,
        token_provider: KubernetesTokenProvider,
        transit_mount: str,
        key_name: str,
    ) -> None:
        self._client = client
        self._tokens = token_provider
        self._transit_mount = _bounded_name(transit_mount, name="OpenBao Transit mount")
        self._key_name = _bounded_name(key_name, name="OpenBao Transit key")

    def sign(self, claims: Mapping[str, Any]) -> SignedPairingAssertion:
        token = self._tokens.token()
        version = self._latest_key_version(token)
        key_id = f"{self._key_name}:v{version}"
        header = {
            "alg": _ALGORITHM,
            "kid": key_id,
            "typ": _ASSERTION_TYPE,
        }
        signing_input = b".".join(
            (
                _base64url(_json_bytes(header)).encode(),
                _base64url(_json_bytes(claims)).encode(),
            )
        )
        response = self._client.request(
            "POST",
            self._transit_path("sign"),
            token=token,
            json_body={
                "input": _base64(signing_input),
                "key_version": version,
                "prehashed": False,
            },
        )
        data = response.get("data")
        signature = data.get("signature") if isinstance(data, Mapping) else None
        raw_signature = self._signature_bytes(signature, version)
        return SignedPairingAssertion(
            compact_jws=f"{signing_input.decode('ascii')}.{_base64url(raw_signature)}",
            signer_key_id=key_id,
        )

    def _latest_key_version(self, token: str) -> int:
        response = self._client.request("GET", self._transit_path("keys"), token=token)
        data = response.get("data")
        version = data.get("latest_version") if isinstance(data, Mapping) else None
        keys = data.get("keys") if isinstance(data, Mapping) else None
        key_type = data.get("type") if isinstance(data, Mapping) else None
        supports_signing = (
            data.get("supports_signing") if isinstance(data, Mapping) else None
        )
        key = keys.get(str(version)) if isinstance(keys, Mapping) else None
        public_key = key.get("public_key") if isinstance(key, Mapping) else None
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version <= 0
            or key_type != "ed25519"
            or supports_signing is not True
            or not isinstance(public_key, str)
            or not public_key
        ):
            raise PairingSigningError("The OpenBao signing key metadata is invalid.")
        return version

    def _transit_path(self, operation: str) -> str:
        mount = quote(self._transit_mount, safe="")
        key = quote(self._key_name, safe="")
        return f"/v1/{mount}/{operation}/{key}"

    @staticmethod
    def _signature_bytes(value: object, version: int) -> bytes:
        prefix = f"vault:v{version}:"
        if not isinstance(value, str) or not value.startswith(prefix):
            raise PairingSigningError("OpenBao returned an invalid signature.")
        try:
            signature = base64.b64decode(value.removeprefix(prefix), validate=True)
        except (ValueError, TypeError) as exc:
            raise PairingSigningError("OpenBao returned an invalid signature.") from exc
        if len(signature) != 64:
            raise PairingSigningError("OpenBao returned an invalid Ed25519 signature.")
        return signature


def build_pairing_assertion_signer(
    *, database: str, company_id: int
) -> PairingAssertionSigner:
    """Compose the signer from pod-only environment and workload identity files."""

    address = os.environ.get("INARI_OPENBAO_ADDR", "")
    role = os.environ.get("INARI_OPENBAO_KUBERNETES_ROLE", "")
    if not address or not role:
        raise PairingSigningError("Pairing Assertion signing is not configured.")
    certificate_file = os.environ.get("INARI_OPENBAO_CLIENT_CERT_FILE")
    key_file = os.environ.get("INARI_OPENBAO_CLIENT_KEY_FILE")
    if bool(certificate_file) != bool(key_file):
        raise PairingSigningError("OpenBao client certificate settings are incomplete.")
    ca_certificate: str | bool = os.environ.get("INARI_OPENBAO_CACERT", True)
    client = OpenBaoClient(
        address=address,
        ca_certificate=ca_certificate,
        client_certificate=(certificate_file, key_file)
        if certificate_file and key_file
        else None,
        namespace=os.environ.get("INARI_OPENBAO_NAMESPACE") or None,
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
    return PairingAssertionSigner(
        client=client,
        token_provider=tokens,
        transit_mount=os.environ.get("INARI_OPENBAO_TRANSIT_MOUNT", "transit"),
        key_name=pairing_signing_key_name(database, company_id),
    )
