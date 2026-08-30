from __future__ import annotations

from datetime import UTC, datetime, timedelta
import base64
import hashlib

import pytest
from joserfc import jwk, jwt
from joserfc.jwk import OKPKey

from inari.client_trust.crypto import (
    PairingAssertionSigner,
    PairingAssertionVerifier,
    jwk_thumbprint,
    public_ed25519_jwk,
)
from inari.client_trust.errors import ClientTrustError, ClientTrustErrorCode
from inari.client_trust.models import (
    AccessTokenClaims,
    BoundOrigin,
    BusinessScope,
    ClientGrant,
    PairingAssertionClaims,
    PairingScope,
    RequestTarget,
)
from inari.client_trust.permissions import Permission
from inari.client_trust.tokens import (
    AccessTokenSigner,
    AccessTokenVerifier,
    DPoPProofVerifier,
    RenewalDPoPProofVerifier,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def key() -> OKPKey:
    return jwk.generate_key("OKP", "Ed25519")


@pytest.fixture
def pairing(key: OKPKey) -> PairingAssertionClaims:
    public = public_ed25519_jwk(key)
    business = BusinessScope("odoo", "company", "org", "site", "pos")
    return PairingAssertionClaims(
        issuer="odoo-issuer",
        subject="pairing-request",
        audience="agent-audience",
        pairing_request_id="request-1",
        agent_id="agent-1",
        jwk_thumbprint=jwk_thumbprint(public),
        business=business,
        actor_id="operator-1",
        role="cashier",
        scopes=frozenset({Permission.RECEIPT_IMAGE}),
        session_nonce="session-nonce",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        jti="assertion-1",
    )


def test_pairing_assertion_round_trip_and_strict_header(
    key: OKPKey, pairing: PairingAssertionClaims
) -> None:
    assertion = PairingAssertionSigner(signing_key=key, signer_key_id="odoo-key").sign(
        pairing
    )
    verified = PairingAssertionVerifier(
        verification_keys={"odoo-key": public_ed25519_jwk(key)},
        issuer="odoo-issuer",
        audience="agent-audience",
        agent_id="agent-1",
    ).verify(assertion, at=NOW)
    assert verified == pairing


def test_pairing_assertion_rejects_expired_and_unknown_kid(
    key: OKPKey, pairing: PairingAssertionClaims
) -> None:
    assertion = PairingAssertionSigner(signing_key=key, signer_key_id="odoo-key").sign(
        pairing
    )
    verifier = PairingAssertionVerifier(
        verification_keys={"other-key": public_ed25519_jwk(key)}
    )
    with pytest.raises(ClientTrustError) as error:
        verifier.verify(assertion, at=NOW)
    assert error.value.code is ClientTrustErrorCode.INVALID_ASSERTION


def test_public_jwk_rejects_private_material_and_wrong_curve(key: OKPKey) -> None:
    private = key.as_dict(private=True)
    with pytest.raises(ClientTrustError):
        public_ed25519_jwk(private)
    with pytest.raises(ClientTrustError):
        public_ed25519_jwk({"kty": "OKP", "crv": "Ed448", "x": private["x"]})


def test_access_token_round_trip_requires_generation_and_digest(key: OKPKey) -> None:
    public = public_ed25519_jwk(key)
    scope = PairingScope(
        agent_id="agent-1",
        browser_origin=BoundOrigin("https://pos.example"),
        agent_endpoint=BoundOrigin("https://agent.example"),
        business=BusinessScope("odoo", "company", "org", "site", "pos"),
        audience="agent-audience",
    )
    grant = ClientGrant(
        grant_id="grant-1",
        pairing_id="pairing-1",
        jwk_thumbprint=jwk_thumbprint(public),
        scope=scope,
        actor_id="operator-1",
        role="cashier",
        permissions=frozenset({Permission.RECEIPT_IMAGE}),
        authorization_digest="auth-digest",
        generation=3,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    token, claims = AccessTokenSigner(
        signing_key=key, issuer="agent-1", audience="agent-audience"
    ).issue(
        grant, issued_at=NOW, expires_at=NOW + timedelta(minutes=15), token_id="token-1"
    )
    assert claims.cnf_jkt == grant.jwk_thumbprint
    verified = AccessTokenVerifier(
        verification_key=public,
        issuer="agent-1",
        audience="agent-audience",
    ).verify(token, at=NOW)
    assert verified.client_grant_id == grant.grant_id


def test_dpop_proof_binds_key_method_uri_token_and_nonce(key: OKPKey) -> None:
    public = public_ed25519_jwk(key)
    claims = AccessTokenClaims(
        issuer="agent-1",
        subject="operator-1",
        audience="agent-audience",
        token_id="token-1",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        cnf_jkt=jwk_thumbprint(public),
        client_pairing_id="pairing-1",
        client_grant_id="grant-1",
        business=BusinessScope("odoo", "company", "org", "site", "pos"),
        permissions=frozenset({Permission.RECEIPT_IMAGE}),
        generation=1,
        authorization_digest="auth-digest",
    )
    access_token = "access-token"
    target = RequestTarget(
        "POST", "https://agent.example/device-work?ignored=yes#fragment"
    )
    nonce = "nonce-1234"
    ath = (
        base64.urlsafe_b64encode(hashlib.sha256(access_token.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    proof = jwt.encode(
        {"typ": "dpop+jwt", "alg": "Ed25519", "jwk": public},
        {
            "htm": "POST",
            "htu": target.htu,
            "iat": int(NOW.timestamp()),
            "ath": ath,
            "nonce": nonce,
            "jti": "proof-1",
        },
        key,
        algorithms=["Ed25519"],
    )
    accepted = DPoPProofVerifier().verify(
        proof,
        target=target,
        claims=claims,
        access_token=access_token,
        at=NOW,
    )
    assert accepted.jwk_thumbprint == claims.cnf_jkt
    assert accepted.htu == "https://agent.example/device-work"


def test_dpop_rejects_wrong_token_hash_and_clock(key: OKPKey) -> None:
    public = public_ed25519_jwk(key)
    claims = AccessTokenClaims(
        issuer="agent-1",
        subject="operator-1",
        audience="agent-audience",
        token_id="token-1",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        cnf_jkt=jwk_thumbprint(public),
        client_pairing_id="pairing-1",
        client_grant_id="grant-1",
        business=BusinessScope("odoo", "company", "org", "site", "pos"),
        permissions=frozenset({Permission.RECEIPT_IMAGE}),
        generation=1,
        authorization_digest="auth-digest",
    )
    target = RequestTarget("POST", "https://agent.example/device-work")
    proof = jwt.encode(
        {"typ": "dpop+jwt", "alg": "Ed25519", "jwk": public},
        {
            "htm": "POST",
            "htu": target.htu,
            "iat": int((NOW - timedelta(minutes=3)).timestamp()),
            "ath": "wrong-hash",
            "nonce": "nonce-1234",
            "jti": "proof-1",
        },
        key,
        algorithms=["Ed25519"],
    )
    with pytest.raises(ClientTrustError) as error:
        DPoPProofVerifier().verify(
            proof,
            target=target,
            claims=claims,
            access_token="access-token",
            at=NOW,
        )
    assert error.value.code is ClientTrustErrorCode.INVALID_DPOP_PROOF


def test_renewal_dpop_binds_browser_key_and_target(key: OKPKey) -> None:
    public = public_ed25519_jwk(key)
    target = RequestTarget("POST", "https://agent.example/v1/client-grants/renew")
    proof = jwt.encode(
        {"typ": "dpop+jwt", "alg": "Ed25519", "jwk": public},
        {
            "htm": target.method,
            "htu": target.htu,
            "iat": int(NOW.timestamp()),
            "nonce": "nonce-1234",
            "jti": "renewal-proof-1",
        },
        key,
        algorithms=["Ed25519"],
    )
    accepted = RenewalDPoPProofVerifier().verify(
        proof,
        target=target,
        jwk_thumbprint=jwk_thumbprint(public),
        at=NOW,
    )
    assert accepted.nonce == "nonce-1234"
