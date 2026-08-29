from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest

from inari.client_trust import (
    AccessTokenClaims,
    AcceptedDPoPProof,
    AuthorizedRequest,
    BoundOrigin,
    BusinessScope,
    ClientGrant,
    ClientPairing,
    ClientTrustError,
    EndpointPolicy,
    GrantAdmissionProof,
    GrantLifecycle,
    InvalidOriginError,
    PairingAssertion,
    PairingAssertionClaims,
    PairingCommand,
    PairingLifecycle,
    PairingRequest,
    PairingRequestState,
    PairingScope,
    Permission,
    PermissionCatalog,
    PermissionDeniedError,
    RenewalCommand,
    RenewalResult,
    RequestTarget,
    ScopeMismatchError,
)


NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
ORIGIN = BoundOrigin("https://POS.Example:443/")
BUSINESS = BusinessScope(
    database="odoo_prod",
    company_id="company_1",
    organization_id="org_1",
    site_id="site_1",
    pos_configuration_id="pos_1",
)
SCOPE = PairingScope(
    agent_id="agent_1",
    origin=ORIGIN,
    business=BUSINESS,
    audience="inari-agent",
)
THUMBPRINT = "A" * 43


def pairing(*, lifecycle: PairingLifecycle = PairingLifecycle.ACTIVE) -> ClientPairing:
    return ClientPairing(
        pairing_id="pairing_1",
        jwk_thumbprint=THUMBPRINT,
        scope=SCOPE,
        actor_id="user_1",
        role="device_operator",
        permissions=frozenset({Permission.RECEIPT_IMAGE, Permission.DRAWER}),
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
        lifecycle=lifecycle,
    )


def grant(*, lifecycle: GrantLifecycle = GrantLifecycle.ACTIVE) -> ClientGrant:
    return ClientGrant(
        grant_id="grant_1",
        pairing_id="pairing_1",
        jwk_thumbprint=THUMBPRINT,
        scope=SCOPE,
        actor_id="user_1",
        role="device_operator",
        permissions=frozenset({Permission.RECEIPT_IMAGE, Permission.DRAWER}),
        authorization_digest="authorization_digest_1",
        token_id="token_1",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        offline_renewal_until=NOW + timedelta(days=7),
        lifecycle=lifecycle,
    )


def claims() -> PairingAssertionClaims:
    return PairingAssertionClaims(
        issuer="odoo_prod",
        subject="pairing_request_1",
        audience="inari-agent",
        pairing_request_id="pairing_request_1",
        agent_id="agent_1",
        jwk_thumbprint=THUMBPRINT,
        business=BUSINESS,
        actor_id="user_1",
        role="device_operator",
        scopes=frozenset({Permission.RECEIPT_IMAGE}),
        session_nonce="session_nonce_1",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        jti="assertion_jti_1",
    )


def test_bound_origin_is_exact_and_canonical() -> None:
    assert ORIGIN.value == "https://pos.example"
    assert ORIGIN.matches("https://POS.EXAMPLE:443/")
    assert not ORIGIN.matches("https://other.example")


@pytest.mark.parametrize(
    "value",
    (
        "",
        "null",
        "*",
        "https://*.example",
        "http://pos.example",
        "https://pos.example/path",
        "https://user:password@pos.example",
    ),
)
def test_bound_origin_rejects_ambiguous_values(value: str) -> None:
    with pytest.raises(InvalidOriginError):
        BoundOrigin(value)


def test_scope_values_are_immutable() -> None:
    assert BUSINESS.is_pos_scope
    with pytest.raises((AttributeError, TypeError)):
        BUSINESS.database = "other"  # ty: ignore[invalid-assignment]

    pairing_value = pairing()
    assert isinstance(pairing_value.permissions, frozenset)
    with pytest.raises(AttributeError):
        pairing_value.permissions.add(Permission.SCALE)  # ty: ignore[unresolved-attribute]


def test_permission_catalog_is_closed_and_requires_all_permissions() -> None:
    assert PermissionCatalog.permissions_for("receipt_image") == frozenset(
        {Permission.RECEIPT_IMAGE}
    )
    assert PermissionCatalog.permissions_for("jobs") == frozenset(
        {Permission.JOBS_READ, Permission.JOBS_SUBMIT}
    )
    with pytest.raises(ClientTrustError):
        PermissionCatalog.permissions_for("raw_bytes")
    with pytest.raises(PermissionDeniedError):
        PermissionCatalog.require({Permission.RECEIPT_IMAGE}, Permission.DRAWER)


