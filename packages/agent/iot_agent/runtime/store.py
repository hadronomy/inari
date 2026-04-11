from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..drivers import DeviceKind
from ..printers import PrinterTransport
from .models import (
    DeviceConnectionState,
    DeviceEventRecord,
    DeviceRecord,
    JobAttemptRecord,
    JobEventRecord,
    JobKind,
    JobRecord,
    JobState,
    normalize_timestamp,
    timestamp_to_iso,
    utc_now,
)


class RuntimeStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    driver_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    connection_state TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_default INTEGER NOT NULL,
                    preferred_transport TEXT,
                    capabilities_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS device_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY (device_id) REFERENCES devices(id)
                );

                CREATE INDEX IF NOT EXISTS idx_device_events_device_id
                ON device_events(device_id, sequence DESC);

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    device_kind TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    request_metadata_json TEXT NOT NULL,
                    content_kind TEXT,
                    command_kind TEXT,
                    attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    next_run_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    lease_expires_at TEXT,
                    result_json TEXT,
                    last_error_code TEXT,
                    last_error_detail TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_state_next_run_at
                ON jobs(state, next_run_at, created_at);

                CREATE INDEX IF NOT EXISTS idx_jobs_device_id
                ON jobs(device_id, created_at);

                CREATE TABLE IF NOT EXISTS job_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_code TEXT,
                    error_detail TEXT,
                    result_json TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_job_attempts_job_id
                ON job_attempts(job_id, attempt_number DESC);

                CREATE TABLE IF NOT EXISTS job_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_job_events_job_id
                ON job_events(job_id, sequence DESC);
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
            finally:
                connection.close()

    def upsert_device(self, record: DeviceRecord) -> DeviceRecord:
        existing = self.get_device(record.id)
        first_seen_at = existing.first_seen_at if existing is not None else record.first_seen_at
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO devices (
                    id, kind, driver_key, name, connection_state,
                    first_seen_at, last_seen_at, updated_at,
                    is_default, preferred_transport, capabilities_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    driver_key = excluded.driver_key,
                    name = excluded.name,
                    connection_state = excluded.connection_state,
                    last_seen_at = excluded.last_seen_at,
                    updated_at = excluded.updated_at,
                    is_default = excluded.is_default,
                    preferred_transport = excluded.preferred_transport,
                    capabilities_json = excluded.capabilities_json,
                    metadata_json = excluded.metadata_json
                """,
                (
                    record.id,
                    record.kind.value,
                    record.driver_key,
                    record.name,
                    record.connection_state.value,
                    timestamp_to_iso(first_seen_at),
                    timestamp_to_iso(record.last_seen_at),
                    timestamp_to_iso(record.updated_at),
                    1 if record.is_default else 0,
                    record.preferred_transport.value if record.preferred_transport is not None else None,
                    _dump_json(record.capabilities),
                    _dump_json(record.metadata),
                ),
            )
        return self.get_device(record.id) or record

    def get_device(self, device_id: str) -> DeviceRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return _row_to_device(row) if row is not None else None

    def list_devices(self, *, kind: DeviceKind | None = None) -> tuple[DeviceRecord, ...]:
        query = "SELECT * FROM devices"
        params: tuple[object, ...] = ()
        if kind is not None:
            query += " WHERE kind = ?"
            params = (kind.value,)
        query += " ORDER BY name COLLATE NOCASE"
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_row_to_device(row) for row in rows)

    def create_device_event(
        self,
        *,
        device_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> DeviceEventRecord:
        occurred_at = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO device_events (device_id, event_type, payload_json, occurred_at) VALUES (?, ?, ?, ?)",
                (device_id, event_type, _dump_json(payload), timestamp_to_iso(occurred_at)),
            )
            sequence = int(cursor.lastrowid)
        return DeviceEventRecord(
            sequence=sequence,
            resource_id=device_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=dict(payload),
        )

    def list_device_events(self, device_id: str, *, limit: int = 50) -> tuple[DeviceEventRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM device_events WHERE device_id = ? ORDER BY sequence DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()
        return tuple(_row_to_device_event(row) for row in rows)

    def create_job(
        self,
        *,
        kind: JobKind,
        operation: str,
        device_id: str,
        device_kind: DeviceKind,
        device_name: str,
        request_payload: Mapping[str, Any],
        request_metadata: Mapping[str, Any],
        content_kind: str | None,
        command_kind: str | None,
        max_attempts: int,
    ) -> JobRecord:
        now = utc_now()
        job_id = f"job_{uuid.uuid4().hex}"
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, kind, operation, device_id, device_kind, device_name, state,
                    request_json, request_metadata_json, content_kind, command_kind,
                    attempt_count, max_attempts, created_at, updated_at, queued_at, next_run_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    kind.value,
                    operation,
                    device_id,
                    device_kind.value,
                    device_name,
                    JobState.QUEUED.value,
                    _dump_json(request_payload),
                    _dump_json(request_metadata),
                    content_kind,
                    command_kind,
                    0,
                    max_attempts,
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                ),
            )
        return self.get_job(job_id) or _missing_job(job_id)

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row is not None else None

    def list_jobs(
        self,
        *,
        limit: int = 100,
        state: JobState | None = None,
        device_id: str | None = None,
    ) -> tuple[JobRecord, ...]:
        clauses: list[str] = []
        params: list[object] = []
        if state is not None:
            clauses.append("state = ?")
            params.append(state.value)
        if device_id is not None:
            clauses.append("device_id = ?")
            params.append(device_id)
        query = "SELECT * FROM jobs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return tuple(_row_to_job(row) for row in rows)

    def claim_runnable_jobs(self, *, limit: int, lease_seconds: int) -> tuple[JobRecord, ...]:
        now = utc_now()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id FROM jobs
                WHERE state IN (?, ?)
                  AND next_run_at <= ?
                ORDER BY queued_at ASC, created_at ASC
                LIMIT ?
                """,
                (
                    JobState.QUEUED.value,
                    JobState.RETRY_SCHEDULED.value,
                    timestamp_to_iso(now),
                    limit,
                ),
            ).fetchall()
        claimed: list[JobRecord] = []
        for row in rows:
            job_id = str(row["id"])
            updated = self._transition_job(
                job_id=job_id,
                from_states=(JobState.QUEUED, JobState.RETRY_SCHEDULED),
                to_state=JobState.DISPATCHED,
                assignments={
                    "lease_expires_at": timestamp_to_iso(lease_expires_at),
                    "updated_at": timestamp_to_iso(now),
                },
            )
            if updated is not None:
                claimed.append(updated)
        return tuple(claimed)

    def start_job_attempt(self, job_id: str, *, lease_seconds: int) -> tuple[JobRecord, JobAttemptRecord]:
        job = self.get_job(job_id)
        if job is None:
            raise LookupError(job_id)
        now = utc_now()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt_number = job.attempt_count + 1
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, attempt_count = ?, updated_at = ?, started_at = COALESCE(started_at, ?),
                    lease_expires_at = ?, last_error_code = NULL, last_error_detail = NULL
                WHERE id = ? AND state = ?
                """,
                (
                    JobState.RUNNING.value,
                    attempt_number,
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                    timestamp_to_iso(lease_expires_at),
                    job_id,
                    JobState.DISPATCHED.value,
                ),
            )
            cursor = connection.execute(
                """
                INSERT INTO job_attempts (job_id, attempt_number, state, started_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    job_id,
                    attempt_number,
                    JobState.RUNNING.value,
                    timestamp_to_iso(now),
                ),
            )
            attempt_id = int(cursor.lastrowid)
        updated = self.get_job(job_id) or _missing_job(job_id)
        attempt = JobAttemptRecord(
            id=attempt_id,
            job_id=job_id,
            attempt_number=attempt_number,
            state=JobState.RUNNING,
            started_at=now,
        )
        return updated, attempt

    def renew_job_lease(self, job_id: str, *, lease_seconds: int) -> JobRecord | None:
        now = utc_now()
        return self._transition_job(
            job_id=job_id,
            from_states=(JobState.DISPATCHED, JobState.RUNNING),
            to_state=None,
            assignments={
                "lease_expires_at": timestamp_to_iso(now + timedelta(seconds=lease_seconds)),
                "updated_at": timestamp_to_iso(now),
            },
        )

    def mark_job_succeeded(
        self,
        job_id: str,
        *,
        attempt_number: int,
        result_payload: Mapping[str, Any],
    ) -> JobRecord:
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, updated_at = ?, finished_at = ?, lease_expires_at = NULL,
                    result_json = ?, last_error_code = NULL, last_error_detail = NULL
                WHERE id = ?
                """,
                (
                    JobState.SUCCEEDED.value,
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                    _dump_json(result_payload),
                    job_id,
                ),
            )
            connection.execute(
                """
                UPDATE job_attempts
                SET state = ?, finished_at = ?, result_json = ?
                WHERE job_id = ? AND attempt_number = ?
                """,
                (
                    JobState.SUCCEEDED.value,
                    timestamp_to_iso(now),
                    _dump_json(result_payload),
                    job_id,
                    attempt_number,
                ),
            )
        return self.get_job(job_id) or _missing_job(job_id)

    def mark_job_retry(
        self,
        job_id: str,
        *,
        attempt_number: int,
        next_run_at,
        error_code: str,
        error_detail: str,
    ) -> JobRecord:
        now = utc_now()
        retry_at = normalize_timestamp(next_run_at) or now
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, updated_at = ?, next_run_at = ?, lease_expires_at = NULL,
                    last_error_code = ?, last_error_detail = ?
                WHERE id = ?
                """,
                (
                    JobState.RETRY_SCHEDULED.value,
                    timestamp_to_iso(now),
                    timestamp_to_iso(retry_at),
                    error_code,
                    error_detail,
                    job_id,
                ),
            )
            connection.execute(
                """
                UPDATE job_attempts
                SET state = ?, finished_at = ?, error_code = ?, error_detail = ?
                WHERE job_id = ? AND attempt_number = ?
                """,
                (
                    JobState.RETRY_SCHEDULED.value,
                    timestamp_to_iso(now),
                    error_code,
                    error_detail,
                    job_id,
                    attempt_number,
                ),
            )
        return self.get_job(job_id) or _missing_job(job_id)

    def mark_job_failed(
        self,
        job_id: str,
        *,
        attempt_number: int | None,
        error_code: str,
        error_detail: str,
    ) -> JobRecord:
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, updated_at = ?, finished_at = ?, lease_expires_at = NULL,
                    last_error_code = ?, last_error_detail = ?
                WHERE id = ?
                """,
                (
                    JobState.FAILED.value,
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                    error_code,
                    error_detail,
                    job_id,
                ),
            )
            if attempt_number is not None:
                connection.execute(
                    """
                    UPDATE job_attempts
                    SET state = ?, finished_at = ?, error_code = ?, error_detail = ?
                    WHERE job_id = ? AND attempt_number = ?
                    """,
                    (
                        JobState.FAILED.value,
                        timestamp_to_iso(now),
                        error_code,
                        error_detail,
                        job_id,
                        attempt_number,
                    ),
                )
        return self.get_job(job_id) or _missing_job(job_id)

    def cancel_job(self, job_id: str) -> JobRecord | None:
        now = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET state = ?, updated_at = ?, finished_at = ?, lease_expires_at = NULL
                WHERE id = ? AND state IN (?, ?, ?)
                """,
                (
                    JobState.CANCELLED.value,
                    timestamp_to_iso(now),
                    timestamp_to_iso(now),
                    job_id,
                    JobState.QUEUED.value,
                    JobState.RETRY_SCHEDULED.value,
                    JobState.DISPATCHED.value,
                ),
            )
        return self.get_job(job_id) if cursor.rowcount > 0 else None

    def list_job_attempts(self, job_id: str) -> tuple[JobAttemptRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM job_attempts WHERE job_id = ? ORDER BY attempt_number DESC",
                (job_id,),
            ).fetchall()
        return tuple(_row_to_job_attempt(row) for row in rows)

    def create_job_event(
        self,
        *,
        job_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> JobEventRecord:
        occurred_at = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO job_events (job_id, event_type, payload_json, occurred_at) VALUES (?, ?, ?, ?)",
                (job_id, event_type, _dump_json(payload), timestamp_to_iso(occurred_at)),
            )
            sequence = int(cursor.lastrowid)
        return JobEventRecord(
            sequence=sequence,
            resource_id=job_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=dict(payload),
        )

    def list_job_events(self, job_id: str, *, limit: int = 100) -> tuple[JobEventRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id = ? ORDER BY sequence DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return tuple(_row_to_job_event(row) for row in rows)

    def queue_counts(self) -> Mapping[str, int]:
        with self._connection() as connection:
            rows = connection.execute("SELECT state, COUNT(*) AS total FROM jobs GROUP BY state").fetchall()
        counts = Counter()
        for row in rows:
            counts[str(row["state"])] = int(row["total"])
        return dict(counts)

    def recover_expired_jobs(self) -> tuple[JobRecord, ...]:
        now = utc_now()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE state IN (?, ?)
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                """,
                (
                    JobState.DISPATCHED.value,
                    JobState.RUNNING.value,
                    timestamp_to_iso(now),
                ),
            ).fetchall()
        recovered: list[JobRecord] = []
        for row in rows:
            job = _row_to_job(row)
            next_state = JobState.RETRY_SCHEDULED if job.attempt_count < job.max_attempts else JobState.FAILED
            with self._connection() as connection:
                connection.execute(
                    """
                    UPDATE jobs
                    SET state = ?, updated_at = ?, next_run_at = ?, finished_at = ?, lease_expires_at = NULL,
                        last_error_code = ?, last_error_detail = ?
                    WHERE id = ?
                    """,
                    (
                        next_state.value,
                        timestamp_to_iso(now),
                        timestamp_to_iso(now),
                        timestamp_to_iso(now) if next_state is JobState.FAILED else None,
                        "JOB_LEASE_EXPIRED",
                        "Job execution lease expired before completion.",
                        job.id,
                    ),
                )
            recovered.append(self.get_job(job.id) or job)
        return tuple(recovered)

    def _transition_job(
        self,
        *,
        job_id: str,
        from_states: tuple[JobState, ...],
        to_state: JobState | None,
        assignments: Mapping[str, object],
    ) -> JobRecord | None:
        updates: list[str] = []
        params: list[object] = []
        if to_state is not None:
            updates.append("state = ?")
            params.append(to_state.value)
        for key, value in assignments.items():
            updates.append(f"{key} = ?")
            params.append(value)
        params.append(job_id)
        params.extend(state.value for state in from_states)
        placeholders = ", ".join("?" for _ in from_states)
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {', '.join(updates)} WHERE id = ? AND state IN ({placeholders})",
                tuple(params),
            )
        return self.get_job(job_id) if cursor.rowcount > 0 else None


def _dump_json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    loaded = json.loads(value)
    if isinstance(loaded, dict):
        return dict(loaded)
    return {"value": loaded}


def _row_to_device(row: sqlite3.Row) -> DeviceRecord:
    return DeviceRecord(
        id=str(row["id"]),
        kind=DeviceKind(str(row["kind"])),
        driver_key=str(row["driver_key"]),
        name=str(row["name"]),
        connection_state=DeviceConnectionState(str(row["connection_state"])),
        first_seen_at=normalize_timestamp(str(row["first_seen_at"])) or utc_now(),
        last_seen_at=normalize_timestamp(str(row["last_seen_at"])) or utc_now(),
        updated_at=normalize_timestamp(str(row["updated_at"])) or utc_now(),
        is_default=bool(row["is_default"]),
        preferred_transport=PrinterTransport(str(row["preferred_transport"]))
        if row["preferred_transport"] is not None
        else None,
        capabilities=_load_json(str(row["capabilities_json"])),
        metadata=_load_json(str(row["metadata_json"])),
    )


def _row_to_job(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=str(row["id"]),
        kind=JobKind(str(row["kind"])),
        operation=str(row["operation"]),
        device_id=str(row["device_id"]),
        device_kind=DeviceKind(str(row["device_kind"])),
        device_name=str(row["device_name"]),
        state=JobState(str(row["state"])),
        request_payload=_load_json(str(row["request_json"])),
        request_metadata=_load_json(str(row["request_metadata_json"])),
        content_kind=str(row["content_kind"]) if row["content_kind"] is not None else None,
        command_kind=str(row["command_kind"]) if row["command_kind"] is not None else None,
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        created_at=normalize_timestamp(str(row["created_at"])) or utc_now(),
        updated_at=normalize_timestamp(str(row["updated_at"])) or utc_now(),
        queued_at=normalize_timestamp(str(row["queued_at"])) or utc_now(),
        next_run_at=normalize_timestamp(str(row["next_run_at"])) or utc_now(),
        started_at=normalize_timestamp(str(row["started_at"])) if row["started_at"] is not None else None,
        finished_at=normalize_timestamp(str(row["finished_at"])) if row["finished_at"] is not None else None,
        lease_expires_at=normalize_timestamp(str(row["lease_expires_at"]))
        if row["lease_expires_at"] is not None
        else None,
        result_payload=_load_json(str(row["result_json"])) if row["result_json"] is not None else None,
        last_error_code=str(row["last_error_code"]) if row["last_error_code"] is not None else None,
        last_error_detail=str(row["last_error_detail"]) if row["last_error_detail"] is not None else None,
    )


def _row_to_job_attempt(row: sqlite3.Row) -> JobAttemptRecord:
    return JobAttemptRecord(
        id=int(row["id"]),
        job_id=str(row["job_id"]),
        attempt_number=int(row["attempt_number"]),
        state=JobState(str(row["state"])),
        started_at=normalize_timestamp(str(row["started_at"])) or utc_now(),
        finished_at=normalize_timestamp(str(row["finished_at"])) if row["finished_at"] is not None else None,
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        error_detail=str(row["error_detail"]) if row["error_detail"] is not None else None,
        result_payload=_load_json(str(row["result_json"])) if row["result_json"] is not None else None,
    )


def _row_to_device_event(row: sqlite3.Row) -> DeviceEventRecord:
    return DeviceEventRecord(
        sequence=int(row["sequence"]),
        resource_id=str(row["device_id"]),
        event_type=str(row["event_type"]),
        occurred_at=normalize_timestamp(str(row["occurred_at"])) or utc_now(),
        payload=_load_json(str(row["payload_json"])),
    )


def _row_to_job_event(row: sqlite3.Row) -> JobEventRecord:
    return JobEventRecord(
        sequence=int(row["sequence"]),
        resource_id=str(row["job_id"]),
        event_type=str(row["event_type"]),
        occurred_at=normalize_timestamp(str(row["occurred_at"])) or utc_now(),
        payload=_load_json(str(row["payload_json"])),
    )


def _missing_job(job_id: str) -> JobRecord:
    raise LookupError(f"Job {job_id!r} is missing from the runtime store.")
