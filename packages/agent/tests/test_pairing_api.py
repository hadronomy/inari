from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient, Response
from joserfc import jwk, jwt
from joserfc.jwk import OKPKey

from inari.application.container import AgentContainer
from inari.client_trust import (
    AccessTokenSigner,
    AccessTokenVerifier,
    BusinessScope,
    ClientTrustService,
    DPoPProofVerifier,
    PairingAssertionClaims,
    PairingAssertionSigner,
    PairingAssertionVerifier,
    Permission,
    RenewalDPoPProofVerifier,
    SqliteClientTrustStore,
    public_ed25519_jwk,
)
from inari.config import AgentSettings
from inari.db import DatabaseMigrator
from inari.local_api.app import create_app
from inari.security.models import AccessScope


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
AGENT_ORIGIN = "https://agent.example:7443"
ODOO_ORIGIN = "https://odoo.example"


@dataclass(slots=True)
class Clock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


class Supervisor:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class IdentityService:
    def get_or_create_identity(self):
        return SimpleNamespace(agent_id="agent_1")


class DeviceCenterAuthorization:
    def authenticate_connection(self, connection):
        assert connection.headers["authorization"] == "Bearer device-center"
        return SimpleNamespace(scopes=frozenset({AccessScope.ADMIN_WRITE}))

    def require_scopes(self, principal, scopes):
        assert set(scopes).issubset(principal.scopes)
        return principal


def _proof(
    key: OKPKey,
    *,
    method: str,
    uri: str,
    nonce: str,
    jti: str,
) -> str:
    return jwt.encode(
        {
            "typ": "dpop+jwt",
            "alg": "Ed25519",
            "jwk": public_ed25519_jwk(key),
        },
        {
            "htm": method,
            "htu": uri,
            "iat": int(NOW.timestamp()),
            "nonce": nonce,
            "jti": jti,
        },
        key,
        algorithms=["Ed25519"],
    )


def _headers(proof: str) -> dict[str, str]:
    return {"Origin": ODOO_ORIGIN, "DPoP": proof}


async def _retry_with_nonce(
    client: AsyncClient,
    *,
    method: str,
    path: str,
    browser_key: OKPKey,
    first_jti: str,
    retry_jti: str,
    json: dict[str, Any] | None = None,
) -> Response:
    uri = f"{AGENT_ORIGIN}{path}"
    first = await client.request(
        method,
        path,
        headers=_headers(
            _proof(
                browser_key,
                method=method,
                uri=uri,
                nonce="request-a-nonce",
                jti=first_jti,
            )
        ),
        json=json,
    )
    assert first.status_code == 401, first.text
    assert first.headers["www-authenticate"] == (
        'DPoP realm="inari", error="use_dpop_nonce"'
    )
    exposed = {
        header.strip().lower()
        for header in first.headers["access-control-expose-headers"].split(",")
    }
    assert {"www-authenticate", "dpop-nonce"}.issubset(exposed)
    nonce = first.headers["dpop-nonce"]
    return await client.request(
        method,
        path,
        headers=_headers(
            _proof(
                browser_key,
                method=method,
                uri=uri,
                nonce=nonce,
                jti=retry_jti,
            )
        ),
        json=json,
    )


@asynccontextmanager
async def _client(tmp_path, *, socket_address=False):
    database_path = tmp_path / "runtime.sqlite3"
    browser_assertion_key = jwk.generate_key("OKP", "Ed25519")
    agent_token_key = jwk.generate_key("OKP", "Ed25519")
    clock = Clock()
    trust = ClientTrustService(
        store=SqliteClientTrustStore(database_path),
        assertion_verifier=PairingAssertionVerifier(
            verification_keys={
                "odoo-pairing-v1": public_ed25519_jwk(browser_assertion_key)
            },
            issuer="odoo",
            audience="inari.local",
            agent_id="agent_1",
        ),
        access_token_verifier=AccessTokenVerifier(
            verification_key=public_ed25519_jwk(agent_token_key),
            issuer="agent_1",
            audience="inari.local",
        ),
        dpop_verifier=DPoPProofVerifier(),
        renewal_dpop_verifier=RenewalDPoPProofVerifier(),
        access_token_issuer=AccessTokenSigner(
            signing_key=agent_token_key,
            issuer="agent_1",
            audience="inari.local",
        ),
        clock=clock,
    )
    settings = AgentSettings(
        allowed_origins=[ODOO_ORIGIN],
        runtime_database_path=database_path,
        **(
            {
                "host": "127.0.0.1",
                "port": 7443,
                "agent_endpoint": AGENT_ORIGIN,
                "trusted_hosts": ["agent.example"],
                "tls_cert_path": tmp_path / "server.pem",
                "tls_key_path": tmp_path / "server-key.pem",
            }
            if socket_address
            else {}
        ),
    )
    container = cast(
        AgentContainer,
        SimpleNamespace(
            settings=settings,
            database_migrator=DatabaseMigrator(database_path),
            application_supervisor=Supervisor(),
            runtime_supervisor=Supervisor(),
            security_policy_service=None,
            device_work_authorizer=None,
            client_trust_service=trust,
            identity_service=IdentityService(),
            authorization_service=DeviceCenterAuthorization(),
        ),
    )
    app = create_app(container=container)

    async def listener(scope, receive, send):
        if socket_address and scope["type"] == "http":
            scope = {**scope, "server": ("127.0.0.1", 7443)}
        await app(scope, receive, send)

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=listener), base_url=AGENT_ORIGIN
        ) as client:
            yield client, trust, browser_assertion_key, clock


