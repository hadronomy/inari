from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

from .http_client import RemoteServiceError
from .openbao import (
    KubernetesTokenProvider,
    OpenBaoClient,
    build_openbao_client,
    openbao_name,
)


_ASSERTION_TYPE = "application/inari-pairing-assertion+jws"
_ALGORITHM = "Ed25519"
_SAFE_DATABASE = re.compile(r"[^a-z0-9]+")


class PairingSigningError(RemoteServiceError):
    """A content-free failure at the Pairing Assertion signing boundary."""


@dataclass(frozen=True, slots=True)
class SignedPairingAssertion:
    compact_jws: str
    signer_key_id: str


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
    return openbao_name(
        f"inari-odoo-pairing-{safe_database[:80]}-{company_id}",
        name="Pairing signing key name",
    )


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
        self._transit_mount = openbao_name(transit_mount, name="OpenBao Transit mount")
        self._key_name = openbao_name(key_name, name="OpenBao Transit key")

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

    client, tokens = build_openbao_client()
    return PairingAssertionSigner(
        client=client,
        token_provider=tokens,
        transit_mount=os.environ.get("INARI_OPENBAO_TRANSIT_MOUNT", "transit"),
        key_name=pairing_signing_key_name(database, company_id),
    )
