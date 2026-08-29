from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..core.failures import ProblemCode
from ..db.schema import (
    device_work_admissions_table,
    spool_admission_keys_table,
    spool_artifacts_table,
    spool_reservations_table,
)
from ..documents import AdmissionAccepted
from ..runtime.store import RuntimeStore
from .errors import SpoolAdmissionError
from .limits import SpoolCapacityPolicy
from .manifest import (
    assert_exact_replay,
    manifest_from_row,
    raise_if_aborted,
    same_origin_submission,
    parse_timestamp,
    timestamp,
)
from .models import EncryptedArtifact
from .owner import SpoolOwner
from .proofs import insert_authority_proof, read_authority_proof
from .types import AdmissionManifest, AdmissionPlan


IdFactory = Callable[[], str]
_IDEMPOTENCY_RETENTION = timedelta(days=90)
_CONTENT_RETENTION = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class SpoolAdmissionLedger:
    """Own the SQLite admission, reservation, and staged-artifact records.

    Methods that accept a connection participate in the caller's transaction.
    This keeps the publication barrier atomic when the admission coordinator
    writes the public job and the durable spool records together.
    """

    store: RuntimeStore
    owner: SpoolOwner
    capacity: SpoolCapacityPolicy
    id_factory: IdFactory

    def replay(self, manifest: AdmissionManifest) -> AdmissionAccepted | None:
        with self.store.connection() as connection:
            existing = self.find_idempotency(connection, manifest)
            if existing is not None:
                assert_exact_replay(existing, manifest)
                raise_if_aborted(existing)
                if existing["state"] == "accepted":
                    return _accepted_from_row(existing, replayed=True)
                return None

            origin = self.find_origin(connection, manifest)
            if origin is None:
                return None
            if not same_origin_submission(origin, manifest):
                raise SpoolAdmissionError(ProblemCode.REQUEST_CONFLICT)
            raise_if_aborted(origin)
            if origin["state"] == "accepted":
                return _accepted_from_row(origin, replayed=True)
            return None

    def prepare(
        self, manifest: AdmissionManifest, *, now: datetime
    ) -> AdmissionPlan | AdmissionAccepted:
        """Find or create one admission and its held capacity reservation."""

        with self.store.immediate_transaction() as connection:
            existing = self.find_idempotency(connection, manifest)
            if existing is not None:
                assert_exact_replay(existing, manifest)
                raise_if_aborted(existing)
                if existing["state"] == "accepted":
                    return _accepted_from_row(existing, replayed=True)
                return self.resume_plan(connection, existing, now=now)

            origin = self.find_origin(connection, manifest)
            if origin is not None:
                if not same_origin_submission(origin, manifest):
                    raise SpoolAdmissionError(ProblemCode.REQUEST_CONFLICT)
                raise_if_aborted(origin)
                if origin["state"] == "accepted":
                    return _accepted_from_row(origin, replayed=True)
                return self.resume_plan(connection, origin, now=now)

            persistent_bytes = self.capacity.persistent_bytes(manifest)
            self.capacity.assert_quota(
                connection,
                device_id=manifest.device_id,
                original_bytes=manifest.original_size_bytes,
                persistent_bytes=persistent_bytes,
            )
            plan = AdmissionPlan(
                admission_id=self.new_id(),
                job_id=self.new_id(),
                reservation_id=self.new_id(),
                artifact_id=self.new_id(),
                manifest=manifest,
            )
            connection.execute(
                insert(device_work_admissions_table).values(
                    id=plan.admission_id,
                    planned_job_id=plan.job_id,
                    database=manifest.database,
                    scope_kind="paired_client",
                    organization_id=manifest.organization_id,
                    site_id=manifest.site_id,
                    pos_configuration_id=manifest.pos_configuration_id,
                    paired_client_id=manifest.paired_client_id,
                    idempotency_key=manifest.idempotency_key,
                    fingerprint=manifest.fingerprint,
                    state="staging",
                    job_id=None,
                    intent_id=manifest.intent_id,
                    device_id=manifest.device_id,
                    deadline_at=manifest.deadline_at,
                    original_size_bytes=manifest.original_size_bytes,
                    created_at=timestamp(now),
                    updated_at=timestamp(now),
                    accepted_at=None,
                    failed_at=None,
                    failure_code=None,
                    idempotency_expires_at=timestamp(now + _IDEMPOTENCY_RETENTION),
                    content_expires_at=timestamp(now + _CONTENT_RETENTION),
                    actor_id=manifest.actor_id,
                    binding_revision_id=manifest.binding_revision_id,
                    authorization_digest=manifest.authorization_digest,
                    operation=manifest.operation,
                    media_type=manifest.media_type,
                    normalized_options_digest=manifest.normalized_options_digest,
                    grant_scope_digest=manifest.grant_scope_digest,
                    origin_submission_key=manifest.origin_submission_key,
                    origin_kind=manifest.origin_kind,
                    origin_json=manifest.origin_json,
                    contract_major=manifest.contract_major,
                    copy_ordinal=manifest.copy_ordinal,
                )
            )
            insert_authority_proof(
                connection,
                admission_id=plan.admission_id,
                proof=manifest.authority_proof,
            )
            self.hold_reservation(
                connection,
                plan=plan,
                persistent_bytes=persistent_bytes,
                now=now,
            )
            return plan

    def resume_plan(
        self,
        connection: Connection,
        row: RowMapping,
        *,
        now: datetime,
    ) -> AdmissionPlan:
        proof = read_authority_proof(connection, admission_id=row["id"])
        manifest = manifest_from_row(row, proof)
        active = connection.execute(
            select(spool_reservations_table).where(
                spool_reservations_table.c.admission_id == row["id"],
                spool_reservations_table.c.state.in_(("held", "committed")),
            )
        ).mappings().first()
        if active is None:
            persistent_bytes = self.capacity.persistent_bytes(manifest)
            self.capacity.assert_quota(
                connection,
                device_id=manifest.device_id,
                original_bytes=manifest.original_size_bytes,
                persistent_bytes=persistent_bytes,
            )
            plan = AdmissionPlan(
                admission_id=row["id"],
                job_id=row["planned_job_id"],
                reservation_id=self.new_id(),
                artifact_id=self.new_id(),
                manifest=manifest,
            )
            self.hold_reservation(
                connection,
                plan=plan,
                persistent_bytes=persistent_bytes,
                now=now,
            )
            return plan

        artifact_id = connection.execute(
            select(spool_artifacts_table.c.id).where(
                spool_artifacts_table.c.admission_id == row["id"],
                spool_artifacts_table.c.artifact_kind == "original",
            )
        ).scalar_one_or_none()
        return AdmissionPlan(
            admission_id=row["id"],
            job_id=row["planned_job_id"],
            reservation_id=active["id"],
            artifact_id=artifact_id or self.new_id(),
            manifest=manifest,
        )

    def hold_reservation(
        self,
        connection: Connection,
        *,
        plan: AdmissionPlan,
        persistent_bytes: int,
        now: datetime,
    ) -> None:
        connection.execute(
            insert(spool_reservations_table).values(
                id=plan.reservation_id,
                reservation_kind="admission",
                admission_id=plan.admission_id,
                job_id=None,
                device_id=plan.manifest.device_id,
                owner_id=self.owner.owner_id,
                owner_generation=self.owner.generation,
                queue_slots=1,
                original_bytes=plan.manifest.original_size_bytes,
                persistent_bytes=persistent_bytes,
                temporary_bytes=0,
                state="held",
                created_at=timestamp(now),
                expires_at=timestamp(now + _CONTENT_RETENTION),
                released_at=None,
            )
        )

    def record_staged_artifact(
        self,
        plan: AdmissionPlan,
        *,
        encrypted: EncryptedArtifact,
        storage_ref: str,
        plaintext_sha256: bytes,
        now: datetime,
    ) -> None:
        with self.store.immediate_transaction() as connection:
            prior = connection.execute(
                select(spool_artifacts_table.c.storage_ref).where(
                    spool_artifacts_table.c.admission_id == plan.admission_id,
                    spool_artifacts_table.c.artifact_kind == "original",
                )
            ).scalar_one_or_none()
            if prior is not None:
                if prior != storage_ref:
                    raise SpoolAdmissionError(ProblemCode.RECOVERY_UNCERTAIN)
                return
            connection.execute(
                insert(spool_artifacts_table).values(
                    id=plan.artifact_id,
                    admission_id=plan.admission_id,
                    reservation_id=plan.reservation_id,
                    job_id=None,
                    aad_job_id=plan.job_id,
                    intent_id=plan.manifest.intent_id,
                    format_version=encrypted.format_version,
                    artifact_kind=encrypted.kind.value,
                    storage_ref=storage_ref,
                    nonce=encrypted.nonce,
                    plaintext_size_bytes=plan.manifest.original_size_bytes,
                    ciphertext_size_bytes=len(encrypted.ciphertext),
                    plaintext_sha256=plaintext_sha256,
                    state="staging",
                    retention_policy="active",
                    created_at=timestamp(now),
                    committed_at=None,
                    delete_after=None,
                    deleted_at=None,
                    delete_attempts=0,
                    last_delete_error_code=None,
                )
            )

    def original_artifact(
        self, admission_id: str, *, connection: Connection | None = None
    ) -> RowMapping | None:
        if connection is not None:
            return connection.execute(
                select(spool_artifacts_table).where(
                    spool_artifacts_table.c.admission_id == admission_id,
                    spool_artifacts_table.c.artifact_kind == "original",
                )
            ).mappings().first()
        with self.store.connection() as read_connection:
            return self.original_artifact(admission_id, connection=read_connection)

    def plan_from_rows(
        self, admission: RowMapping, artifact: RowMapping
    ) -> AdmissionPlan:
        with self.store.connection() as connection:
            reservation = connection.execute(
                select(spool_reservations_table).where(
                    spool_reservations_table.c.admission_id == admission["id"],
                    spool_reservations_table.c.state.in_(("held", "committed")),
                )
            ).mappings().first()
            proof = read_authority_proof(connection, admission_id=admission["id"])
        if reservation is None:
            raise SpoolAdmissionError(ProblemCode.RECOVERY_UNCERTAIN)
        return AdmissionPlan(
            admission_id=admission["id"],
            job_id=admission["planned_job_id"],
            reservation_id=reservation["id"],
            artifact_id=artifact["id"],
            manifest=manifest_from_row(admission, proof),
        )

    def staging_admissions(self) -> tuple[RowMapping, ...]:
        with self.store.connection() as connection:
            return tuple(
                connection.execute(
                    select(device_work_admissions_table).where(
                        device_work_admissions_table.c.state.in_(
                            ("staging", "finalizing")
                        )
                    )
                ).mappings()
            )

    def staging_storage_refs(self, admission_id: str) -> tuple[str, ...]:
        with self.store.connection() as connection:
            return tuple(
                connection.execute(
                    select(spool_artifacts_table.c.storage_ref).where(
                        spool_artifacts_table.c.admission_id == admission_id,
                        spool_artifacts_table.c.state == "staging",
                    )
                ).scalars()
            )

    def has_staging_artifact(self, admission_id: str) -> bool:
        with self.store.connection() as connection:
            return (
                connection.execute(
                    select(spool_artifacts_table.c.id).where(
                        spool_artifacts_table.c.admission_id == admission_id,
                        spool_artifacts_table.c.state == "staging",
                    )
                ).scalar_one_or_none()
                is not None
            )

    def remove_missing_artifact(
        self, plan: AdmissionPlan, storage_ref: str, *, now: datetime
    ) -> int:
        with self.store.immediate_transaction() as connection:
            connection.execute(
                delete(spool_artifacts_table).where(
                    spool_artifacts_table.c.admission_id == plan.admission_id,
                    spool_artifacts_table.c.storage_ref == storage_ref,
                    spool_artifacts_table.c.state == "staging",
                )
            )
            result = connection.execute(
                update(spool_reservations_table)
                .where(
                    spool_reservations_table.c.id == plan.reservation_id,
                    spool_reservations_table.c.state.in_(("held", "committed")),
                )
                .values(state="released", released_at=timestamp(now))
            )
        return int(result.rowcount or 0)

    def release_reservation(self, reservation_id: str, *, now: datetime) -> None:
        with self.store.immediate_transaction() as connection:
            connection.execute(
                update(spool_reservations_table)
                .where(
                    spool_reservations_table.c.id == reservation_id,
                    spool_reservations_table.c.state.in_(("held", "committed")),
                )
                .values(state="released", released_at=timestamp(now))
            )

    def release_admission_reservations(
        self, admission_id: str, *, now: datetime
    ) -> int:
        with self.store.immediate_transaction() as connection:
            result = connection.execute(
                update(spool_reservations_table)
                .where(
                    spool_reservations_table.c.admission_id == admission_id,
                    spool_reservations_table.c.state.in_(("held", "committed")),
                )
                .values(state="released", released_at=timestamp(now))
            )
        return int(result.rowcount or 0)

    def abort_preaccept(
        self, admission_id: str, *, code: ProblemCode, now: datetime
    ) -> int:
        if code not in {
            ProblemCode.EXPIRED,
            ProblemCode.CAPABILITY_CHANGED,
            ProblemCode.CERTIFICATION_REQUIRED,
            ProblemCode.RECOVERY_UNCERTAIN,
            ProblemCode.SERVICE_UNAVAILABLE,
        }:
            raise ValueError("The staging admission failure code is not supported.")
        with self.store.immediate_transaction() as connection:
            connection.execute(
                delete(spool_artifacts_table).where(
                    spool_artifacts_table.c.admission_id == admission_id,
                    spool_artifacts_table.c.state == "staging",
                )
            )
            connection.execute(
                delete(spool_admission_keys_table).where(
                    spool_admission_keys_table.c.admission_id == admission_id
                )
            )
            result = connection.execute(
                update(spool_reservations_table)
                .where(
                    spool_reservations_table.c.admission_id == admission_id,
                    spool_reservations_table.c.state.in_(("held", "committed")),
                )
                .values(state="released", released_at=timestamp(now))
            )
            connection.execute(
                update(device_work_admissions_table)
                .where(
                    device_work_admissions_table.c.id == admission_id,
                    device_work_admissions_table.c.state.in_(
                        ("staging", "finalizing")
                    ),
                )
                .values(
                    state="aborted",
                    failed_at=timestamp(now),
                    failure_code=code.value,
                    updated_at=timestamp(now),
                )
            )
        return int(result.rowcount or 0)

    def find_idempotency(
        self, connection: Connection, manifest: AdmissionManifest
    ) -> RowMapping | None:
        return connection.execute(
            select(device_work_admissions_table).where(
                device_work_admissions_table.c.scope_kind == "paired_client",
                device_work_admissions_table.c.organization_id
                == manifest.organization_id,
                device_work_admissions_table.c.site_id == manifest.site_id,
                device_work_admissions_table.c.pos_configuration_id
                == manifest.pos_configuration_id,
                device_work_admissions_table.c.paired_client_id
                == manifest.paired_client_id,
                device_work_admissions_table.c.idempotency_key
                == manifest.idempotency_key,
            )
        ).mappings().first()

    def find_origin(
        self, connection: Connection, manifest: AdmissionManifest
    ) -> RowMapping | None:
        return connection.execute(
            select(device_work_admissions_table).where(
                device_work_admissions_table.c.scope_kind == "paired_client",
                device_work_admissions_table.c.organization_id
                == manifest.organization_id,
                device_work_admissions_table.c.site_id == manifest.site_id,
                device_work_admissions_table.c.pos_configuration_id
                == manifest.pos_configuration_id,
                device_work_admissions_table.c.paired_client_id
                == manifest.paired_client_id,
                device_work_admissions_table.c.origin_submission_key
                == manifest.origin_submission_key,
            )
        ).mappings().first()

    def new_id(self) -> str:
        value = self.id_factory()
        if not isinstance(value, str) or not value or len(value) > 128:
            raise SpoolAdmissionError(ProblemCode.INTERNAL_ERROR)
        return value


def _accepted_from_row(row: RowMapping, *, replayed: bool) -> AdmissionAccepted:
    return AdmissionAccepted(
        print_intent_id=row["intent_id"],
        print_job_id=row["job_id"],
        device_id=row["device_id"],
        accepted_at=parse_timestamp(row["accepted_at"]),
        state_version=1,
        replayed=replayed,
    )


__all__ = ["SpoolAdmissionLedger"]