@pytest.mark.anyio
@pytest.mark.parametrize("socket_address", [False, True])
async def test_pairing_api_approves_admits_and_renews_one_browser_key(
    tmp_path,
    socket_address,
) -> None:
    browser_key = jwk.generate_key("OKP", "Ed25519")
    public = public_ed25519_jwk(browser_key)
    create_payload = {
        "browser_jwk": {name: public[name] for name in ("kty", "crv", "x")},
        "business": {
            "database": "odoo",
            "company_id": "company_1",
            "organization_id": "organization_1",
            "site_id": "site_1",
            "pos_configuration_id": "pos_1",
        },
        "requested_permissions": [Permission.RECEIPT_IMAGE.value],
    }

    async with _client(tmp_path, socket_address=socket_address) as (
        client,
        trust,
        assertion_key,
        clock,
    ):
        created = await _retry_with_nonce(
            client,
            method="POST",
            path="/pairing/v1/requests",
            browser_key=browser_key,
            first_jti="create-first",
            retry_jti="create-retry",
            json=create_payload,
        )
        assert created.status_code == 201
        body = created.json()
        request_id = body["request_id"]
        assert body["state"] == "pending"
        assert body["approval_uri"] == f"inari://pairing/{request_id}"
        assert body["scope"]["browser_origin"] == ODOO_ORIGIN

        approved = await client.post(
            f"/pairing/v1/requests/{request_id}/decision",
            headers={"Authorization": "Bearer device-center"},
            json={"decision": "approve"},
        )
        assert approved.status_code == 200
        assert approved.json()["state"] == "approved"

        request = trust.require_pairing_request(request_id)
        assertion = PairingAssertionSigner(
            signing_key=assertion_key,
            signer_key_id="odoo-pairing-v1",
        ).sign(
            PairingAssertionClaims(
                issuer="odoo",
                subject=request_id,
                audience=request.scope.audience,
                pairing_request_id=request_id,
                agent_id=request.scope.agent_id,
                jwk_thumbprint=request.browser_jwk_thumbprint,
                business=BusinessScope(**create_payload["business"]),
                actor_id="res.users:7",
                role="device_operator",
                scopes=request.requested_permissions,
                session_nonce=request.session_nonce,
                issued_at=NOW,
                expires_at=NOW + timedelta(minutes=5),
                jti="assertion_1",
            )
        )
        admitted = await _retry_with_nonce(
            client,
            method="POST",
            path=f"/pairing/v1/requests/{request_id}/admit",
            browser_key=browser_key,
            first_jti="admit-first",
            retry_jti="admit-retry",
            json={"assertion": assertion.compact_jws},
        )
        assert admitted.status_code == 200
        admitted_body = admitted.json()
        assert admitted_body["token_type"] == "DPoP"
        assert admitted_body["grant"]["generation"] == 0
        assert admitted_body["access_token"]

        recovered = await _retry_with_nonce(
            client,
            method="POST",
            path=f"/pairing/v1/requests/{request_id}/admit",
            browser_key=browser_key,
            first_jti="recover-first",
            retry_jti="recover-retry",
            json={"assertion": assertion.compact_jws},
        )
        assert recovered.status_code == 200
        assert (
            recovered.json()["pairing"]["pairing_id"]
            == (admitted_body["pairing"]["pairing_id"])
        )
        assert (
            recovered.json()["grant"]["grant_id"]
            == (admitted_body["grant"]["grant_id"])
        )

        renewed = await _retry_with_nonce(
            client,
            method="POST",
            path="/pairing/v1/client-grants/renew",
            browser_key=browser_key,
            first_jti="renew-first",
            retry_jti="renew-retry",
            json={
                "pairing_id": admitted_body["pairing"]["pairing_id"],
                "grant_id": admitted_body["grant"]["grant_id"],
            },
        )
        assert renewed.status_code == 200
        assert renewed.json()["grant"]["generation"] == 1
        assert renewed.json()["access_token"]
        assert clock.value == NOW
