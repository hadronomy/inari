from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from ..client_trust import GrantLifecycle, PairingLifecycle, Permission
from ..db.schema import (
    client_grants_table,
    client_pairings_table,
    device_tests_table,
    device_work_admissions_table,
    devices_table,
    physical_execution_attempts_table,
    public_print_job_events_table,
    public_print_jobs_table,
    spool_artifacts_table,
    spool_job_keys_table,
    spool_reservations_table,
)
from ..print_jobs import OutputEvidence, PrintJobState
from ..runtime.store import RuntimeStore
from ..spool import ArtifactKind, SqlActiveAuthorityGuard
from ..spool.errors import SpoolAdmissionError
from ..spool.manifest import canonical_json, parse_timestamp, timestamp
from ..spool.proofs import read_authority_proof
from .models import (
    ArtifactRef,
    DriverExecutionResult,
    DriverOutcome,
    ExecutionClaim,
    ExecutionOwner,
    ExecutionRejected,
    ExecutionReceipt,
    IoPermit,
    LeaseLost,
    RecoveryReport,
    WrappedJobKey,
)


_ACTIVE_PHASES = (
    "claimed",
    "prepared",
    "marker_committed",
    "permission_delivered",
)
_LEASE_DURATION = timedelta(seconds=30)
_EXECUTION_TEMP_BYTES = 8 * 1024 * 1024


