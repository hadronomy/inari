from __future__ import annotations

import uuid
from collections import Counter
from datetime import timedelta
from typing import Any, Mapping

from sqlalchemy import collate, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult, RowMapping

from ..db.schema import (
    device_events_table,
    devices_table,
    job_attempts_table,
    job_events_table,
    jobs_table,
)
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
from .store import RuntimeStore, dump_json, load_json


class DeviceRepository:
    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def upsert(self, record: DeviceRecord) -> DeviceRecord:
        existing = self.get(record.id)
        first_seen_at = (
            existing.first_seen_at if existing is not None else record.first_seen_at
        )
        stmt = sqlite_insert(devices_table).values(
            id=record.id,
            kind=record.kind.value,
            driver_key=record.driver_key,
            name=record.name,
            connection_state=record.connection_state.value,
            first_seen_at=timestamp_to_iso(first_seen_at),
            last_seen_at=timestamp_to_iso(record.last_seen_at),
            updated_at=timestamp_to_iso(record.updated_at),
            is_default=record.is_default,
            preferred_transport=record.preferred_transport.value
            if record.preferred_transport is not None
            else None,
            capabilities_json=dump_json(record.capabilities),
            metadata_json=dump_json(record.metadata),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[devices_table.c.id],
            set_={
                "kind": record.kind.value,
                "driver_key": record.driver_key,
                "name": record.name,
                "connection_state": record.connection_state.value,
                "last_seen_at": timestamp_to_iso(record.last_seen_at),
                "updated_at": timestamp_to_iso(record.updated_at),
                "is_default": record.is_default,
                "preferred_transport": record.preferred_transport.value
                if record.preferred_transport is not None
                else None,
                "capabilities_json": dump_json(record.capabilities),
                "metadata_json": dump_json(record.metadata),
            },
        )
        with self.store.connection() as connection:
            connection.execute(stmt)
        return self.get(record.id) or record

    def get(self, device_id: str) -> DeviceRecord | None:
        stmt = select(devices_table).where(devices_table.c.id == device_id)
        with self.store.connection() as connection:
            row = connection.execute(stmt).mappings().first()
        return _row_to_device(row) if row is not None else None

    def list(self, *, kind: DeviceKind | None = None) -> tuple[DeviceRecord, ...]:
        stmt = select(devices_table)
        if kind is not None:
            stmt = stmt.where(devices_table.c.kind == kind.value)
        stmt = stmt.order_by(collate(devices_table.c.name, "NOCASE"))
        with self.store.connection() as connection:
            rows = connection.execute(stmt).mappings().all()
        return tuple(_row_to_device(row) for row in rows)

    def append_event(
        self,
        *,
        device_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> DeviceEventRecord:
        occurred_at = utc_now()
        stmt = insert(device_events_table).values(
            device_id=device_id,
            event_type=event_type,
            payload_json=dump_json(payload),
            occurred_at=timestamp_to_iso(occurred_at),
        )
        with self.store.connection() as connection:
            result = connection.execute(stmt)
            sequence = _inserted_row_id(result)
        return DeviceEventRecord(
            sequence=sequence,
            resource_id=device_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=dict(payload),
        )

    def list_events(
        self, device_id: str, *, limit: int = 50
    ) -> tuple[DeviceEventRecord, ...]:
        stmt = (
            select(device_events_table)
            .where(device_events_table.c.device_id == device_id)
            .order_by(device_events_table.c.sequence.desc())
            .limit(limit)
        )
        with self.store.connection() as connection:
            rows = connection.execute(stmt).mappings().all()
        return tuple(_row_to_device_event(row) for row in rows)


class JobRepository:
    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def create(
        self,
        *,
        kind: JobKind,
        operation: str,
        device: DeviceRecord,
        request_payload: Mapping[str, Any],
        request_metadata: Mapping[str, Any],
        content_kind: str | None,
        command_kind: str | None,
        max_attempts: int,
    ) -> JobRecord:
        now = utc_now()
        job_id = f"job_{uuid.uuid4().hex}"
        stmt = insert(jobs_table).values(
            id=job_id,
            kind=kind.value,
            operation=operation,
            device_id=device.id,
            device_kind=device.kind.value,
            device_name=device.name,
            state=JobState.QUEUED.value,
            request_json=dump_json(request_payload),
            request_metadata_json=dump_json(request_metadata),
            content_kind=content_kind,
            command_kind=command_kind,
            attempt_count=0,
            max_attempts=max_attempts,
            created_at=timestamp_to_iso(now),
            updated_at=timestamp_to_iso(now),
            queued_at=timestamp_to_iso(now),
            next_run_at=timestamp_to_iso(now),
        )
        with self.store.connection() as connection:
            connection.execute(stmt)
        return self.get(job_id) or _missing_job(job_id)

    def get(self, job_id: str) -> JobRecord | None:
        stmt = select(jobs_table).where(jobs_table.c.id == job_id)
        with self.store.connection() as connection:
            row = connection.execute(stmt).mappings().first()
        return _row_to_job(row) if row is not None else None

    def list(
        self,
        *,
        limit: int = 100,
        state: JobState | None = None,
        device_id: str | None = None,
    ) -> tuple[JobRecord, ...]:
        stmt = select(jobs_table)
        if state is not None:
            stmt = stmt.where(jobs_table.c.state == state.value)
        if device_id is not None:
            stmt = stmt.where(jobs_table.c.device_id == device_id)
        stmt = stmt.order_by(jobs_table.c.created_at.desc()).limit(limit)
        with self.store.connection() as connection:
            rows = connection.execute(stmt).mappings().all()
        return tuple(_row_to_job(row) for row in rows)

    def claim_runnable(
        self, *, limit: int, lease_seconds: int
    ) -> tuple[JobRecord, ...]:
        now = utc_now()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        stmt = (
            select(jobs_table.c.id)
            .where(
                jobs_table.c.state.in_(
                    (JobState.QUEUED.value, JobState.RETRY_SCHEDULED.value)
                ),
                jobs_table.c.next_run_at <= timestamp_to_iso(now),
            )
            .order_by(jobs_table.c.queued_at.asc(), jobs_table.c.created_at.asc())
            .limit(limit)
        )
        with self.store.connection() as connection:
            job_ids = tuple(connection.execute(stmt).scalars().all())
        claimed: list[JobRecord] = []
        for job_id in job_ids:
            updated = self._transition(
                job_id=str(job_id),
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

    def start_attempt(
        self, job_id: str, *, lease_seconds: int
    ) -> tuple[JobRecord, JobAttemptRecord]:
        job = self.get(job_id)
        if job is None:
            raise LookupError(job_id)
        now = utc_now()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt_number = job.attempt_count + 1
        with self.store.connection() as connection:
            connection.execute(
                update(jobs_table)
                .where(
                    jobs_table.c.id == job_id,
                    jobs_table.c.state == JobState.DISPATCHED.value,
                )
                .values(
                    state=JobState.RUNNING.value,
                    attempt_count=attempt_number,
                    updated_at=timestamp_to_iso(now),
                    started_at=job.started_at
                    and timestamp_to_iso(job.started_at)
                    or timestamp_to_iso(now),
                    lease_expires_at=timestamp_to_iso(lease_expires_at),
                    last_error_code=None,
                    last_error_detail=None,
                )
            )
            result = connection.execute(
                insert(job_attempts_table).values(
                    job_id=job_id,
                    attempt_number=attempt_number,
                    state=JobState.RUNNING.value,
                    started_at=timestamp_to_iso(now),
                )
            )
            attempt_id = _inserted_row_id(result)
        updated = self.get(job_id) or _missing_job(job_id)
        return updated, JobAttemptRecord(
            id=attempt_id,
            job_id=job_id,
            attempt_number=attempt_number,
            state=JobState.RUNNING,
            started_at=now,
        )

    def renew_lease(self, job_id: str, *, lease_seconds: int) -> JobRecord | None:
        now = utc_now()
        return self._transition(
            job_id=job_id,
            from_states=(JobState.DISPATCHED, JobState.RUNNING),
            to_state=None,
            assignments={
                "lease_expires_at": timestamp_to_iso(
                    now + timedelta(seconds=lease_seconds)
                ),
                "updated_at": timestamp_to_iso(now),
            },
        )

    def mark_succeeded(
        self,
        job_id: str,
        *,
        attempt_number: int,
        result_payload: Mapping[str, Any],
    ) -> JobRecord:
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute(
                update(jobs_table)
                .where(jobs_table.c.id == job_id)
                .values(
                    state=JobState.SUCCEEDED.value,
                    updated_at=timestamp_to_iso(now),
                    finished_at=timestamp_to_iso(now),
                    lease_expires_at=None,
                    result_json=dump_json(result_payload),
                    last_error_code=None,
                    last_error_detail=None,
                )
            )
            connection.execute(
                update(job_attempts_table)
                .where(
                    job_attempts_table.c.job_id == job_id,
                    job_attempts_table.c.attempt_number == attempt_number,
                )
                .values(
                    state=JobState.SUCCEEDED.value,
                    finished_at=timestamp_to_iso(now),
                    result_json=dump_json(result_payload),
                )
            )
        return self.get(job_id) or _missing_job(job_id)

    def mark_retry(
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
        with self.store.connection() as connection:
            connection.execute(
                update(jobs_table)
                .where(jobs_table.c.id == job_id)
                .values(
                    state=JobState.RETRY_SCHEDULED.value,
                    updated_at=timestamp_to_iso(now),
                    next_run_at=timestamp_to_iso(retry_at),
                    lease_expires_at=None,
                    last_error_code=error_code,
                    last_error_detail=error_detail,
                )
            )
            connection.execute(
                update(job_attempts_table)
                .where(
                    job_attempts_table.c.job_id == job_id,
                    job_attempts_table.c.attempt_number == attempt_number,
                )
                .values(
                    state=JobState.RETRY_SCHEDULED.value,
                    finished_at=timestamp_to_iso(now),
                    error_code=error_code,
                    error_detail=error_detail,
                )
            )
        return self.get(job_id) or _missing_job(job_id)

    def mark_failed(
        self,
        job_id: str,
        *,
        attempt_number: int | None,
        error_code: str,
        error_detail: str,
    ) -> JobRecord:
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute(
                update(jobs_table)
                .where(jobs_table.c.id == job_id)
                .values(
                    state=JobState.FAILED.value,
                    updated_at=timestamp_to_iso(now),
                    finished_at=timestamp_to_iso(now),
                    lease_expires_at=None,
                    last_error_code=error_code,
                    last_error_detail=error_detail,
                )
            )
            if attempt_number is not None:
                connection.execute(
                    update(job_attempts_table)
                    .where(
                        job_attempts_table.c.job_id == job_id,
                        job_attempts_table.c.attempt_number == attempt_number,
                    )
                    .values(
                        state=JobState.FAILED.value,
                        finished_at=timestamp_to_iso(now),
                        error_code=error_code,
                        error_detail=error_detail,
                    )
                )
        return self.get(job_id) or _missing_job(job_id)

    def cancel(self, job_id: str) -> JobRecord | None:
        now = utc_now()
        stmt = (
            update(jobs_table)
            .where(
                jobs_table.c.id == job_id,
                jobs_table.c.state.in_(
                    (
                        JobState.QUEUED.value,
                        JobState.RETRY_SCHEDULED.value,
                        JobState.DISPATCHED.value,
                    )
                ),
            )
            .values(
                state=JobState.CANCELLED.value,
                updated_at=timestamp_to_iso(now),
                finished_at=timestamp_to_iso(now),
                lease_expires_at=None,
            )
        )
        with self.store.connection() as connection:
            result = connection.execute(stmt)
        return self.get(job_id) if result.rowcount and result.rowcount > 0 else None

    def list_attempts(self, job_id: str) -> tuple[JobAttemptRecord, ...]:
        stmt = (
            select(job_attempts_table)
            .where(job_attempts_table.c.job_id == job_id)
            .order_by(job_attempts_table.c.attempt_number.desc())
        )
        with self.store.connection() as connection:
            rows = connection.execute(stmt).mappings().all()
        return tuple(_row_to_job_attempt(row) for row in rows)

    def append_event(
        self,
        *,
        job_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> JobEventRecord:
        occurred_at = utc_now()
        stmt = insert(job_events_table).values(
            job_id=job_id,
            event_type=event_type,
            payload_json=dump_json(payload),
            occurred_at=timestamp_to_iso(occurred_at),
        )
        with self.store.connection() as connection:
            result = connection.execute(stmt)
            sequence = _inserted_row_id(result)
        return JobEventRecord(
            sequence=sequence,
            resource_id=job_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=dict(payload),
        )

    def list_events(
        self, job_id: str, *, limit: int = 100
    ) -> tuple[JobEventRecord, ...]:
        stmt = (
            select(job_events_table)
            .where(job_events_table.c.job_id == job_id)
            .order_by(job_events_table.c.sequence.desc())
            .limit(limit)
        )
        with self.store.connection() as connection:
            rows = connection.execute(stmt).mappings().all()
        return tuple(_row_to_job_event(row) for row in rows)

    def queue_counts(self) -> Mapping[str, int]:
        stmt = select(jobs_table.c.state, func.count().label("total")).group_by(
            jobs_table.c.state
        )
        with self.store.connection() as connection:
            rows = connection.execute(stmt).mappings().all()
        counts = Counter()
        for row in rows:
            counts[str(row["state"])] = int(row["total"])
        return dict(counts)

    def recover_expired(self) -> tuple[JobRecord, ...]:
        now = utc_now()
        stmt = select(jobs_table).where(
            jobs_table.c.state.in_((JobState.DISPATCHED.value, JobState.RUNNING.value)),
            jobs_table.c.lease_expires_at.is_not(None),
            jobs_table.c.lease_expires_at <= timestamp_to_iso(now),
        )
        with self.store.connection() as connection:
            rows = connection.execute(stmt).mappings().all()
        recovered: list[JobRecord] = []
        for row in rows:
            job = _row_to_job(row)
            next_state = (
                JobState.RETRY_SCHEDULED
                if job.attempt_count < job.max_attempts
                else JobState.FAILED
            )
            with self.store.connection() as connection:
                connection.execute(
                    update(jobs_table)
                    .where(jobs_table.c.id == job.id)
                    .values(
                        state=next_state.value,
                        updated_at=timestamp_to_iso(now),
                        next_run_at=timestamp_to_iso(now),
                        finished_at=timestamp_to_iso(now)
                        if next_state is JobState.FAILED
                        else None,
                        lease_expires_at=None,
                        last_error_code="JOB_LEASE_EXPIRED",
                        last_error_detail="Job execution lease expired before completion.",
                    )
                )
            recovered.append(self.get(job.id) or job)
        return tuple(recovered)

    def _transition(
        self,
        *,
        job_id: str,
        from_states: tuple[JobState, ...],
        to_state: JobState | None,
        assignments: Mapping[str, object],
    ) -> JobRecord | None:
        values: dict[str, object] = dict(assignments)
        if to_state is not None:
            values["state"] = to_state.value
        stmt = (
            update(jobs_table)
            .where(
                jobs_table.c.id == job_id,
                jobs_table.c.state.in_(tuple(state.value for state in from_states)),
            )
            .values(**values)
        )
        with self.store.connection() as connection:
            result = connection.execute(stmt)
        return self.get(job_id) if result.rowcount and result.rowcount > 0 else None


def _row_to_device(row: RowMapping | Mapping[str, Any]) -> DeviceRecord:
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
        capabilities=load_json(str(row["capabilities_json"])),
        metadata=load_json(str(row["metadata_json"])),
    )


def _row_to_job(row: RowMapping | Mapping[str, Any]) -> JobRecord:
    return JobRecord(
        id=str(row["id"]),
        kind=JobKind(str(row["kind"])),
        operation=str(row["operation"]),
        device_id=str(row["device_id"]),
        device_kind=DeviceKind(str(row["device_kind"])),
        device_name=str(row["device_name"]),
        state=JobState(str(row["state"])),
        request_payload=load_json(str(row["request_json"])),
        request_metadata=load_json(str(row["request_metadata_json"])),
        content_kind=str(row["content_kind"])
        if row["content_kind"] is not None
        else None,
        command_kind=str(row["command_kind"])
        if row["command_kind"] is not None
        else None,
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        created_at=normalize_timestamp(str(row["created_at"])) or utc_now(),
        updated_at=normalize_timestamp(str(row["updated_at"])) or utc_now(),
        queued_at=normalize_timestamp(str(row["queued_at"])) or utc_now(),
        next_run_at=normalize_timestamp(str(row["next_run_at"])) or utc_now(),
        started_at=normalize_timestamp(str(row["started_at"]))
        if row["started_at"] is not None
        else None,
        finished_at=normalize_timestamp(str(row["finished_at"]))
        if row["finished_at"] is not None
        else None,
        lease_expires_at=normalize_timestamp(str(row["lease_expires_at"]))
        if row["lease_expires_at"] is not None
        else None,
        result_payload=load_json(str(row["result_json"]))
        if row["result_json"] is not None
        else None,
        last_error_code=str(row["last_error_code"])
        if row["last_error_code"] is not None
        else None,
        last_error_detail=str(row["last_error_detail"])
        if row["last_error_detail"] is not None
        else None,
    )


def _row_to_job_attempt(
    row: RowMapping | Mapping[str, Any]
) -> JobAttemptRecord:
    return JobAttemptRecord(
        id=int(row["id"]),
        job_id=str(row["job_id"]),
        attempt_number=int(row["attempt_number"]),
        state=JobState(str(row["state"])),
        started_at=normalize_timestamp(str(row["started_at"])) or utc_now(),
        finished_at=normalize_timestamp(str(row["finished_at"]))
        if row["finished_at"] is not None
        else None,
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        error_detail=str(row["error_detail"])
        if row["error_detail"] is not None
        else None,
        result_payload=load_json(str(row["result_json"]))
        if row["result_json"] is not None
        else None,
    )


def _row_to_device_event(
    row: RowMapping | Mapping[str, Any]
) -> DeviceEventRecord:
    return DeviceEventRecord(
        sequence=int(row["sequence"]),
        resource_id=str(row["device_id"]),
        event_type=str(row["event_type"]),
        occurred_at=normalize_timestamp(str(row["occurred_at"])) or utc_now(),
        payload=load_json(str(row["payload_json"])),
    )


def _row_to_job_event(row: RowMapping | Mapping[str, Any]) -> JobEventRecord:
    return JobEventRecord(
        sequence=int(row["sequence"]),
        resource_id=str(row["job_id"]),
        event_type=str(row["event_type"]),
        occurred_at=normalize_timestamp(str(row["occurred_at"])) or utc_now(),
        payload=load_json(str(row["payload_json"])),
    )


def _missing_job(job_id: str) -> JobRecord:
    raise LookupError(f"Job {job_id!r} is missing from the runtime store.")


def _inserted_row_id(result: CursorResult[Any]) -> int:
    if result.inserted_primary_key:
        inserted_key = result.inserted_primary_key[0]
        if inserted_key is not None:
            return int(inserted_key)
    if result.lastrowid is not None:
        return int(result.lastrowid)
    raise RuntimeError("SQLite insert did not return a row id.")
