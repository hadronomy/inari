"""SQLAlchemy Core persistence for content-free Client Trust state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db.schema import (
    client_grants_table,
    client_pairings_table,
    client_trust_nonces_table,
    client_trust_replays_table,
    create_database_engine,
    pairing_requests_table,
)
from .models import (
    BusinessScope,
    ClientGrant,
    ClientPairing,
    GrantLifecycle,
    PairingLifecycle,
    PairingRequest,
    PairingRequestState,
    PairingScope,
    BoundOrigin,
)
from .permissions import PermissionCatalog, PermissionSet
from .ports import DPoPNonceConsumption


class SqliteClientTrustStore:
    """Store Client Trust identities without signed content or private keys."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.engine = create_database_engine(database_path)

    def get_pairing(self, pairing_id: str) -> ClientPairing | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(client_pairings_table).where(
                        client_pairings_table.c.pairing_id == pairing_id
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else _pairing_from_row(cast(Mapping[str, Any], row))

    def get_pairing_by_request(self, request_id: str) -> ClientPairing | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(client_pairings_table).where(
                        client_pairings_table.c.pairing_request_id == request_id
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else _pairing_from_row(cast(Mapping[str, Any], row))

    def save_pairing(self, pairing: ClientPairing) -> None:
        values = _pairing_values(pairing)
        statement = sqlite_insert(client_pairings_table).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[client_pairings_table.c.pairing_id],
            set_={
                "lifecycle": statement.excluded.lifecycle,
                "last_used_at": statement.excluded.last_used_at,
            },
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    def get_grant(self, grant_id: str) -> ClientGrant | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(client_grants_table).where(
                        client_grants_table.c.grant_id == grant_id
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else _grant_from_row(cast(Mapping[str, Any], row))

    def get_grant_for_pairing(self, pairing_id: str) -> ClientGrant | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(client_grants_table)
                    .where(client_grants_table.c.pairing_id == pairing_id)
                    .order_by(client_grants_table.c.generation.desc())
                )
                .mappings()
                .first()
            )
        return None if row is None else _grant_from_row(cast(Mapping[str, Any], row))

    def save_grant(self, grant: ClientGrant) -> None:
        values = _grant_values(grant)
        statement = sqlite_insert(client_grants_table).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[client_grants_table.c.grant_id],
            set_={
                "generation": statement.excluded.generation,
                "issued_at": statement.excluded.issued_at,
                "expires_at": statement.excluded.expires_at,
                "lifecycle": statement.excluded.lifecycle,
                "last_used_at": statement.excluded.last_used_at,
            },
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    def get_pairing_request(self, request_id: str) -> PairingRequest | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(pairing_requests_table).where(
                        pairing_requests_table.c.request_id == request_id
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else _request_from_row(cast(Mapping[str, Any], row))

    def save_pairing_request(self, request: PairingRequest) -> None:
        values = _request_values(request)
        statement = sqlite_insert(pairing_requests_table).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[pairing_requests_table.c.request_id],
            set_={"state": statement.excluded.state},
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    def transition_pairing_request(
        self,
        request_id: str,
        *,
        from_states: frozenset[str],
        to_state: str,
    ) -> PairingRequest | None:
        if not from_states:
            raise ValueError("from_states must not be empty")
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(pairing_requests_table)
                .where(
                    pairing_requests_table.c.request_id == request_id,
                    pairing_requests_table.c.state.in_(tuple(from_states)),
                )
                .values(state=to_state)
            )
            if changed.rowcount != 1:
                return None
            row = (
                connection.execute(
                    select(pairing_requests_table).where(
                        pairing_requests_table.c.request_id == request_id
                    )
                )
                .mappings()
                .one()
            )
        return _request_from_row(cast(Mapping[str, Any], row))

    def complete_pairing(
        self,
        *,
        request: PairingRequest,
        pairing: ClientPairing,
        grant: ClientGrant,
        assertion_jti: str,
        at: datetime,
    ) -> bool:
        with self.engine.begin() as connection:
            replay = connection.execute(
                sqlite_insert(client_trust_replays_table)
                .values(
                    kind="pairing_assertion",
                    jti=assertion_jti,
                    consumed_at=_timestamp(at),
                    expires_at=None,
                )
                .prefix_with("OR IGNORE")
            )
            if replay.rowcount != 1:
                return False
            completed = connection.execute(
                update(pairing_requests_table)
                .where(
                    pairing_requests_table.c.request_id == request.request_id,
                    pairing_requests_table.c.state
                    == PairingRequestState.APPROVED.value,
                )
                .values(state=request.state.value)
            )
            if completed.rowcount != 1:
                raise ValueError("The Pairing Request is not approved")
            connection.execute(
                sqlite_insert(client_pairings_table).values(_pairing_values(pairing))
            )
            connection.execute(
                sqlite_insert(client_grants_table).values(_grant_values(grant))
            )
        return True

    def pairing_assertion_was_consumed(self, assertion_jti: str) -> bool:
        with self.engine.connect() as connection:
            return (
                connection.execute(
                    select(client_trust_replays_table.c.jti).where(
                        client_trust_replays_table.c.kind == "pairing_assertion",
                        client_trust_replays_table.c.jti == assertion_jti,
                    )
                ).first()
                is not None
            )

    def save_dpop_nonce(
        self, nonce: str, *, issued_at: datetime, expires_at: datetime
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                delete(client_trust_nonces_table).where(
                    client_trust_nonces_table.c.expires_at <= _timestamp(issued_at)
                )
            )
            connection.execute(
                sqlite_insert(client_trust_nonces_table).values(
                    nonce=nonce,
                    issued_at=_timestamp(issued_at),
                    expires_at=_timestamp(expires_at),
                    consumed_at=None,
                )
            )

    def consume_dpop_nonce(
        self,
        nonce: str,
        *,
        jti: str,
        at: datetime,
        replay_expires_at: datetime,
    ) -> DPoPNonceConsumption:
        connection = self.engine.connect()
        transaction = connection.begin()
        try:
            connection.execute(
                delete(client_trust_replays_table).where(
                    client_trust_replays_table.c.kind == "dpop",
                    client_trust_replays_table.c.expires_at <= _timestamp(at),
                )
            )
            replay = connection.execute(
                sqlite_insert(client_trust_replays_table)
                .values(
                    kind="dpop",
                    jti=jti,
                    consumed_at=_timestamp(at),
                    expires_at=_timestamp(replay_expires_at),
                )
                .prefix_with("OR IGNORE")
            )
            if replay.rowcount != 1:
                transaction.rollback()
                return DPoPNonceConsumption.REPLAY
            consumed = connection.execute(
                update(client_trust_nonces_table)
                .where(
                    client_trust_nonces_table.c.nonce == nonce,
                    client_trust_nonces_table.c.consumed_at.is_(None),
                    client_trust_nonces_table.c.expires_at > _timestamp(at),
                )
                .values(consumed_at=_timestamp(at))
            )
            if consumed.rowcount != 1:
                # A valid signed proof identity is spent even when its nonce is
                # unknown. This prevents one captured proof from minting an
                # unbounded sequence of fresh challenges.
                transaction.commit()
                return DPoPNonceConsumption.INVALID
            transaction.commit()
            return DPoPNonceConsumption.ACCEPTED
        except BaseException:
            transaction.rollback()
            raise
        finally:
            connection.close()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Client Trust timestamps must be timezone-aware")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _permissions(value: str) -> PermissionSet:
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise ValueError("Client Trust permissions must be a JSON array")
    return PermissionCatalog.normalize(decoded)


def _permissions_json(value: frozenset) -> str:
    normalized = PermissionCatalog.normalize(value)
    return json.dumps(
        sorted(permission.value for permission in normalized),
        separators=(",", ":"),
    )


def _scope_values(scope: PairingScope) -> dict[str, str | None]:
    return {
        "agent_id": scope.agent_id,
        "browser_origin": scope.browser_origin.value,
        "agent_endpoint": scope.agent_endpoint.value,
        "database": scope.business.database,
        "company_id": scope.business.company_id,
        "organization_id": scope.business.organization_id,
        "site_id": scope.business.site_id,
        "pos_configuration_id": scope.business.pos_configuration_id,
        "audience": scope.audience,
    }


def _scope(values: Mapping[str, Any]) -> PairingScope:
    return PairingScope(
        agent_id=values["agent_id"],
        browser_origin=BoundOrigin(values["browser_origin"]),
        agent_endpoint=BoundOrigin(values["agent_endpoint"]),
        business=BusinessScope(
            database=values["database"],
            company_id=values["company_id"],
            organization_id=values["organization_id"],
            site_id=values["site_id"],
            pos_configuration_id=values["pos_configuration_id"],
        ),
        audience=values["audience"],
    )


def _pairing_values(pairing: ClientPairing) -> dict[str, object]:
    values: dict[str, object] = {
        "pairing_id": pairing.pairing_id,
        "pairing_request_id": pairing.pairing_request_id,
        "jwk_thumbprint": pairing.jwk_thumbprint,
        "actor_id": pairing.actor_id,
        "role": pairing.role,
        "permissions": _permissions_json(pairing.permissions),
        "created_at": _timestamp(pairing.created_at),
        "expires_at": (
            _timestamp(pairing.expires_at) if pairing.expires_at is not None else None
        ),
        "lifecycle": pairing.lifecycle.value,
        "last_used_at": (
            _timestamp(pairing.last_used_at)
            if pairing.last_used_at is not None
            else None
        ),
    }
    values.update(_scope_values(pairing.scope))
    return values


def _grant_values(grant: ClientGrant) -> dict[str, object]:
    values: dict[str, object] = {
        "grant_id": grant.grant_id,
        "pairing_id": grant.pairing_id,
        "jwk_thumbprint": grant.jwk_thumbprint,
        "actor_id": grant.actor_id,
        "role": grant.role,
        "permissions": _permissions_json(grant.permissions),
        "authorization_digest": grant.authorization_digest,
        "generation": grant.generation,
        "issued_at": _timestamp(grant.issued_at),
        "expires_at": _timestamp(grant.expires_at),
        "offline_renewal_until": (
            _timestamp(grant.offline_renewal_until)
            if grant.offline_renewal_until is not None
            else None
        ),
        "lifecycle": grant.lifecycle.value,
        "last_used_at": (
            _timestamp(grant.last_used_at) if grant.last_used_at is not None else None
        ),
    }
    values.update(_scope_values(grant.scope))
    return values


def _request_values(request: PairingRequest) -> dict[str, object]:
    values: dict[str, object] = {
        "request_id": request.request_id,
        "browser_jwk_thumbprint": request.browser_jwk_thumbprint,
        "requested_permissions": _permissions_json(request.requested_permissions),
        "session_nonce": request.session_nonce,
        "phrase": request.phrase,
        "created_at": _timestamp(request.created_at),
        "expires_at": _timestamp(request.expires_at),
        "state": request.state.value,
    }
    values.update(_scope_values(request.scope))
    return values


def _pairing_from_row(row: Mapping[str, Any]) -> ClientPairing:
    return ClientPairing(
        pairing_id=row["pairing_id"],
        pairing_request_id=row["pairing_request_id"],
        jwk_thumbprint=row["jwk_thumbprint"],
        scope=_scope(row),
        actor_id=row["actor_id"],
        role=row["role"],
        permissions=_permissions(row["permissions"]),
        created_at=_parse_timestamp(row["created_at"]),
        expires_at=_parse_optional_timestamp(row["expires_at"]),
        lifecycle=PairingLifecycle(row["lifecycle"]),
        last_used_at=_parse_optional_timestamp(row["last_used_at"]),
    )


def _grant_from_row(row: Mapping[str, Any]) -> ClientGrant:
    return ClientGrant(
        grant_id=row["grant_id"],
        pairing_id=row["pairing_id"],
        jwk_thumbprint=row["jwk_thumbprint"],
        scope=_scope(row),
        actor_id=row["actor_id"],
        role=row["role"],
        permissions=_permissions(row["permissions"]),
        authorization_digest=row["authorization_digest"],
        generation=row["generation"],
        issued_at=_parse_timestamp(row["issued_at"]),
        expires_at=_parse_timestamp(row["expires_at"]),
        offline_renewal_until=_parse_optional_timestamp(row["offline_renewal_until"]),
        lifecycle=GrantLifecycle(row["lifecycle"]),
        last_used_at=_parse_optional_timestamp(row["last_used_at"]),
    )


def _request_from_row(row: Mapping[str, Any]) -> PairingRequest:
    return PairingRequest(
        request_id=row["request_id"],
        scope=_scope(row),
        browser_jwk_thumbprint=row["browser_jwk_thumbprint"],
        requested_permissions=_permissions(row["requested_permissions"]),
        session_nonce=row["session_nonce"],
        phrase=row["phrase"],
        created_at=_parse_timestamp(row["created_at"]),
        expires_at=_parse_timestamp(row["expires_at"]),
        state=PairingRequestState(row["state"]),
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_optional_timestamp(value: str | None) -> datetime | None:
    return None if value is None else _parse_timestamp(value)


__all__ = ["SqliteClientTrustStore"]
