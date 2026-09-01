from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

from ..client_trust import AuthorizedRequest
from ..db.schema import drawer_intents_table
from ..printing.protocols import PrintJobResult
from ..runtime.store import RuntimeStore
from ..spool.manifest import parse_timestamp, timestamp
from .models import (
    DrawerIntentRecord,
    DrawerIntentRequest,
    DrawerIntentState,
    DrawerLedgerAdmission,
    DrawerReason,
)


_RETENTION = timedelta(days=90)


class SqliteDrawerIntentLedger:
    """Persist content-free Drawer Intents and guarded lifecycle transitions."""

    def __init__(self, store: RuntimeStore) -> None:
        self._store = store

    def find(
        self,
        *,
        intent_id: str,
        database: str,
        organization_id: str,
        site_id: str,
        pos_configuration_id: str,
        paired_client_id: str,
        now: datetime,
    ) -> DrawerIntentRecord | None:
        with self._store.connection() as connection:
            _purge(connection, now)
            row = _find_scoped(
                connection,
                intent_id=intent_id,
                database=database,
                organization_id=organization_id,
                site_id=site_id,
                pos_configuration_id=pos_configuration_id,
                paired_client_id=paired_client_id,
            )
        return _record(row) if row is not None else None

    def admit(
        self,
        request: DrawerIntentRequest,
        *,
        authorization: AuthorizedRequest,
        fingerprint: bytes,
        now: datetime,
    ) -> DrawerLedgerAdmission:
        business = authorization.grant.scope.business
        assert business.pos_configuration_id is not None
        accepted = now.astimezone(UTC)
        values = {
            "id": uuid4().hex,
            "intent_id": request.intent_id,
            "database": business.database,
            "organization_id": business.organization_id,
            "site_id": business.site_id,
            "pos_configuration_id": business.pos_configuration_id,
            "paired_client_id": authorization.grant.pairing_id,
            "actor_id": authorization.grant.actor_id,
            "device_id": request.device_id,
            "binding_revision_id": request.binding_revision_id,
            "pos_session_id": request.pos_session_id,
            "action_sequence": request.action_sequence,
            "reason": request.reason.value,
            "contract_major": 1,
            "fingerprint": fingerprint,
            "state": DrawerIntentState.ACCEPTED.value,
            "state_version": 1,
            "accepted_at": timestamp(accepted),
            "expires_at": timestamp(accepted + _RETENTION),
            "updated_at": timestamp(accepted),
        }
        with self._store.immediate_transaction() as connection:
            _purge(connection, accepted)
            result = connection.execute(
                sqlite_insert(drawer_intents_table)
                .values(**values)
                .on_conflict_do_nothing()
            )
            row = _find_scoped(
                connection,
                intent_id=request.intent_id,
                database=business.database,
                organization_id=business.organization_id,
                site_id=business.site_id,
                pos_configuration_id=business.pos_configuration_id,
                paired_client_id=authorization.grant.pairing_id,
            )
            if row is None:
                row = _find_action(
                    connection,
                    database=business.database,
                    organization_id=business.organization_id,
                    site_id=business.site_id,
                    pos_configuration_id=business.pos_configuration_id,
                    pos_session_id=request.pos_session_id,
                    actor_id=authorization.grant.actor_id,
                    device_id=request.device_id,
                    action_sequence=request.action_sequence,
                )
        if row is None:
            raise RuntimeError("The admitted Drawer Intent could not be read.")
        return DrawerLedgerAdmission(record=_record(row), created=result.rowcount == 1)

    def rearm_before_io(
        self, record_id: str, *, now: datetime
    ) -> DrawerIntentRecord | None:
        return self._transition(
            record_id,
            expected_state=DrawerIntentState.FAILED,
            values={
                "state": DrawerIntentState.ACCEPTED.value,
                "terminal_at": None,
                "error_code": None,
                "message_key": None,
                "updated_at": timestamp(now),
                "state_version": drawer_intents_table.c.state_version + 1,
            },
            extra_predicates=(drawer_intents_table.c.started_at.is_(None),),
        )

    def mark_io_started(
        self, record_id: str, *, now: datetime
    ) -> DrawerIntentRecord | None:
        return self._transition(
            record_id,
            expected_state=DrawerIntentState.ACCEPTED,
            values={
                "state": DrawerIntentState.IN_PROGRESS.value,
                "started_at": timestamp(now),
                "updated_at": timestamp(now),
                "state_version": drawer_intents_table.c.state_version + 1,
            },
        )

    def mark_succeeded(
        self,
        record_id: str,
        *,
        result: PrintJobResult,
        now: datetime,
    ) -> DrawerIntentRecord | None:
        return self._transition(
            record_id,
            expected_state=DrawerIntentState.IN_PROGRESS,
            values={
                "state": DrawerIntentState.SUCCEEDED.value,
                "terminal_at": timestamp(now),
                "updated_at": timestamp(now),
                "state_version": drawer_intents_table.c.state_version + 1,
                "printer_name": result.printer_name,
                "transport": result.transport.value,
            },
        )

    def mark_terminal(
        self,
        record_id: str,
        *,
        expected_state: DrawerIntentState,
        state: DrawerIntentState,
        error_code: str,
        message_key: str,
        now: datetime,
    ) -> DrawerIntentRecord | None:
        if state not in {DrawerIntentState.FAILED, DrawerIntentState.OUTCOME_UNKNOWN}:
            raise ValueError("Drawer terminal failure state is invalid.")
        return self._transition(
            record_id,
            expected_state=expected_state,
            values={
                "state": state.value,
                "terminal_at": timestamp(now),
                "updated_at": timestamp(now),
                "state_version": drawer_intents_table.c.state_version + 1,
                "error_code": error_code,
                "message_key": message_key,
            },
        )

    def expire_stale_in_progress(self, *, cutoff: datetime, now: datetime) -> None:
        with self._store.immediate_transaction() as connection:
            connection.execute(
                update(drawer_intents_table)
                .where(
                    drawer_intents_table.c.state == DrawerIntentState.IN_PROGRESS.value,
                    drawer_intents_table.c.started_at <= timestamp(cutoff),
                )
                .values(
                    state=DrawerIntentState.OUTCOME_UNKNOWN.value,
                    terminal_at=timestamp(now),
                    updated_at=timestamp(now),
                    state_version=drawer_intents_table.c.state_version + 1,
                    error_code="outcome_unknown",
                    message_key="drawer.outcome_unknown",
                )
            )

    def query(
        self,
        *,
        intent_ids: tuple[str, ...],
        database: str,
        organization_id: str,
        site_id: str,
        pos_configuration_id: str,
        paired_client_id: str,
        now: datetime,
    ) -> tuple[DrawerIntentRecord, ...]:
        if not intent_ids:
            return ()
        with self._store.connection() as connection:
            _purge(connection, now)
            rows = tuple(
                connection.execute(
                    select(drawer_intents_table).where(
                        drawer_intents_table.c.intent_id.in_(intent_ids),
                        drawer_intents_table.c.database == database,
                        drawer_intents_table.c.organization_id == organization_id,
                        drawer_intents_table.c.site_id == site_id,
                        drawer_intents_table.c.pos_configuration_id
                        == pos_configuration_id,
                        drawer_intents_table.c.paired_client_id == paired_client_id,
                    )
                ).mappings()
            )
        return tuple(_record(row) for row in rows)

    def _transition(
        self,
        record_id: str,
        *,
        expected_state: DrawerIntentState,
        values: dict[str, object],
        extra_predicates: tuple[object, ...] = (),
    ) -> DrawerIntentRecord | None:
        with self._store.immediate_transaction() as connection:
            result = connection.execute(
                update(drawer_intents_table)
                .where(
                    drawer_intents_table.c.id == record_id,
                    drawer_intents_table.c.state == expected_state.value,
                    *extra_predicates,
                )
                .values(**values)
            )
            row = (
                connection.execute(
                    select(drawer_intents_table).where(
                        drawer_intents_table.c.id == record_id
                    )
                )
                .mappings()
                .first()
            )
        if result.rowcount != 1 or row is None:
            return None
        return _record(row)


def _find_scoped(connection: Connection, **scope: str):
    return (
        connection.execute(
            select(drawer_intents_table).where(
                drawer_intents_table.c.intent_id == scope["intent_id"],
                drawer_intents_table.c.database == scope["database"],
                drawer_intents_table.c.organization_id == scope["organization_id"],
                drawer_intents_table.c.site_id == scope["site_id"],
                drawer_intents_table.c.pos_configuration_id
                == scope["pos_configuration_id"],
                drawer_intents_table.c.paired_client_id == scope["paired_client_id"],
            )
        )
        .mappings()
        .first()
    )


def _find_action(connection: Connection, **action: str | int):
    return (
        connection.execute(
            select(drawer_intents_table).where(
                drawer_intents_table.c.database == action["database"],
                drawer_intents_table.c.organization_id == action["organization_id"],
                drawer_intents_table.c.site_id == action["site_id"],
                drawer_intents_table.c.pos_configuration_id
                == action["pos_configuration_id"],
                drawer_intents_table.c.pos_session_id == action["pos_session_id"],
                drawer_intents_table.c.actor_id == action["actor_id"],
                drawer_intents_table.c.device_id == action["device_id"],
                drawer_intents_table.c.action_sequence == action["action_sequence"],
            )
        )
        .mappings()
        .first()
    )


def _purge(connection: Connection, now: datetime) -> None:
    connection.execute(
        delete(drawer_intents_table).where(
            drawer_intents_table.c.expires_at <= timestamp(now)
        )
    )


def _record(row: Any) -> DrawerIntentRecord:
    return DrawerIntentRecord(
        record_id=str(row["id"]),
        intent_id=str(row["intent_id"]),
        database=str(row["database"]),
        organization_id=str(row["organization_id"]),
        site_id=str(row["site_id"]),
        pos_configuration_id=str(row["pos_configuration_id"]),
        paired_client_id=str(row["paired_client_id"]),
        actor_id=str(row["actor_id"]),
        device_id=str(row["device_id"]),
        binding_revision_id=str(row["binding_revision_id"]),
        pos_session_id=str(row["pos_session_id"]),
        action_sequence=int(row["action_sequence"]),
        reason=DrawerReason(str(row["reason"])),
        fingerprint=bytes(row["fingerprint"]),
        state=DrawerIntentState(str(row["state"])),
        state_version=int(row["state_version"]),
        accepted_at=parse_timestamp(row["accepted_at"]),
        expires_at=parse_timestamp(row["expires_at"]),
        started_at=_optional_timestamp(row.get("started_at")),
        terminal_at=_optional_timestamp(row.get("terminal_at")),
        error_code=_optional_text(row.get("error_code")),
        message_key=_optional_text(row.get("message_key")),
        printer_name=_optional_text(row.get("printer_name")),
        transport=_optional_text(row.get("transport")),
    )


def _optional_timestamp(value: object) -> datetime | None:
    return parse_timestamp(value) if value is not None else None


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


__all__ = ["SqliteDrawerIntentLedger"]
