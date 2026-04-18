from __future__ import annotations

import uuid
from typing import Any, Mapping

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import OperationalError

from ..db.schema import gateway_inbound_commands_table, gateway_outbox_table
from ..runtime.models import normalize_timestamp, timestamp_to_iso, utc_now
from ..runtime.store import RuntimeStore, dump_json, load_json
from .models import (
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
        message_type: str,
        payload: Mapping[str, Any],
    ) -> tuple[GatewayInboundCommandRecord, bool]:
        existing = self.get_inbound_command(command_id)
        if existing is not None:
            return existing, False
        now = utc_now()
        stmt = insert(gateway_inbound_commands_table).values(
            command_id=command_id,
            message_id=message_id,
            sequence=sequence,
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
        with self.store.connection() as connection:
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

    def enqueue_outbound(
        self,
        *,
        message_type: str,
        payload: Mapping[str, Any],
        correlation_id: str | None = None,
        dedupe_key: str | None = None,
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
            created_at=timestamp_to_iso(now),
            updated_at=timestamp_to_iso(now),
            sent_at=None,
            last_error=None,
        )
        with self.store.connection() as connection:
            connection.execute(stmt)
        return self.get_outbox(message_id) or _missing_outbox(message_id)

    def list_pending_outbox(
        self, *, limit: int = 128
    ) -> tuple[GatewayOutboxRecord, ...]:
        stmt = (
            select(gateway_outbox_table)
            .where(gateway_outbox_table.c.state == GatewayOutboxState.PENDING.value)
            .order_by(gateway_outbox_table.c.created_at.asc())
            .limit(limit)
        )
        with self.store.connection() as connection:
            rows = connection.execute(stmt).mappings().all()
        return tuple(_row_to_outbox(row) for row in rows)

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


def _row_to_inbound(row: Mapping[str, Any]) -> GatewayInboundCommandRecord:
    return GatewayInboundCommandRecord(
        command_id=str(row["command_id"]),
        message_type=str(row["message_type"]),
        state=GatewayInboundCommandState(str(row["state"])),
        payload=load_json(str(row["payload_json"])),
        message_id=str(row["message_id"]),
        sequence=int(row["sequence"]) if row["sequence"] is not None else None,
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


def _row_to_outbox(row: Mapping[str, Any]) -> GatewayOutboxRecord:
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


def _missing_outbox(message_id: str) -> GatewayOutboxRecord:
    raise LookupError(f"Missing gateway outbox message {message_id!r}.")