class SqliteExecutionLedger:
    """Own fenced physical execution state and the public I/O boundary."""

    def __init__(
        self,
        *,
        store: RuntimeStore,
        authority_guard: SqlActiveAuthorityGuard,
        lease_duration: timedelta = _LEASE_DURATION,
    ) -> None:
        self._store = store
        self._authority_guard = authority_guard
        self._lease_duration = lease_duration

    def claim_next(
        self, owner: ExecutionOwner, *, device_id: str | None, now: datetime
    ) -> ExecutionClaim | None:
        lease_expires_at = now + self._lease_duration
        with self._store.immediate_transaction() as connection:
            rows = tuple(
                connection.execute(
                    select(public_print_jobs_table)
                    .where(public_print_jobs_table.c.state == "accepted")
                    .order_by(
                        public_print_jobs_table.c.accepted_at,
                        public_print_jobs_table.c.id,
                    )
                ).mappings()
            )
            for job in rows:
                if device_id is not None and job["device_id"] != device_id:
                    continue
                if parse_timestamp(job["expires_at"]) <= now:
                    self._expire_accepted(connection, job, now=now)
                    continue
                if self._device_is_busy(
                    connection, device_id=str(job["device_id"]), now=now
                ):
                    continue
                return self._claim(
                    connection,
                    job,
                    owner=owner,
                    now=now,
                    lease_expires_at=lease_expires_at,
                )
        return None

    def mark_prepared(self, claim: ExecutionClaim, *, now: datetime) -> None:
        with self._store.immediate_transaction() as connection:
            result = connection.execute(
                update(physical_execution_attempts_table)
                .where(*self._fence(claim, phase="claimed", now=now))
                .values(phase="prepared", updated_at=timestamp(now))
            )
            if result.rowcount != 1:
                raise LeaseLost("The execution claim is no longer active.")

    def renew(self, claim: ExecutionClaim, *, now: datetime) -> ExecutionClaim:
        lease_expires_at = now + self._lease_duration
        with self._store.immediate_transaction() as connection:
            result = connection.execute(
                update(physical_execution_attempts_table)
                .where(*self._fence(claim, now=now))
                .values(
                    lease_expires_at=timestamp(lease_expires_at),
                    updated_at=timestamp(now),
                )
            )
            if result.rowcount != 1:
                raise LeaseLost("The execution lease is no longer active.")
            connection.execute(
                update(spool_reservations_table)
                .where(
                    spool_reservations_table.c.job_id == claim.job_id,
                    spool_reservations_table.c.owner_id == claim.owner.owner_id,
                    spool_reservations_table.c.owner_generation
                    == claim.owner.generation,
                    spool_reservations_table.c.reservation_kind == "execution_temp",
                    spool_reservations_table.c.state == "held",
                )
                .values(expires_at=timestamp(lease_expires_at))
            )
        return replace(claim, lease_expires_at=lease_expires_at)

    def mark_io_started(self, claim: ExecutionClaim, *, now: datetime) -> IoPermit:
        marker_id = uuid4().hex
        with self._store.immediate_transaction() as connection:
            attempt = self._attempt(connection, claim, phase="prepared", now=now)
            job = self._job(connection, claim.job_id)
            if (
                job["state"] != "accepted"
                or job["state_version"] != claim.state_version
            ):
                raise LeaseLost("The Print Job changed before Device I/O.")
            if parse_timestamp(job["expires_at"]) <= now:
                raise ExecutionRejected("expired", "print.expired")
            if claim.scope_kind == "paired_client":
                self._check_client_grant(connection, claim, now=now)
            self._check_device_authority(connection, claim, now=now)

            next_version = int(job["state_version"]) + 1
            changed = connection.execute(
                update(public_print_jobs_table)
                .where(
                    public_print_jobs_table.c.id == claim.job_id,
                    public_print_jobs_table.c.state == "accepted",
                    public_print_jobs_table.c.state_version == claim.state_version,
                )
                .values(
                    state="in_progress",
                    state_version=next_version,
                    started_at=timestamp(now),
                )
            )
            if changed.rowcount != 1:
                raise LeaseLost("The Print Job changed before Device I/O.")
            event = connection.execute(
                insert(public_print_job_events_table).values(
                    job_id=claim.job_id,
                    state_version=next_version,
                    event_type="in_progress",
                    snapshot_json=self._snapshot(
                        job,
                        state="in_progress",
                        state_version=next_version,
                        started_at=timestamp(now),
                    ),
                    occurred_at=timestamp(now),
                )
            )
            inserted_key = event.inserted_primary_key
            if not inserted_key:
                raise LeaseLost("The Device I/O event has no durable sequence.")
            sequence = int(inserted_key[0])
            marked = connection.execute(
                update(physical_execution_attempts_table)
                .where(
                    physical_execution_attempts_table.c.attempt_id
                    == attempt["attempt_id"],
                    physical_execution_attempts_table.c.phase == "prepared",
                )
                .values(
                    phase="marker_committed",
                    state_version=next_version,
                    marker_id=marker_id,
                    marker_at=timestamp(now),
                    marker_sequence=sequence,
                    updated_at=timestamp(now),
                )
            )
            if marked.rowcount != 1:
                raise LeaseLost("The execution marker lost its lease.")
        return IoPermit(
            attempt_id=claim.attempt_id,
            lease_id=claim.lease_id,
            execution_id=claim.execution_id,
            job_id=claim.job_id,
            device_id=claim.device_id,
            marker_id=marker_id,
            marker_sequence=sequence,
            committed_at=now,
        )

    def note_permission_delivered(
        self, claim: ExecutionClaim, permit: IoPermit, *, now: datetime
    ) -> None:
        with self._store.immediate_transaction() as connection:
            result = connection.execute(
                update(physical_execution_attempts_table)
                .where(
                    *self._fence(claim, phase="marker_committed", now=now),
                    physical_execution_attempts_table.c.marker_id == permit.marker_id,
                )
                .values(
                    phase="permission_delivered",
                    io_permission_issued=True,
                    updated_at=timestamp(now),
                )
            )
            if result.rowcount != 1:
                raise LeaseLost("The Device I/O permit lost its lease.")

    def finish(
        self,
        claim: ExecutionClaim,
        result: DriverExecutionResult,
        *,
        now: datetime,
    ) -> ExecutionReceipt:
        with self._store.immediate_transaction() as connection:
            attempt = self._attempt(
                connection, claim, phase="permission_delivered", now=now
            )
            job = self._job(connection, claim.job_id)
            if (
                result.outcome is DriverOutcome.CONFIRMED
                and result.evidence is not None
                and not self._authority_guard.output_evidence_meets_contract(
                    connection,
                    read_authority_proof(connection, admission_id=claim.admission_id),
                    result.evidence.value,
                )
            ):
                result = replace(
                    result,
                    outcome=DriverOutcome.UNKNOWN,
                    evidence=None,
                    error_code="output_evidence_insufficient",
                    message_key="print.output_evidence_insufficient",
                )
            state, evidence, error_code, message_key = self._result_state(result)
            next_version = int(job["state_version"]) + 1
            values: dict[str, object] = {
                "state": state.value,
                "state_version": next_version,
                "terminal_at": timestamp(now),
                "retryable": False,
                "error_code": error_code,
                "message_key": message_key,
                "confirmation_evidence": evidence.value if evidence else None,
            }
            changed = connection.execute(
                update(public_print_jobs_table)
                .where(
                    public_print_jobs_table.c.id == claim.job_id,
                    public_print_jobs_table.c.state == "in_progress",
                    public_print_jobs_table.c.state_version == attempt["state_version"],
                )
                .values(**values)
            )
            if changed.rowcount != 1:
                raise LeaseLost("A stale worker cannot finish this Print Job.")
            connection.execute(
                insert(public_print_job_events_table).values(
                    job_id=claim.job_id,
                    state_version=next_version,
                    event_type=state.value,
                    snapshot_json=self._snapshot(
                        job,
                        state=state.value,
                        state_version=next_version,
                        terminal_at=timestamp(now),
                        error_code=error_code,
                        message_key=message_key,
                        confirmation_evidence=evidence.value if evidence else None,
                    ),
                    occurred_at=timestamp(now),
                )
            )
            connection.execute(
                update(physical_execution_attempts_table)
                .where(
                    physical_execution_attempts_table.c.attempt_id == claim.attempt_id
                )
                .values(
                    phase="finished",
                    platform_job_id=result.platform_job_id,
                    result_json=canonical_json(
                        {
                            "evidence": evidence.value if evidence else None,
                            "outcome": result.outcome.value,
                        }
                    ),
                    error_code=error_code,
                    updated_at=timestamp(now),
                    finished_at=timestamp(now),
                )
            )
            self._release_reservation(connection, claim, now=now)
            self._schedule_artifact_release(connection, claim, state=state, now=now)
        return ExecutionReceipt(claim.job_id, state, next_version)

    def fail_before_io(
        self,
        claim: ExecutionClaim,
        *,
        error_code: str,
        message_key: str,
        now: datetime,
    ) -> ExecutionReceipt:
        with self._store.immediate_transaction() as connection:
            attempt = self._attempt(connection, claim, now=now)
            if attempt["phase"] not in {"claimed", "prepared"}:
                raise LeaseLost("Device I/O can no longer be ruled out.")
            job = self._job(connection, claim.job_id)
            state = (
                PrintJobState.EXPIRED
                if parse_timestamp(job["expires_at"]) <= now
                else PrintJobState.FAILED
            )
            next_version = int(job["state_version"]) + 1
            final_error = None if state is PrintJobState.EXPIRED else error_code
            final_message = None if state is PrintJobState.EXPIRED else message_key
            changed = connection.execute(
                update(public_print_jobs_table)
                .where(
                    public_print_jobs_table.c.id == claim.job_id,
                    public_print_jobs_table.c.state == "accepted",
                    public_print_jobs_table.c.state_version == claim.state_version,
                )
                .values(
                    state=state.value,
                    state_version=next_version,
                    terminal_at=timestamp(now),
                    retryable=False,
                    error_code=final_error,
                    message_key=final_message,
                )
            )
            if changed.rowcount != 1:
                raise LeaseLost("A stale worker cannot fail this Print Job.")
            connection.execute(
                insert(public_print_job_events_table).values(
                    job_id=claim.job_id,
                    state_version=next_version,
                    event_type=state.value,
                    snapshot_json=self._snapshot(
                        job,
                        state=state.value,
                        state_version=next_version,
                        terminal_at=timestamp(now),
                        error_code=final_error,
                        message_key=final_message,
                    ),
                    occurred_at=timestamp(now),
                )
            )
            connection.execute(
                update(physical_execution_attempts_table)
                .where(
                    physical_execution_attempts_table.c.attempt_id == claim.attempt_id
                )
                .values(
                    phase="finished",
                    error_code=final_error,
                    updated_at=timestamp(now),
                    finished_at=timestamp(now),
                )
            )
            self._release_reservation(connection, claim, now=now)
            self._schedule_artifact_release(connection, claim, state=state, now=now)
        return ExecutionReceipt(claim.job_id, state, next_version)

    def abandon_after_marker(
        self, claim: ExecutionClaim, *, now: datetime
    ) -> ExecutionReceipt:
        with self._store.immediate_transaction() as connection:
            attempt = (
                connection.execute(
                    select(physical_execution_attempts_table).where(
                        physical_execution_attempts_table.c.attempt_id
                        == claim.attempt_id,
                        physical_execution_attempts_table.c.lease_id == claim.lease_id,
                        physical_execution_attempts_table.c.owner_id
                        == claim.owner.owner_id,
                        physical_execution_attempts_table.c.owner_generation
                        == claim.owner.generation,
                        physical_execution_attempts_table.c.phase.in_(
                            ("marker_committed", "permission_delivered")
                        ),
                        physical_execution_attempts_table.c.marker_id.is_not(None),
                    )
                )
                .mappings()
                .first()
            )
            if attempt is None:
                raise LeaseLost("The marked execution attempt is no longer active.")
            job = self._job(connection, claim.job_id)
            if job["state"] != "in_progress":
                raise LeaseLost("The marked Print Job is no longer in progress.")
            self._recover_unknown(connection, job, now=now)
            connection.execute(
                update(physical_execution_attempts_table)
                .where(
                    physical_execution_attempts_table.c.attempt_id == claim.attempt_id
                )
                .values(
                    phase="recovered",
                    error_code="outcome_unknown",
                    updated_at=timestamp(now),
                    finished_at=timestamp(now),
                )
            )
            self._release_reservation(connection, claim, now=now)
        return ExecutionReceipt(
            claim.job_id,
            PrintJobState.OUTCOME_UNKNOWN,
            int(job["state_version"]) + 1,
        )

    def recover(self, owner: ExecutionOwner, *, now: datetime) -> RecoveryReport:
        returned = 0
        expired = 0
        unknown = 0
        with self._store.immediate_transaction() as connection:
            attempts = tuple(
                connection.execute(
                    select(physical_execution_attempts_table).where(
                        physical_execution_attempts_table.c.phase.in_(_ACTIVE_PHASES),
                        (
                            physical_execution_attempts_table.c.lease_expires_at
                            <= timestamp(now)
                        )
                        | (
                            (
                                physical_execution_attempts_table.c.owner_id
                                == owner.owner_id
                            )
                            & (
                                physical_execution_attempts_table.c.owner_generation
                                < owner.generation
                            )
                        ),
                    )
                ).mappings()
            )
            for attempt in attempts:
                job = self._job(connection, str(attempt["job_id"]))
                marked = attempt["marker_id"] is not None
                if not marked:
                    if (
                        job["state"] == "accepted"
                        and parse_timestamp(job["expires_at"]) <= now
                    ):
                        self._expire_accepted(connection, job, now=now)
                        expired += 1
                    else:
                        returned += 1
                elif job["state"] == "in_progress":
                    self._recover_unknown(connection, job, now=now)
                    unknown += 1
                connection.execute(
                    update(physical_execution_attempts_table)
                    .where(
                        physical_execution_attempts_table.c.attempt_id
                        == attempt["attempt_id"]
                    )
                    .values(
                        phase="recovered",
                        updated_at=timestamp(now),
                        finished_at=timestamp(now),
                    )
                )
                connection.execute(
                    update(spool_reservations_table)
                    .where(
                        spool_reservations_table.c.job_id == attempt["job_id"],
                        spool_reservations_table.c.reservation_kind == "execution_temp",
                        spool_reservations_table.c.state == "held",
                    )
                    .values(state="released", released_at=timestamp(now))
                )
        return RecoveryReport(returned, expired, unknown)

    def _claim(
        self,
        connection: Connection,
        job: RowMapping,
        *,
        owner: ExecutionOwner,
        now: datetime,
        lease_expires_at: datetime,
    ) -> ExecutionClaim:
        admission = (
            connection.execute(
                select(device_work_admissions_table).where(
                    device_work_admissions_table.c.id == job["admission_id"]
                )
            )
            .mappings()
            .one()
        )
        artifact = (
            connection.execute(
                select(spool_artifacts_table).where(
                    spool_artifacts_table.c.job_id == job["id"],
                    spool_artifacts_table.c.artifact_kind == "original",
                    spool_artifacts_table.c.state == "committed",
                )
            )
            .mappings()
            .one()
        )
        key = (
            connection.execute(
                select(spool_job_keys_table).where(
                    spool_job_keys_table.c.job_id == job["id"],
                    spool_job_keys_table.c.key_deleted_at.is_(None),
                )
            )
            .mappings()
            .one()
        )
        device = (
            connection.execute(
                select(devices_table).where(devices_table.c.id == job["device_id"])
            )
            .mappings()
            .one()
        )
        proof = read_authority_proof(connection, admission_id=str(job["admission_id"]))
        scope_kind = str(admission["scope_kind"])
        grant_values = (
            admission["grant_id"],
            admission["grant_pairing_id"],
            admission["grant_generation"],
            admission["grant_authorization_digest"],
        )
        if scope_kind == "paired_client":
            if any(value is None for value in grant_values):
                raise LeaseLost("The admitted Client Grant reference is incomplete.")
        elif scope_kind == "device_manager":
            if admission["managed_work_id"] is None or any(
                value is not None for value in grant_values
            ):
                raise LeaseLost("The admitted managed authorization is incomplete.")
        else:
            raise LeaseLost("The admitted authorization scope is invalid.")

        attempt_id = uuid4().hex
        lease_id = uuid4().hex
        execution_id = uuid4().hex
        reservation_id = uuid4().hex
        attempt_number = (
            int(
                connection.execute(
                    select(func.count())
                    .select_from(physical_execution_attempts_table)
                    .where(physical_execution_attempts_table.c.job_id == job["id"])
                ).scalar_one()
            )
            + 1
        )
        connection.execute(
            insert(spool_reservations_table).values(
                id=reservation_id,
                reservation_kind="execution_temp",
                admission_id=None,
                job_id=job["id"],
                device_id=job["device_id"],
                owner_id=owner.owner_id,
                owner_generation=owner.generation,
                queue_slots=0,
                original_bytes=0,
                persistent_bytes=0,
                temporary_bytes=_EXECUTION_TEMP_BYTES,
                state="held",
                created_at=timestamp(now),
                expires_at=timestamp(lease_expires_at),
                released_at=None,
            )
        )
        connection.execute(
            insert(physical_execution_attempts_table).values(
                attempt_id=attempt_id,
                job_id=job["id"],
                lease_id=lease_id,
                owner_id=owner.owner_id,
                owner_generation=owner.generation,
                attempt_number=attempt_number,
                state_version=job["state_version"],
                phase="claimed",
                lease_expires_at=timestamp(lease_expires_at),
                marker_id=None,
                marker_at=None,
                marker_sequence=None,
                io_permission_issued=False,
                execution_id=execution_id,
                platform_job_id=None,
                result_json=None,
                error_code=None,
                created_at=timestamp(now),
                updated_at=timestamp(now),
                finished_at=None,
            )
        )
        return ExecutionClaim(
            attempt_id=attempt_id,
            lease_id=lease_id,
            execution_id=execution_id,
            owner=owner,
            attempt_number=attempt_number,
            state_version=int(job["state_version"]),
            lease_expires_at=lease_expires_at,
            job_id=str(job["id"]),
            intent_id=str(job["intent_id"]),
            admission_id=str(job["admission_id"]),
            device_id=str(job["device_id"]),
            driver_key=str(device["driver_key"]),
            device_name=str(device["name"]),
            capability_id=proof.capability_id,
            operation=str(admission["operation"]),
            media_type=str(admission["media_type"]),
            normalized_options_digest=bytes(admission["normalized_options_digest"]),
            normalized_options=bytes(admission["normalized_options"]),
            binding_revision_id=str(admission["binding_revision_id"]),
            scope_kind=scope_kind,
            managed_work_id=(
                str(admission["managed_work_id"])
                if admission["managed_work_id"] is not None
                else None
            ),
            grant_id=(
                str(admission["grant_id"])
                if admission["grant_id"] is not None
                else None
            ),
            grant_pairing_id=(
                str(admission["grant_pairing_id"])
                if admission["grant_pairing_id"] is not None
                else None
            ),
            grant_generation=(
                int(admission["grant_generation"])
                if admission["grant_generation"] is not None
                else None
            ),
            grant_authorization_digest=(
                bytes(admission["grant_authorization_digest"])
                if admission["grant_authorization_digest"] is not None
                else None
            ),
            expires_at=parse_timestamp(str(job["expires_at"])),
            original=ArtifactRef(
                artifact_id=str(artifact["id"]),
                storage_ref=str(artifact["storage_ref"]),
                kind=ArtifactKind(str(artifact["artifact_kind"])),
                format_version=int(artifact["format_version"]),
                nonce=bytes(artifact["nonce"]),
                plaintext_size_bytes=int(artifact["plaintext_size_bytes"]),
                ciphertext_size_bytes=int(artifact["ciphertext_size_bytes"]),
                plaintext_sha256=bytes(artifact["plaintext_sha256"]),
            ),
            key=WrappedJobKey(
                root_version=int(key["root_version"]),
                format_version=int(key["format_version"]),
                wrap_nonce=bytes(key["wrap_nonce"]),
                wrapped_key=bytes(key["wrapped_key"]),
            ),
        )

    def _check_client_grant(
        self, connection: Connection, claim: ExecutionClaim, *, now: datetime
    ) -> None:
        if (
            claim.grant_id is None
            or claim.grant_pairing_id is None
            or claim.grant_generation is None
            or claim.grant_authorization_digest is None
        ):
            raise ExecutionRejected("permission_denied", "print.permission_denied")
        grant = (
            connection.execute(
                select(client_grants_table).where(
                    client_grants_table.c.grant_id == claim.grant_id
                )
            )
            .mappings()
            .first()
        )
        pairing = (
            connection.execute(
                select(client_pairings_table).where(
                    client_pairings_table.c.pairing_id == claim.grant_pairing_id
                )
            )
            .mappings()
            .first()
        )
        if grant is None or pairing is None:
            raise ExecutionRejected("permission_denied", "print.permission_denied")
        permissions = json.loads(str(grant["permissions"]))
        expected = {
            "pairing_id": claim.grant_pairing_id,
            "generation": claim.grant_generation,
            "database": str(grant["database"]),
            "organization_id": str(grant["organization_id"]),
            "site_id": str(grant["site_id"]),
            "pos_configuration_id": str(grant["pos_configuration_id"]),
            "actor_id": str(grant["actor_id"]),
        }
        admission = (
            connection.execute(
                select(device_work_admissions_table).where(
                    device_work_admissions_table.c.id == claim.admission_id
                )
            )
            .mappings()
            .one()
        )
        if (
            grant["lifecycle"] != GrantLifecycle.ACTIVE.value
            or pairing["lifecycle"] != PairingLifecycle.ACTIVE.value
            or grant["pairing_id"] != claim.grant_pairing_id
            or int(grant["generation"]) != claim.grant_generation
            or parse_timestamp(str(grant["expires_at"])) <= now
            or (
                pairing["expires_at"] is not None
                and parse_timestamp(str(pairing["expires_at"])) <= now
            )
            or sha256(str(grant["authorization_digest"]).encode()).digest()
            != claim.grant_authorization_digest
            or Permission.RECEIPT_IMAGE.value not in permissions
            or expected
            != {
                "pairing_id": admission["grant_pairing_id"],
                "generation": admission["grant_generation"],
                "database": admission["database"],
                "organization_id": admission["organization_id"],
                "site_id": admission["site_id"],
                "pos_configuration_id": admission["pos_configuration_id"],
                "actor_id": admission["actor_id"],
            }
        ):
            raise ExecutionRejected("permission_denied", "print.permission_denied")

    def _check_device_authority(
        self, connection: Connection, claim: ExecutionClaim, *, now: datetime
    ) -> None:
        try:
            proof = read_authority_proof(connection, admission_id=claim.admission_id)
            self._authority_guard.check(connection, proof, now=now)
        except SpoolAdmissionError as error:
            raise ExecutionRejected(
                error.code.value,
                f"print.{error.code.value}",
            ) from None

    @staticmethod
    def _result_state(
        result: DriverExecutionResult,
    ) -> tuple[PrintJobState, OutputEvidence | None, str | None, str | None]:
        if result.outcome is DriverOutcome.CONFIRMED and result.evidence is not None:
            return PrintJobState.OUTPUT_CONFIRMED, result.evidence, None, None
        if result.outcome is DriverOutcome.FAILED:
            return (
                PrintJobState.FAILED,
                None,
                result.error_code or "device_failed",
                result.message_key or "print.device_failed",
            )
        return (
            PrintJobState.OUTCOME_UNKNOWN,
            None,
            result.error_code or "outcome_unknown",
            result.message_key or "print.outcome_unknown",
        )

    def _recover_unknown(
        self, connection: Connection, job: RowMapping, *, now: datetime
    ) -> None:
        next_version = int(job["state_version"]) + 1
        connection.execute(
            update(public_print_jobs_table)
            .where(
                public_print_jobs_table.c.id == job["id"],
                public_print_jobs_table.c.state == "in_progress",
            )
            .values(
                state="outcome_unknown",
                state_version=next_version,
                terminal_at=timestamp(now),
                retryable=False,
                error_code="outcome_unknown",
                message_key="print.outcome_unknown",
            )
        )
        connection.execute(
            insert(public_print_job_events_table).values(
                job_id=job["id"],
                state_version=next_version,
                event_type="outcome_unknown",
                snapshot_json=self._snapshot(
                    job,
                    state="outcome_unknown",
                    state_version=next_version,
                    terminal_at=timestamp(now),
                    error_code="outcome_unknown",
                    message_key="print.outcome_unknown",
                ),
                occurred_at=timestamp(now),
            )
        )
        self._schedule_artifact_release_for_job(
            connection,
            job_id=str(job["id"]),
            state=PrintJobState.OUTCOME_UNKNOWN,
            now=now,
        )

    def _expire_accepted(
        self, connection: Connection, job: RowMapping, *, now: datetime
    ) -> None:
        next_version = int(job["state_version"]) + 1
        connection.execute(
            update(public_print_jobs_table)
            .where(
                public_print_jobs_table.c.id == job["id"],
                public_print_jobs_table.c.state == "accepted",
            )
            .values(
                state="expired",
                state_version=next_version,
                terminal_at=timestamp(now),
                retryable=False,
            )
        )
        connection.execute(
            insert(public_print_job_events_table).values(
                job_id=job["id"],
                state_version=next_version,
                event_type="expired",
                snapshot_json=self._snapshot(
                    job,
                    state="expired",
                    state_version=next_version,
                    terminal_at=timestamp(now),
                ),
                occurred_at=timestamp(now),
            )
        )
        self._schedule_artifact_release_for_job(
            connection,
            job_id=str(job["id"]),
            state=PrintJobState.EXPIRED,
            now=now,
        )

    def _schedule_artifact_release(
        self,
        connection: Connection,
        claim: ExecutionClaim,
        *,
        state: PrintJobState,
        now: datetime,
    ) -> None:
        self._schedule_artifact_release_for_job(
            connection,
            job_id=claim.job_id,
            state=state,
            now=now,
        )

    @staticmethod
    def _schedule_artifact_release_for_job(
        connection: Connection,
        *,
        job_id: str,
        state: PrintJobState,
        now: datetime,
    ) -> None:
        immediate = state is PrintJobState.OUTPUT_CONFIRMED
        delete_after = now if immediate else now + timedelta(hours=24)
        connection.execute(
            update(spool_artifacts_table)
            .where(
                spool_artifacts_table.c.job_id == job_id,
                spool_artifacts_table.c.state == "committed",
            )
            .values(
                state="delete_pending",
                retention_policy=("success_immediate" if immediate else "failure_24h"),
                delete_after=timestamp(delete_after),
            )
        )
        if immediate:
            connection.execute(
                update(spool_job_keys_table)
                .where(
                    spool_job_keys_table.c.job_id == job_id,
                    spool_job_keys_table.c.key_deleted_at.is_(None),
                )
                .values(wrapped_key=b"", key_deleted_at=timestamp(now))
            )

    def _device_is_busy(
        self, connection: Connection, *, device_id: str, now: datetime
    ) -> bool:
        if (
            connection.execute(
                select(device_tests_table.c.record_id).where(
                    device_tests_table.c.device_id == device_id,
                    or_(
                        device_tests_table.c.state == "in_progress",
                        and_(
                            device_tests_table.c.state == "accepted",
                            device_tests_table.c.io_deadline > timestamp(now),
                        ),
                    ),
                )
            ).first()
            is not None
        ):
            return True
        return (
            connection.execute(
                select(spool_reservations_table.c.id).where(
                    spool_reservations_table.c.device_id == device_id,
                    spool_reservations_table.c.reservation_kind == "execution_temp",
                    spool_reservations_table.c.state == "held",
                )
            ).first()
            is not None
        )

    @staticmethod
    def _release_reservation(
        connection: Connection, claim: ExecutionClaim, *, now: datetime
    ) -> None:
        connection.execute(
            update(spool_reservations_table)
            .where(
                spool_reservations_table.c.job_id == claim.job_id,
                spool_reservations_table.c.owner_id == claim.owner.owner_id,
                spool_reservations_table.c.owner_generation == claim.owner.generation,
                spool_reservations_table.c.reservation_kind == "execution_temp",
                spool_reservations_table.c.state == "held",
            )
            .values(state="released", released_at=timestamp(now))
        )

    @staticmethod
    def _fence(
        claim: ExecutionClaim, *, phase: str | None = None, now: datetime
    ) -> tuple[ColumnElement[bool], ...]:
        predicates: list[ColumnElement[bool]] = [
            physical_execution_attempts_table.c.attempt_id == claim.attempt_id,
            physical_execution_attempts_table.c.lease_id == claim.lease_id,
            physical_execution_attempts_table.c.owner_id == claim.owner.owner_id,
            physical_execution_attempts_table.c.owner_generation
            == claim.owner.generation,
            physical_execution_attempts_table.c.lease_expires_at > timestamp(now),
        ]
        if phase is not None:
            predicates.append(physical_execution_attempts_table.c.phase == phase)
        else:
            predicates.append(
                physical_execution_attempts_table.c.phase.in_(_ACTIVE_PHASES)
            )
        return tuple(predicates)

    def _attempt(
        self,
        connection: Connection,
        claim: ExecutionClaim,
        *,
        phase: str | None = None,
        now: datetime,
    ) -> RowMapping:
        row = (
            connection.execute(
                select(physical_execution_attempts_table).where(
                    *self._fence(claim, phase=phase, now=now)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise LeaseLost("The execution lease is no longer active.")
        return row

    @staticmethod
    def _job(connection: Connection, job_id: str) -> RowMapping:
        row = (
            connection.execute(
                select(public_print_jobs_table).where(
                    public_print_jobs_table.c.id == job_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise LeaseLost("The Print Job is no longer available.")
        return row

    @staticmethod
    def _snapshot(
        job: RowMapping,
        *,
        state: str,
        state_version: int,
        started_at: str | None = None,
        terminal_at: str | None = None,
        error_code: str | None = None,
        message_key: str | None = None,
        confirmation_evidence: str | None = None,
    ) -> str:
        values: dict[str, object] = {
            "accepted_at": job["accepted_at"],
            "contract_version": job["contract_version"],
            "device_id": job["device_id"],
            "intent_id": job["intent_id"],
            "job_id": job["id"],
            "state": state,
            "state_version": state_version,
        }
        for name, value in (
            ("started_at", started_at or job["started_at"]),
            ("terminal_at", terminal_at),
            ("error_code", error_code),
            ("message_key", message_key),
            ("confirmation_evidence", confirmation_evidence),
        ):
            if value is not None:
                values[name] = value
        return canonical_json(values)


__all__ = ["SqliteExecutionLedger"]