def test_pairing_and_grant_lifecycles_are_checked_at_request_time() -> None:
    pairing().active_at(NOW)
    grant().active_at(NOW)
    with pytest.raises(ClientTrustError):
        pairing(lifecycle=PairingLifecycle.REVOKED).active_at(NOW)
    with pytest.raises(ClientTrustError):
        grant(lifecycle=GrantLifecycle.REVOKED).active_at(NOW)
    with pytest.raises(ClientTrustError):
        grant().active_at(NOW + timedelta(minutes=15))


def test_access_claims_are_serializable_without_mutable_nested_values() -> None:
    value = AccessTokenClaims(
        issuer="agent_1",
        subject="user_1",
        audience="inari-agent",
        token_id="token_1",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        cnf_jkt=THUMBPRINT,
        client_pairing_id="pairing_1",
        client_grant_id="grant_1",
        business=BUSINESS,
        permissions=frozenset({Permission.RECEIPT_IMAGE}),
    )

    serialized = value.claims()
    assert serialized["cnf"] == MappingProxyType({"jkt": THUMBPRINT})
    with pytest.raises(TypeError):
        serialized["cnf"]["jkt"] = "other"  # type: ignore[index]
    assert serialized["scope"] == (Permission.RECEIPT_IMAGE.value,)


def test_authorized_request_checks_dpop_and_endpoint_scope() -> None:
    target = RequestTarget("post", "https://pos.example/v1/device-work?ignored=true")
    proof = AcceptedDPoPProof(
        jwk_thumbprint=THUMBPRINT,
        htm="post",
        htu=target.uri,
        iat=NOW,
        ath="a" * 43,
        nonce="nonce_1",
        jti="proof_1",
        accepted_at=NOW,
    )
    endpoint = EndpointPolicy(
        agent_id="agent_1",
        audience="inari-agent",
        origin=ORIGIN,
        business=BUSINESS,
        allowed_paths=("/v1/device-work",),
    )
    authorized = AuthorizedRequest(
        target=target,
        grant=grant(),
        dpop=proof,
        endpoint=endpoint,
        accepted_at=NOW,
    )
    assert authorized.require(Permission.RECEIPT_IMAGE) is authorized
    with pytest.raises(PermissionDeniedError):
        authorized.require(Permission.SCALE)


def test_pairing_command_requires_request_and_assertion_identity() -> None:
    request = PairingRequest(
        request_id="pairing_request_1",
        scope=SCOPE,
        browser_jwk_thumbprint=THUMBPRINT,
        requested_permissions=frozenset({Permission.RECEIPT_IMAGE}),
        session_nonce="session_nonce_1",
        phrase="amber-river-seven",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    command = PairingCommand(
        request=request,
        assertion=PairingAssertion(
            claims=claims(),
            compact_jws="header.payload.signature",
            signer_key_id="key_1",
        ),
    )
    assert command.request.state is PairingRequestState.PENDING
    mismatched_claims = replace(claims(), pairing_request_id="other_request")
    with pytest.raises(ScopeMismatchError):
        PairingCommand(
            request=request,
            assertion=PairingAssertion(
                claims=mismatched_claims,
                compact_jws="header.payload.signature",
                signer_key_id="key_1",
            ),
        )


def test_grant_admission_and_renewal_values_are_immutable() -> None:
    admission = GrantAdmissionProof(
        pairing_request_id="pairing_request_1",
        assertion_jti="assertion_jti_1",
        assertion_digest="assertion_digest_1",
        jwk_thumbprint=THUMBPRINT,
        admitted_at=NOW,
    )
    renewal = RenewalCommand(
        pairing_id="pairing_1",
        grant_id="grant_1",
        jwk_thumbprint=THUMBPRINT,
        session_nonce="session_nonce_1",
        requested_at=NOW,
    )
    result = RenewalResult(
        grant=grant(),
        claims=AccessTokenClaims(
            issuer="agent_1",
            subject="user_1",
            audience="inari-agent",
            token_id="token_2",
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=15),
            cnf_jkt=THUMBPRINT,
            client_pairing_id="pairing_1",
            client_grant_id="grant_1",
            business=BUSINESS,
            permissions=frozenset({Permission.RECEIPT_IMAGE, Permission.DRAWER}),
        ),
    )
    assert admission.jwk_thumbprint == renewal.jwk_thumbprint == result.claims.cnf_jkt
