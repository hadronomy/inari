from __future__ import annotations

import uuid
import json
from dataclasses import asdict
from typing import Any, Mapping

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import OperationalError

from ..core.exceptions import AgentError
from ..db.schema import (
    gateway_inbound_commands_table,
    gateway_managed_dispatch_state_table,
    gateway_outbox_table,
)
from ..runtime.models import normalize_timestamp, timestamp_to_iso, utc_now
from ..runtime.store import RuntimeStore, dump_json, load_json
from .models import (
    AgentManagedScope,
    GatewayInboundCommandRecord,
    GatewayInboundCommandState,
    GatewayOutboxRecord,
    GatewayOutboxState,
)


class GatewayRepository:
    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def record_inbound_command(
        self,
        *,
        command_id: str,
        message_id: str,
        sequence: int | None,
        dispatch_epoch: int | None = None,
        message_type: str,
        payload: Mapping[str, Any],
    ) -> tuple[GatewayInboundCommandRecord, bool]:
        now = utc_now()
        stmt = insert(gateway_inbound_commands_table).values(
            command_id=command_id,
            message_id=message_id,
            sequence=sequence,
            dispatch_epoch=dispatch_epoch,
            message_type=message_type,
            state=GatewayInboundCommandState.RECEIVED.value,
            payload_json=dump_json(payload),
            response_json=None,
            error_code=None,
            error_detail=None,
            job_id=None,
            received_at=timestamp_to_iso(now),
            updated_at=timestamp_to_iso(now),
        )
        with self.store.immediate_transaction() as connection:
            existing_row = (
                connection.execute(
                    select(gateway_inbound_commands_table).where(
                        gateway_inbound_commands_table.c.command_id == command_id
                    )
                )
                .mappings()
                .first()
            )
            if existing_row is not None:
                existing = _row_to_inbound(existing_row)
                _assert_exact_inbound_replay(
                    existing,
                    message_id=message_id,
                    sequence=sequence,
                    dispatch_epoch=dispatch_epoch,
                    message_type=message_type,
                    payload=payload,
                )
                return existing, False
            if sequence is not None:
                last_sequence = connection.execute(
                    select(func.max(gateway_inbound_commands_table.c.sequence)).where(
                        gateway_inbound_commands_table.c.state.in_(
                            (
                                GatewayInboundCommandState.ACCEPTED.value,
                                GatewayInboundCommandState.REJECTED.value,
                            )
                        ),
                        gateway_inbound_commands_table.c.sequence.is_not(None),
                    )
                ).scalar_one_or_none()
                expected = int(last_sequence or 0) + 1
                if sequence != expected:
                    raise AgentError(
                        "UPSTREAM_SEQUENCE_GAP",
                        f"Expected Controller command sequence {expected}, received {sequence}.",
                        status_code=409,
                    )
            if dispatch_epoch is not None:
                state = (
                    connection.execute(select(gateway_managed_dispatch_state_table))
                    .mappings()
                    .first()
                )
                if state is not None and dispatch_epoch < int(state["dispatch_epoch"]):
                    raise AgentError(
                        "MANAGED_DISPATCH_EPOCH_STALE",
                        "The managed dispatch epoch is older than the applied epoch.",
                        status_code=409,
                    )
            connection.execute(stmt)
        return self.get_inbound_command(command_id) or _missing_inbound(
            command_id
        ), True

    def last_applied_controller_sequence(self) -> int | None:
        stmt = select(func.max(gateway_inbound_commands_table.c.sequence)).where(
            gateway_inbound_commands_table.c.state.in_(
                (
                    GatewayInboundCommandState.ACCEPTED.value,
                    GatewayInboundCommandState.REJECTED.value,
                )
            ),
            gateway_inbound_commands_table.c.sequence.is_not(None),
        )
        try:
            with self.store.connection() as connection:
                value = connection.execute(stmt).scalar_one_or_none()
        except OperationalError:
            return None
        return int(value) if value is not None else None

    def get_inbound_command(
        self, command_id: str
    ) -> GatewayInboundCommandRecord | None:
        stmt = select(gateway_inbound_commands_table).where(
            gateway_inbound_commands_table.c.command_id == command_id
        )
        with self.store.connection() as connection:
            row = connection.execute(stmt).mappings().first()
        return _row_to_inbound(row) if row is not None else None

    def get_inbound_command_for_job(
        self, job_id: str
    ) -> GatewayInboundCommandRecord | None:
        stmt = (
            select(gateway_inbound_commands_table)
            .where(gateway_inbound_commands_table.c.job_id == job_id)
            .order_by(gateway_inbound_commands_table.c.updated_at.desc())
            .limit(1)
        )
        with self.store.connection() as connection:
            row = connection.execute(stmt).mappings().first()
        return _row_to_inbound(row) if row is not None else None

    def mark_inbound_accepted(
        self,
        command_id: str,
        *,
        job_id: str | None,
        response_payload: Mapping[str, Any],
    ) -> GatewayInboundCommandRecord:
        now = utc_now()
        stmt = (
            update(gateway_inbound_commands_table)
            .where(gateway_inbound_commands_table.c.command_id == command_id)
            .values(
                state=GatewayInboundCommandState.ACCEPTED.value,
                job_id=job_id,
                response_json=dump_json(response_payload),
                error_code=None,
                error_detail=None,
                updated_at=timestamp_to_iso(now),
            )
        )
        with self.store.connection() as connection:
            connection.execute(stmt)
        return self.get_inbound_command(command_id) or _missing_inbound(command_id)

    def mark_inbound_rejected(
        self,
        command_id: str,
        *,
        error_code: str,
        error_detail: str,
        response_payload: Mapping[str, Any],
    ) -> GatewayInboundCommandRecord:
        now = utc_now()
        stmt = (
            update(gateway_inbound_commands_table)
            .where(gateway_inbound_commands_table.c.command_id == command_id)
            .values(
                state=GatewayInboundCommandState.REJECTED.value,
                response_json=dump_json(response_payload),
                error_code=error_code,
                error_detail=error_detail,
                updated_at=timestamp_to_iso(now),
            )
        )
        with self.store.connection() as connection:
            connection.execute(stmt)
        return self.get_inbound_command(command_id) or _missing_inbound(command_id)

    def mark_managed_dispatch_accepted(
        self,
        command_id: str,
        *,
        dispatch_epoch: int,
        job_id: str,
        response_payload: Mapping[str, Any],
    ) -> GatewayInboundCommandRecord:
        return self._finish_managed_dispatch(
            command_id,
            dispatch_epoch=dispatch_epoch,
            state=GatewayInboundCommandState.ACCEPTED,
            job_id=job_id,
            response_payload=response_payload,
            error_code=None,
            error_detail=None,
        )

    def mark_managed_dispatch_rejected(
        self,
        command_id: str,
        *,
        dispatch_epoch: int,
        error_code: str,
        error_detail: str,
        response_payload: Mapping[str, Any],
    ) -> GatewayInboundCommandRecord:
        return self._finish_managed_dispatch(
            command_id,
            dispatch_epoch=dispatch_epoch,
            state=GatewayInboundCommandState.REJECTED,
            job_id=None,
            response_payload=response_payload,
            error_code=error_code,
            error_detail=error_detail,
        )

    def _finish_managed_dispatch(
        self,
        command_id: str,
        *,
        dispatch_epoch: int,
        state: GatewayInboundCommandState,
        job_id: str | None,
        response_payload: Mapping[str, Any],
        error_code: str | None,
        error_detail: str | None,
    ) -> GatewayInboundCommandRecord:
        now = utc_now()
        with self.store.immediate_transaction() as connection:
            inbound = (
                connection.execute(
                    select(gateway_inbound_commands_table).where(
                        gateway_inbound_commands_table.c.command_id == command_id
                    )
                )
                .mappings()
                .one()
            )
            if inbound["dispatch_epoch"] != dispatch_epoch:
                raise AgentError(
                    "MANAGED_DISPATCH_EPOCH_INVALID",
                    "The stored dispatch epoch does not match the accepted command.",
                    status_code=409,
                )
            if inbound["state"] in {
                GatewayInboundCommandState.ACCEPTED.value,
                GatewayInboundCommandState.REJECTED.value,
            }:
                return _row_to_inbound(inbound)
            sequence = int(inbound["sequence"])
            dispatch_state = (
                connection.execute(select(gateway_managed_dispatch_state_table))
                .mappings()
                .first()
            )
            if dispatch_state is None:
                connection.execute(
                    insert(gateway_managed_dispatch_state_table).values(
                        id=1,
                        dispatch_epoch=dispatch_epoch,
                        last_sequence=sequence,
                        updated_at=timestamp_to_iso(now),
                    )
                )
            else:
                stored_epoch = int(dispatch_state["dispatch_epoch"])
                stored_sequence = int(dispatch_state["last_sequence"])
                if dispatch_epoch < stored_epoch or sequence <= stored_sequence:
                    raise AgentError(
                        "MANAGED_DISPATCH_REPLAY_REJECTED",
                        "The managed dispatch position was already applied.",
                        status_code=409,
                    )
                connection.execute(
                    update(gateway_managed_dispatch_state_table)
                    .where(gateway_managed_dispatch_state_table.c.id == 1)
                    .values(
                        dispatch_epoch=dispatch_epoch,
                        last_sequence=sequence,
                        updated_at=timestamp_to_iso(now),
                    )
                )
            connection.execute(
                update(gateway_inbound_commands_table)
                .where(gateway_inbound_commands_table.c.command_id == command_id)
                .values(
                    state=state.value,
                    job_id=job_id,
                    response_json=dump_json(response_payload),
                    error_code=error_code,
                    error_detail=error_detail,
                    updated_at=timestamp_to_iso(now),
                )
            )
        return self.get_inbound_command(command_id) or _missing_inbound(command_id)

    def enqueue_outbound(
        self,
        *,
        message_type: str,
        payload: Mapping[str, Any],
        correlation_id: str | None = None,
        dedupe_key: str | None = None,
        recipient_scope: AgentManagedScope | None = None,
    ) -> GatewayOutboxRecord:
        if dedupe_key is not None:
            existing = self.find_outbox_by_dedupe_key(dedupe_key)
            if existing is not None:
                return existing
        now = utc_now()
        message_id = str(payload.get("message_id") or f"gout_{uuid.uuid4().hex}")
        stmt = insert(gateway_outbox_table).values(
            message_id=message_id,
            message_type=message_type,
            state=GatewayOutboxState.PENDING.value,
            payload_json=dump_json(payload),
            correlation_id=correlation_id,
            dedupe_key=dedupe_key,
            recipient_scope=recipient_scope_key(recipient_scope),
            created_at=timestamp_to_iso(now),
            updated_at=timestamp_to_iso(now),
            sent_at=None,
            last_error=None,
        )
        with self.store.connection() as connection:
            connection.execute(stmt)
        return self.get_outbox(message_id) or _missing_outbox(message_id)

    def list_pending_outbox(
        self, *, limit: int = 128, recipient_scope: AgentManagedScope | None = None
    ) -> tuple[GatewayOutboxRecord, ...]:
        stmt = (
            select(gateway_outbox_table)
            .where(gateway_outbox_table.c.state == GatewayOutboxState.PENDING.value)
            .where(
                or_(
                    gateway_outbox_table.c.recipient_scope.is_(None),
                    gateway_outbox_table.c.recipient_scope
                    == recipient_scope_key(recipient_scope),
                )
            )
            .order_by(
                gateway_outbox_table.c.updated_at.asc(),
                gateway_outbox_table.c.created_at.asc(),
                gateway_outbox_table.c.message_id.asc(),
            )
            .limit(limit)
        )
        with self.store.connection() as connection:
            rows = connection.execute(stmt).mappings().all()
        return tuple(_row_to_outbox(row) for row in rows)

    def discard_pending_outbound(self, message_id: str) -> None:
        """Remove an unpublished event without recording transport delivery."""
        stmt = delete(gateway_outbox_table).where(
            gateway_outbox_table.c.message_id == message_id,
            gateway_outbox_table.c.state == GatewayOutboxState.PENDING.value,
        )
        with self.store.connection() as connection:
            connection.execute(stmt)

    def mark_outbox_sent(self, message_id: str) -> GatewayOutboxRecord | None:
        now = utc_now()
        stmt = (
            update(gateway_outbox_table)
            .where(gateway_outbox_table.c.message_id == message_id)
            .values(
                state=GatewayOutboxState.SENT.value,
                sent_at=timestamp_to_iso(now),
                updated_at=timestamp_to_iso(now),
                last_error=None,
            )
        )
        with self.store.connection() as connection:
            connection.execute(stmt)
        return self.get_outbox(message_id)

    def mark_outbox_failed(
        self, message_id: str, *, detail: str
    ) -> GatewayOutboxRecord | None:
        now = utc_now()
        stmt = (
            update(gateway_outbox_table)
            .where(gateway_outbox_table.c.message_id == message_id)
            .values(
                state=GatewayOutboxState.PENDING.value,
                updated_at=timestamp_to_iso(now),
                last_error=detail,
            )
        )
        with self.store.connection() as connection:
            connection.execute(stmt)
        return self.get_outbox(message_id)

    def requeue_outbound(self, message_id: str) -> GatewayOutboxRecord | None:
        now = utc_now()
        stmt = (
            update(gateway_outbox_table)
            .where(gateway_outbox_table.c.message_id == message_id)
            .values(
                state=GatewayOutboxState.PENDING.value,
                updated_at=timestamp_to_iso(now),
                sent_at=None,
                last_error=None,
            )
        )
        with self.store.connection() as connection:
            connection.execute(stmt)
        return self.get_outbox(message_id)

    def get_outbox(self, message_id: str) -> GatewayOutboxRecord | None:
        stmt = select(gateway_outbox_table).where(
            gateway_outbox_table.c.message_id == message_id
        )
        with self.store.connection() as connection:
            row = connection.execute(stmt).mappings().first()
        return _row_to_outbox(row) if row is not None else None

    def find_outbox_by_dedupe_key(self, dedupe_key: str) -> GatewayOutboxRecord | None:
        stmt = (
            select(gateway_outbox_table)
            .where(gateway_outbox_table.c.dedupe_key == dedupe_key)
            .order_by(gateway_outbox_table.c.created_at.desc())
            .limit(1)
        )
        with self.store.connection() as connection:
            row = connection.execute(stmt).mappings().first()
        return _row_to_outbox(row) if row is not None else None

    def summary(self) -> dict[str, int]:
        inbound_stmt = select(
            gateway_inbound_commands_table.c.state, func.count().label("total")
        ).group_by(gateway_inbound_commands_table.c.state)
        outbox_stmt = select(
            gateway_outbox_table.c.state, func.count().label("total")
        ).group_by(gateway_outbox_table.c.state)
        with self.store.connection() as connection:
            inbound_rows = connection.execute(inbound_stmt).mappings().all()
            outbox_rows = connection.execute(outbox_stmt).mappings().all()
        summary: dict[str, int] = {}
        for row in inbound_rows:
            summary[f"inbound_{row['state']}"] = int(row["total"])
        for row in outbox_rows:
            summary[f"outbox_{row['state']}"] = int(row["total"])
        return summary


