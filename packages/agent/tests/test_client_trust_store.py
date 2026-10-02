from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy.exc import IntegrityError

from inari.client_trust import (
    BoundOrigin,
    BusinessScope,
    ClientGrant,
    ClientPairing,
    PairingLifecycle,
    PairingRequest,
    PairingScope,
    PairingRequestState,
    Permission,
    DPoPNonceConsumption,
)
from inari.client_trust.store import SqliteClientTrustStore
from inari.db.migrations import DatabaseMigrator


NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
SCOPE = PairingScope(
    agent_id="agent_1",
    browser_origin=BoundOrigin("https://odoo.example"),
    agent_endpoint=BoundOrigin("https://agent.example"),
    business=BusinessScope(
        database="odoo_prod",
        company_id="company_1",
        organization_id="org_1",
        site_id="site_1",
        pos_configuration_id="pos_1",
    ),
    audience="inari-agent",
)
THUMBPRINT = "A" * 43


def test_store_round_trips_content_free_trust_values(tmp_path: Path) -> None:
    path = _database(tmp_path)
    store = SqliteClientTrustStore(path)
    request = PairingRequest(
        request_id="request_1",
        scope=SCOPE,
        browser_jwk_thumbprint=THUMBPRINT,
        requested_permissions=frozenset({Permission.RECEIPT_IMAGE}),
        session_nonce="session_nonce_1",
        phrase="amber-river-seven",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    pairing = ClientPairing(
        pairing_id="pairing_1",
        pairing_request_id=request.request_id,
        jwk_thumbprint=THUMBPRINT,
        scope=SCOPE,
        actor_id="user_1",
        role="device_operator",
        permissions=frozenset({Permission.RECEIPT_IMAGE}),
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    grant = ClientGrant(
        grant_id="grant_1",
        pairing_id="pairing_1",
        jwk_thumbprint=THUMBPRINT,
        scope=SCOPE,
        actor_id="user_1",
        role="device_operator",
        permissions=frozenset({Permission.RECEIPT_IMAGE}),
        authorization_digest="authorization_digest_1",
        generation=0,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )

    store.save_pairing_request(request)
    assert store.complete_pairing(
        request=replace(request, state=PairingRequestState.COMPLETED),
        pairing=pairing,
        grant=grant,
        assertion_jti="assertion_1",
        at=NOW,
    )

    assert store.get_pairing_request(request.request_id) == replace(
        request, state=PairingRequestState.COMPLETED
    )
    assert store.get_pairing(pairing.pairing_id) == pairing
    assert store.get_grant(grant.grant_id) == grant
    nonce_expires_at = NOW + timedelta(minutes=2)
    store.save_dpop_nonce("nonce_1234", issued_at=NOW, expires_at=nonce_expires_at)
    assert store.consume_dpop_nonce(
        "nonce_1234",
        jti="dpop_1",
        at=NOW,
        replay_expires_at=NOW + timedelta(minutes=15),
    ) is DPoPNonceConsumption.ACCEPTED
    assert store.consume_dpop_nonce(
        "nonce_1234",
        jti="dpop_1",
        at=NOW,
        replay_expires_at=NOW + timedelta(minutes=15),
    ) is DPoPNonceConsumption.REPLAY

    assert store.consume_dpop_nonce(
        "unknown_nonce",
        jti="dpop_after_invalid_nonce",
        at=NOW,
        replay_expires_at=NOW + timedelta(minutes=15),
    ) is DPoPNonceConsumption.INVALID
    store.save_dpop_nonce(
        "nonce_after_invalid_nonce",
        issued_at=NOW,
        expires_at=nonce_expires_at,
    )
    assert store.consume_dpop_nonce(
        "nonce_after_invalid_nonce",
        jti="dpop_after_invalid_nonce",
        at=NOW,
        replay_expires_at=NOW + timedelta(minutes=15),
    ) is DPoPNonceConsumption.ACCEPTED

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(client_trust_grants)")
        }
    assert "compact_jws" not in columns
    assert "access_token" not in columns


def test_store_only_updates_lifecycle_fields(tmp_path: Path) -> None:
    path = _database(tmp_path)
    store = SqliteClientTrustStore(path)
    request = PairingRequest(
        request_id="request_1",
        scope=SCOPE,
        browser_jwk_thumbprint=THUMBPRINT,
        requested_permissions=frozenset({Permission.RECEIPT_IMAGE}),
        session_nonce="session_nonce_1",
        phrase="amber-river-seven",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    store.save_pairing_request(request)
    pairing = ClientPairing(
        pairing_id="pairing_1",
        pairing_request_id=request.request_id,
        jwk_thumbprint=THUMBPRINT,
        scope=SCOPE,
        actor_id="user_1",
        role="device_operator",
        permissions=frozenset({Permission.RECEIPT_IMAGE}),
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    store.save_pairing(pairing)
    store.save_pairing(replace(pairing, lifecycle=PairingLifecycle.REVOKED))
    stored = store.get_pairing("pairing_1")
    assert stored is not None
    assert stored.lifecycle is PairingLifecycle.REVOKED

    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE client_trust_pairings SET actor_id = 'other' WHERE pairing_id = 'pairing_1'"
            )


def test_pairing_request_state_update_is_checked_by_sqlite(tmp_path: Path) -> None:
    path = _database(tmp_path)
    store = SqliteClientTrustStore(path)
    request = PairingRequest(
        request_id="request_1",
        scope=SCOPE,
        browser_jwk_thumbprint=THUMBPRINT,
        requested_permissions=frozenset({Permission.RECEIPT_IMAGE}),
        session_nonce="session_nonce_1",
        phrase="amber-river-seven",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    store.save_pairing_request(request)
    store.save_pairing_request(
        PairingRequest(
            request_id=request.request_id,
            scope=request.scope,
            browser_jwk_thumbprint=request.browser_jwk_thumbprint,
            requested_permissions=request.requested_permissions,
            session_nonce=request.session_nonce,
            phrase=request.phrase,
            created_at=request.created_at,
            expires_at=request.expires_at,
            state=PairingRequestState.APPROVED,
        )
    )
    with pytest.raises(IntegrityError):
        store.save_pairing_request(
            PairingRequest(
                request_id=request.request_id,
                scope=request.scope,
                browser_jwk_thumbprint=request.browser_jwk_thumbprint,
                requested_permissions=request.requested_permissions,
                session_nonce=request.session_nonce,
                phrase=request.phrase,
                created_at=request.created_at,
                expires_at=request.expires_at,
                state=PairingRequestState.DENIED,
            )
        )


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "runtime.sqlite3"
    DatabaseMigrator(path).ensure_current()
    return path
