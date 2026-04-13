from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Any, Mapping

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
        message_type: str,
        payload: Mapping[str, Any],
    ) -> tuple[GatewayInboundCommandRecord, bool]:
        existing = self.get_inbound_command(command_id)
        if existing is not None:
            return existing, False
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute(
                """
                INSERT INTO gateway_inbound_commands (
                    command_id, message_id, message_type, state,
                    payload_json, response_json, error_code, error_detail,
                    job_id, received_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    message_id,
                    message_type,
                    GatewayInboundCommandState.RECEIVED.value,
                    dump_json(payload),
                    None,
                    None,
                    None,
                    None,
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                ),
            )
        return self.get_inbound_command(command_id) or _missing_inbound(command_id), True

    def get_inbound_command(self, command_id: str) -> GatewayInboundCommandRecord | None:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM gateway_inbound_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return _row_to_inbound(row) if row is not None else None

    def get_inbound_command_for_job(self, job_id: str) -> GatewayInboundCommandRecord | None:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM gateway_inbound_commands WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        return _row_to_inbound(row) if row is not None else None

    def mark_inbound_accepted(
        self,
        command_id: str,
        *,
        job_id: str | None,
        response_payload: Mapping[str, Any],
    ) -> GatewayInboundCommandRecord:
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute(
                """
                UPDATE gateway_inbound_commands
                SET state = ?, job_id = ?, response_json = ?, error_code = NULL, error_detail = NULL, updated_at = ?
                WHERE command_id = ?
                """,
                (
                    GatewayInboundCommandState.ACCEPTED.value,
                    job_id,
                    dump_json(response_payload),
                    timestamp_to_iso(now),
                    command_id,
                ),
            )
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
        with self.store.connection() as connection:
            connection.execute(
                """
                UPDATE gateway_inbound_commands
                SET state = ?, response_json = ?, error_code = ?, error_detail = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (
                    GatewayInboundCommandState.REJECTED.value,
                    dump_json(response_payload),
                    error_code,
                    error_detail,
                    timestamp_to_iso(now),
                    command_id,
                ),
            )
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
        with self.store.connection() as connection:
            connection.execute(
                """
                INSERT INTO gateway_outbox (
                    message_id, message_type, state, payload_json, correlation_id, dedupe_key,
                    created_at, updated_at, sent_at, acknowledged_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    message_type,
                    GatewayOutboxState.PENDING.value,
                    dump_json(payload),
                    correlation_id,
                    dedupe_key,
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                    None,
                    None,
                    None,
                ),
            )
        return self.get_outbox(message_id) or _missing_outbox(message_id)

    def list_pending_outbox(self, *, limit: int = 128) -> tuple[GatewayOutboxRecord, ...]:
        with self.store.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM gateway_outbox
                WHERE state IN (?, ?)
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (
                    GatewayOutboxState.PENDING.value,
                    GatewayOutboxState.SENT.value,
                    limit,
                ),
            ).fetchall()
        return tuple(_row_to_outbox(row) for row in rows)

    def mark_outbox_sent(self, message_id: str) -> GatewayOutboxRecord | None:
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute(
                """
                UPDATE gateway_outbox
                SET state = ?, sent_at = ?, updated_at = ?, last_error = NULL
                WHERE message_id = ?
                """,
                (
                    GatewayOutboxState.SENT.value,
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                    message_id,
                ),
            )
        return self.get_outbox(message_id)

    def mark_outbox_acked(self, message_id: str) -> GatewayOutboxRecord | None:
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute(
                """
                UPDATE gateway_outbox
                SET state = ?, acknowledged_at = ?, updated_at = ?
                WHERE message_id = ?
                """,
                (
                    GatewayOutboxState.ACKNOWLEDGED.value,
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                    message_id,
                ),
            )
        return self.get_outbox(message_id)

    def mark_outbox_failed(self, message_id: str, *, detail: str) -> GatewayOutboxRecord | None:
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute(
                """
                UPDATE gateway_outbox
                SET state = ?, updated_at = ?, last_error = ?
                WHERE message_id = ?
                """,
                (
                    GatewayOutboxState.PENDING.value,
                    timestamp_to_iso(now),
                    detail,
                    message_id,
                ),
            )
        return self.get_outbox(message_id)

    def get_outbox(self, message_id: str) -> GatewayOutboxRecord | None:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM gateway_outbox WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return _row_to_outbox(row) if row is not None else None

    def find_outbox_by_dedupe_key(self, dedupe_key: str) -> GatewayOutboxRecord | None:
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM gateway_outbox WHERE dedupe_key = ? ORDER BY created_at DESC LIMIT 1",
                (dedupe_key,),
            ).fetchone()
        return _row_to_outbox(row) if row is not None else None

    def summary(self) -> dict[str, int]:
        with self.store.connection() as connection:
            inbound_rows = connection.execute(
                "SELECT state, COUNT(*) AS total FROM gateway_inbound_commands GROUP BY state"
            ).fetchall()
            outbox_rows = connection.execute(
                "SELECT state, COUNT(*) AS total FROM gateway_outbox GROUP BY state"
            ).fetchall()
        summary: dict[str, int] = {}
        for row in inbound_rows:
            summary[f"inbound_{row['state']}"] = int(row["total"])
        for row in outbox_rows:
            summary[f"outbox_{row['state']}"] = int(row["total"])
        return summary


def _row_to_inbound(row: sqlite3.Row) -> GatewayInboundCommandRecord:
    return GatewayInboundCommandRecord(
        command_id=str(row["command_id"]),
        message_type=str(row["message_type"]),
        state=GatewayInboundCommandState(str(row["state"])),
        payload=load_json(str(row["payload_json"])),
        message_id=str(row["message_id"]),
        received_at=normalize_timestamp(str(row["received_at"])) or utc_now(),
        updated_at=normalize_timestamp(str(row["updated_at"])) or utc_now(),
        job_id=str(row["job_id"]) if row["job_id"] is not None else None,
        response_payload=load_json(str(row["response_json"])) if row["response_json"] is not None else None,
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        error_detail=str(row["error_detail"]) if row["error_detail"] is not None else None,
    )


def _row_to_outbox(row: sqlite3.Row) -> GatewayOutboxRecord:
    return GatewayOutboxRecord(
        message_id=str(row["message_id"]),
        message_type=str(row["message_type"]),
        state=GatewayOutboxState(str(row["state"])),
        payload=load_json(str(row["payload_json"])),
        created_at=normalize_timestamp(str(row["created_at"])) or utc_now(),
        updated_at=normalize_timestamp(str(row["updated_at"])) or utc_now(),
        correlation_id=str(row["correlation_id"]) if row["correlation_id"] is not None else None,
        dedupe_key=str(row["dedupe_key"]) if row["dedupe_key"] is not None else None,
        sent_at=normalize_timestamp(str(row["sent_at"])) if row["sent_at"] is not None else None,
        acknowledged_at=normalize_timestamp(str(row["acknowledged_at"]))
        if row["acknowledged_at"] is not None
        else None,
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
    )


def _missing_inbound(command_id: str) -> GatewayInboundCommandRecord:
    raise LookupError(f"Missing gateway inbound command {command_id!r}.")


def _missing_outbox(message_id: str) -> GatewayOutboxRecord:
    raise LookupError(f"Missing gateway outbox message {message_id!r}.")