def _row_to_inbound(row: RowMapping | Mapping[str, Any]) -> GatewayInboundCommandRecord:
    return GatewayInboundCommandRecord(
        command_id=str(row["command_id"]),
        message_type=str(row["message_type"]),
        state=GatewayInboundCommandState(str(row["state"])),
        payload=load_json(str(row["payload_json"])),
        message_id=str(row["message_id"]),
        sequence=int(row["sequence"]) if row["sequence"] is not None else None,
        dispatch_epoch=(
            int(row["dispatch_epoch"]) if row["dispatch_epoch"] is not None else None
        ),
        received_at=normalize_timestamp(str(row["received_at"])) or utc_now(),
        updated_at=normalize_timestamp(str(row["updated_at"])) or utc_now(),
        job_id=str(row["job_id"]) if row["job_id"] is not None else None,
        response_payload=load_json(str(row["response_json"]))
        if row["response_json"] is not None
        else None,
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        error_detail=str(row["error_detail"])
        if row["error_detail"] is not None
        else None,
    )


def _row_to_outbox(row: RowMapping | Mapping[str, Any]) -> GatewayOutboxRecord:
    return GatewayOutboxRecord(
        message_id=str(row["message_id"]),
        message_type=str(row["message_type"]),
        state=GatewayOutboxState(str(row["state"])),
        payload=load_json(str(row["payload_json"])),
        created_at=normalize_timestamp(str(row["created_at"])) or utc_now(),
        updated_at=normalize_timestamp(str(row["updated_at"])) or utc_now(),
        correlation_id=str(row["correlation_id"])
        if row["correlation_id"] is not None
        else None,
        dedupe_key=str(row["dedupe_key"]) if row["dedupe_key"] is not None else None,
        sent_at=normalize_timestamp(str(row["sent_at"]))
        if row["sent_at"] is not None
        else None,
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
    )


def _missing_inbound(command_id: str) -> GatewayInboundCommandRecord:
    raise LookupError(f"Missing gateway inbound command {command_id!r}.")


def _assert_exact_inbound_replay(
    existing: GatewayInboundCommandRecord,
    *,
    message_id: str,
    sequence: int | None,
    dispatch_epoch: int | None,
    message_type: str,
    payload: Mapping[str, Any],
) -> None:
    if (
        existing.message_id != message_id
        or existing.sequence != sequence
        or existing.dispatch_epoch != dispatch_epoch
        or existing.message_type != message_type
        or existing.payload != dict(payload)
    ):
        raise AgentError(
            "UPSTREAM_COMMAND_REPLAY_CONFLICT",
            "The Controller command ID was reused with different content.",
            status_code=409,
        )


def _missing_outbox(message_id: str) -> GatewayOutboxRecord:
    raise LookupError(f"Missing gateway outbox message {message_id!r}.")


def recipient_scope_key(scope: AgentManagedScope | None) -> str | None:
    return (
        None
        if scope is None
        else json.dumps(
            asdict(scope), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )
