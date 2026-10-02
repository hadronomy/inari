from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from inari.client_trust.errors import (
    ClientTrustError,
    ClientTrustErrorCode,
    DPoPNonceRequiredError,
)
from inari.client_trust.models import (
    AccessTokenClaims,
    AcceptedDPoPProof,
    AcceptedRenewalDPoPProof,
    BoundOrigin,
    BusinessScope,
    ClientGrant,
    ClientPairing,
    EndpointBinding,
    PairingAssertion,
    PairingAssertionClaims,
    PairingCommand,
    PairingScope,
    PairingRequest,
    PairingRequestState,
    RenewalCommand,
    RequestTarget,
)
from inari.client_trust.permissions import Permission
from inari.client_trust.ports import DPoPNonceConsumption
from inari.client_trust.service import ClientTrustService


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
SCOPE = PairingScope(
    agent_id="agent-1",
    browser_origin=BoundOrigin("https://pos.example"),
    agent_endpoint=BoundOrigin("https://agent.example"),
    business=BusinessScope("odoo", "company", "org", "site", "pos"),
    audience="inari-agent",
)


class Clock:
    value = NOW

    def now(self) -> datetime:
        return self.value


class Store:
    def __init__(self) -> None:
        self.pairings: dict[str, ClientPairing] = {}
        self.grants: dict[str, ClientGrant] = {}
        self.assertions: set[str] = set()
        self.proofs: set[str] = set()
        self.requests: dict[str, PairingRequest] = {}
        self.nonces: dict[str, tuple[datetime, datetime | None]] = {}

    def get_pairing_request(self, request_id: str) -> PairingRequest | None:
        return self.requests.get(request_id)

    def save_pairing_request(self, request: PairingRequest) -> None:
        self.requests[request.request_id] = request

    def get_pairing(self, pairing_id: str) -> ClientPairing | None:
        return self.pairings.get(pairing_id)

    def save_pairing(self, pairing: ClientPairing) -> None:
        self.pairings[pairing.pairing_id] = pairing

    def get_grant(self, grant_id: str) -> ClientGrant | None:
        return self.grants.get(grant_id)

    def save_grant(self, grant: ClientGrant) -> None:
        self.grants[grant.grant_id] = grant

    def complete_pairing(
        self, *, request, pairing, grant, assertion_jti: str, at: datetime
    ) -> bool:
        if assertion_jti in self.assertions:
            return False
        self.assertions.add(assertion_jti)
        self.requests[request.request_id] = request
        self.save_pairing(pairing)
        self.save_grant(grant)
        return True

    def save_dpop_nonce(
        self, nonce: str, *, issued_at: datetime, expires_at: datetime
    ) -> None:
        self.nonces[nonce] = (expires_at, None)

    def consume_dpop_nonce(
        self,
        nonce: str,
        *,
        jti: str,
        at: datetime,
        replay_expires_at: datetime,
    ) -> DPoPNonceConsumption:
        if jti in self.proofs:
            return DPoPNonceConsumption.REPLAY
        value = self.nonces.get(nonce)
        if value is None or value[0] <= at or value[1] is not None:
            return DPoPNonceConsumption.INVALID
        self.proofs.add(jti)
        self.nonces[nonce] = (value[0], at)
        return DPoPNonceConsumption.ACCEPTED


class AssertionVerifier:
    def verify(self, assertion, *, request, at):
        return assertion.claims


class TokenVerifier:
    def __init__(self) -> None:
        self.claims: AccessTokenClaims | None = None

    def verify(self, token: str, *, at: datetime) -> AccessTokenClaims:
        assert self.claims is not None
        return self.claims


class DPoPVerifier:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.nonce: str | None = None
        self.omit_nonce = False

    def verify(self, proof, *, target, claims, access_token, at):
        if self.nonce is None and not self.omit_nonce:
            self.nonce = next(
                value
                for value, (_, consumed_at) in self.store.nonces.items()
                if consumed_at is None
            )
        return AcceptedDPoPProof(
            jwk_thumbprint=claims.cnf_jkt,
            htm=target.method,
            htu=target.htu,
            iat=at,
            ath="a" * 43,
            nonce=self.nonce,
            jti="proof-1",
            accepted_at=at,
        )


class RenewalDPoPVerifier:
    def __init__(self, store: Store) -> None:
        self.store = store

    def verify(self, proof, *, target, jwk_thumbprint, at):
        nonce = next(
            value
            for value, (_, consumed_at) in self.store.nonces.items()
            if consumed_at is None
        )
        return AcceptedRenewalDPoPProof(
            jwk_thumbprint=jwk_thumbprint,
            htm=target.method,
            htu=target.htu,
            iat=at,
            nonce=nonce,
            jti=f"renewal-proof-{proof.split('.', 1)[0]}",
            accepted_at=at,
        )


class TokenIssuer:
    def issue(self, grant, *, issued_at, expires_at):
        claims = AccessTokenClaims(
            issuer="agent-1",
            subject=grant.actor_id,
            audience=grant.scope.audience,
            token_id="token-1",
            issued_at=issued_at,
            expires_at=expires_at,
            cnf_jkt=grant.jwk_thumbprint,
            client_pairing_id=grant.pairing_id,
            client_grant_id=grant.grant_id,
            business=grant.scope.business,
            permissions=grant.permissions,
            generation=grant.generation,
            authorization_digest=grant.authorization_digest,
        )
        return "signed-access-token", claims


def make_service() -> tuple[ClientTrustService, Store, Clock, TokenVerifier]:
    store = Store()
    clock = Clock()
    verifier = TokenVerifier()
    return (
        ClientTrustService(
            store=store,
            assertion_verifier=AssertionVerifier(),
            access_token_verifier=verifier,
            dpop_verifier=DPoPVerifier(store),
            renewal_dpop_verifier=RenewalDPoPVerifier(store),
            access_token_issuer=TokenIssuer(),
            clock=clock,
            phrase_factory=lambda: "amber-river-seven",
            id_factory=lambda prefix: f"{prefix}-1",
        ),
        store,
        clock,
        verifier,
    )


def test_admission_consumes_assertion_once_and_issues_scoped_records() -> None:
    service, store, _, _ = make_service()
    request = service.create_pairing_request(
        scope=SCOPE,
        browser_jwk_thumbprint="thumbprint-1",
        requested_permissions={Permission.RECEIPT_IMAGE},
    )
    claims = PairingAssertionClaims(
        issuer="odoo",
        subject="request-1",
        audience=SCOPE.audience,
        pairing_request_id=request.request_id,
        agent_id=SCOPE.agent_id,
        jwk_thumbprint=request.browser_jwk_thumbprint,
        business=SCOPE.business,
        actor_id="operator-1",
        role="device_operator",
        scopes=request.requested_permissions,
        session_nonce=request.session_nonce,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        jti="assertion-1",
    )
    command = PairingCommand(
        request=request,
        assertion=PairingAssertion(
            claims=claims, compact_jws="a.b.c", signer_key_id="odoo-key"
        ),
    )
    result = service.admit_pairing(command)
    assert result.pairing.pairing_id == "pairing-1"
    assert result.grant.permissions == frozenset({Permission.RECEIPT_IMAGE})
    assert store.get_grant(result.grant.grant_id) == result.grant
    with pytest.raises(ClientTrustError) as error:
        service.admit_pairing(command)
    assert error.value.code is ClientTrustErrorCode.REPLAY_DETECTED


@pytest.mark.parametrize("initial_nonce", [None, "client-placeholder"])
def test_authorization_requires_exact_origin_and_replays_are_rejected(
    initial_nonce: str | None,
) -> None:
    service, store, _, token_verifier = make_service()
    pairing = ClientPairing(
        pairing_id="pairing-1",
        pairing_request_id="pairing-request-1",
        jwk_thumbprint="thumbprint-1",
        scope=SCOPE,
        actor_id="operator-1",
        role="device_operator",
        permissions=frozenset({Permission.RECEIPT_IMAGE}),
        created_at=NOW,
        expires_at=None,
    )
    grant = ClientGrant(
        grant_id="grant-1",
        pairing_id=pairing.pairing_id,
        jwk_thumbprint=pairing.jwk_thumbprint,
        scope=SCOPE,
        actor_id=pairing.actor_id,
        role=pairing.role,
        permissions=pairing.permissions,
        authorization_digest="digest-1",
        generation=1,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        offline_renewal_until=NOW + timedelta(days=7),
    )
    store.save_pairing(pairing)
    store.save_grant(grant)
    token_verifier.claims = AccessTokenClaims(
        issuer="agent-1",
        subject=grant.actor_id,
        audience=SCOPE.audience,
        token_id="token-1",
        issued_at=NOW,
        expires_at=grant.expires_at,
        cnf_jkt=grant.jwk_thumbprint,
        client_pairing_id=grant.pairing_id,
        client_grant_id=grant.grant_id,
        business=SCOPE.business,
        permissions=grant.permissions,
        generation=1,
        authorization_digest=grant.authorization_digest,
    )
    binding = EndpointBinding(
        agent_id=SCOPE.agent_id,
        audience=SCOPE.audience,
        agent_endpoint=SCOPE.agent_endpoint,
        allowed_methods=frozenset({"POST"}),
        allowed_paths=("/v1/device-work",),
    )
    request = SimpleNamespace(
        target=RequestTarget("POST", "https://agent.example/v1/device-work"),
        origin="https://pos.example",
        authorization="DPoP signed-access-token",
        dpop="signed-proof",
    )
    verifier = service.dpop_verifier
    assert isinstance(verifier, DPoPVerifier)
    verifier.nonce = initial_nonce
    verifier.omit_nonce = initial_nonce is None
    with pytest.raises(DPoPNonceRequiredError) as challenge:
        service.authorize_request(
            request, binding=binding, permission=Permission.RECEIPT_IMAGE
        )
    assert store.nonces[challenge.value.nonce.nonce][1] is None
    verifier.nonce = challenge.value.nonce.nonce
    verifier.omit_nonce = False
    authorized = service.authorize_request(
        request, binding=binding, permission=Permission.RECEIPT_IMAGE
    )
    assert authorized.grant.grant_id == grant.grant_id
    with pytest.raises(ClientTrustError) as error:
        service.authorize_request(
            SimpleNamespace(
                target=request.target,
                origin="https://other.example",
                authorization=request.authorization,
                dpop=request.dpop,
            ),
            binding=binding,
        )
    assert error.value.code is ClientTrustErrorCode.SCOPE_MISMATCH
    with pytest.raises(ClientTrustError) as error:
        service.authorize_request(
            request, binding=binding, permission=Permission.DRAWER
        )
    assert error.value.code is ClientTrustErrorCode.REPLAY_DETECTED


def test_renewal_requires_session_and_offline_window_then_revocation_blocks_use() -> (
    None
):
    service, store, clock, _ = make_service()
    request = PairingRequest(
        request_id="pairing-request-1",
        scope=SCOPE,
        browser_jwk_thumbprint="thumbprint-1",
        requested_permissions=frozenset({Permission.RECEIPT_IMAGE}),
        session_nonce="session-1",
        phrase="amber-river-seven",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        state=PairingRequestState.COMPLETED,
    )
    store.save_pairing_request(request)
    pairing = ClientPairing(
        pairing_id="pairing-1",
        pairing_request_id=request.request_id,
        jwk_thumbprint="thumbprint-1",
        scope=SCOPE,
        actor_id="operator-1",
        role="device_operator",
        permissions=frozenset({Permission.RECEIPT_IMAGE}),
        created_at=NOW,
        expires_at=None,
    )
    grant = ClientGrant(
        grant_id="grant-1",
        pairing_id=pairing.pairing_id,
        jwk_thumbprint=pairing.jwk_thumbprint,
        scope=SCOPE,
        actor_id=pairing.actor_id,
        role=pairing.role,
        permissions=pairing.permissions,
        authorization_digest="digest-1",
        generation=0,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        offline_renewal_until=NOW + timedelta(days=7),
    )
    store.save_pairing(pairing)
    store.save_grant(grant)
    service.issue_dpop_nonce()
    other = replace(pairing, pairing_id="other-pairing", jwk_thumbprint="other-key")
    store.save_pairing(other)
    nonce_count = len(store.nonces)
    with pytest.raises(ClientTrustError) as mismatch:
        service.renew_grant(
            RenewalCommand(
                pairing_id=other.pairing_id,
                grant_id=grant.grant_id,
                target=RequestTarget(
                    "POST", "https://agent.example/pairing/v1/client-grants/renew"
                ),
                dpop="other.proof.signature",
            )
        )
    assert mismatch.value.code is ClientTrustErrorCode.SCOPE_MISMATCH
    assert store.get_grant(grant.grant_id) == grant
    assert len(store.nonces) == nonce_count
    renewed = service.renew_grant(
        RenewalCommand(
            pairing_id=pairing.pairing_id,
            grant_id=grant.grant_id,
            target=RequestTarget(
                "POST", "https://agent.example/v1/client-grants/renew"
            ),
            dpop="a.b.c",
        )
    )
    assert renewed.grant.issued_at == NOW
    assert renewed.grant.generation == 1
    service.revoke_grant(grant.grant_id)
    with pytest.raises(ClientTrustError) as error:
        service.issue_access_token(grant.grant_id)
    assert error.value.code is ClientTrustErrorCode.GRANT_REVOKED
    clock.value = NOW + timedelta(days=8)
    service.issue_dpop_nonce()
    with pytest.raises(ClientTrustError) as error:
        service.renew_grant(
            RenewalCommand(
                pairing_id=pairing.pairing_id,
                grant_id=grant.grant_id,
                target=RequestTarget(
                    "POST", "https://agent.example/v1/client-grants/renew"
                ),
                dpop="d.e.f",
            )
        )
    assert error.value.code is ClientTrustErrorCode.GRANT_REVOKED
