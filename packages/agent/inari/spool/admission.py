from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from ..core.failures import ProblemCode, ProblemDetails
from ..db.schema import (
    device_work_admissions_table,
    public_print_job_events_table,
    public_print_jobs_table,
    spool_admission_keys_table,
    spool_artifacts_table,
    spool_job_keys_table,
    spool_nonce_reservations_table,
    spool_reservations_table,
    spool_root_key_provisioning_table,
    spool_root_keys_table,
)
from ..documents import AdmissionAccepted, DurableAdmission
from ..runtime.store import DatabaseCommitUncertainError, RuntimeStore
from .crypto import (
    ArtifactIntegrityError,
    NonceReservationUncertainError,
    SpoolCrypto,
    SpoolCryptoError,
)
from .authority import ActiveAuthorityGuard
from .filesystem import (
    ArtifactFileStore,
    SpoolCommitUncertainError,
    SpoolStorageError,
)
from .errors import SpoolAdmissionError
from .ledger import SpoolAdmissionLedger
from .keys import (
    SpoolRootKeyAlreadyExists,
    SpoolRootKeyService,
    SpoolRootKeyUnavailable,
)
from .limits import (
    DEFAULT_AGENT_ORIGINAL_LIMIT,
    DEFAULT_AGENT_PERSISTENT_LIMIT,
    DEFAULT_AGENT_QUEUE_LIMIT,
    DEFAULT_DEVICE_ORIGINAL_LIMIT,
    DEFAULT_DEVICE_PERSISTENT_LIMIT,
    DEFAULT_DEVICE_QUEUE_LIMIT,
    MIB,
    SpoolCapacityPolicy,
)
from .models import ArtifactKind, EncryptedArtifact, WrappedDataKey
from .manifest import canonical_json, manifest_from_admission, parse_timestamp
from .manifest import timestamp as format_timestamp
from .manifest import utc
from .owner import SpoolOwner
from .types import AdmissionPlan


_ROOT_ROTATION_PERIOD = timedelta(days=90)
_MAX_ENCRYPTED_RECEIPT_BYTES = 2 * MIB + 16


class _RestartAdmission(Exception):
    """Restart one staging admission with a fresh capacity reservation."""


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    finalized_admissions: int = 0
    failed_print_jobs: int = 0
    released_admissions: int = 0
    deleted_staging_files: int = 0
    deleted_orphan_objects: int = 0


@dataclass(frozen=True, slots=True)
class _RootKeyLease:
    version: int
    key: bytes


class DurableSpoolAdmissionStore:
    """Own durable admission across SQLite and the encrypted Device Spool."""

    def __init__(
        self,
        *,
        store: RuntimeStore,
        files: ArtifactFileStore,
        root_keys: SpoolRootKeyService,
        owner: SpoolOwner,
        authority_guard: ActiveAuthorityGuard,
        crypto: SpoolCrypto | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        device_queue_limit: int = DEFAULT_DEVICE_QUEUE_LIMIT,
        agent_queue_limit: int = DEFAULT_AGENT_QUEUE_LIMIT,
        device_original_limit: int = DEFAULT_DEVICE_ORIGINAL_LIMIT,
        agent_original_limit: int = DEFAULT_AGENT_ORIGINAL_LIMIT,
        device_persistent_limit: int = DEFAULT_DEVICE_PERSISTENT_LIMIT,
        agent_persistent_limit: int = DEFAULT_AGENT_PERSISTENT_LIMIT,
    ) -> None:
        if not isinstance(store, RuntimeStore):
            raise TypeError("store must be a RuntimeStore")
        if not isinstance(files, ArtifactFileStore):
            raise TypeError("files must be an ArtifactFileStore")
        if not isinstance(root_keys, SpoolRootKeyService):
            raise TypeError("root_keys must be a SpoolRootKeyService")
        if not isinstance(owner, SpoolOwner):
            raise TypeError("owner must be a SpoolOwner")
        self._store = store
        self._files = files
        self._root_keys = root_keys
        self._owner = owner
        self._authority_guard = authority_guard
        self._crypto = crypto or SpoolCrypto()
        self._clock = clock
        self._id_factory = id_factory
        self._capacity = SpoolCapacityPolicy(
            device_queue_limit=device_queue_limit,
            agent_queue_limit=agent_queue_limit,
            device_original_limit=device_original_limit,
            agent_original_limit=agent_original_limit,
            device_persistent_limit=device_persistent_limit,
            agent_persistent_limit=agent_persistent_limit,
        )
        self._ledger = SpoolAdmissionLedger(
            store=store,
            owner=owner,
            capacity=self._capacity,
            id_factory=id_factory,
        )
        self._admission_lock = threading.RLock()

    async def accept(self, admission: DurableAdmission) -> AdmissionAccepted:
        """Durably accept new work or return its exact accepted replay."""

        return await asyncio.to_thread(self._accept_serialized, admission)

    async def reconcile(self) -> ReconciliationReport:
        """Repair incomplete admission phases before content work becomes ready."""

        return await asyncio.to_thread(self._reconcile_serialized)

    def _accept_serialized(self, admission: DurableAdmission) -> AdmissionAccepted:
        with self._admission_lock:
            return self._accept(admission)

    def _reconcile_serialized(self) -> ReconciliationReport:
        with self._admission_lock:
            return self._reconcile()

    def _accept(self, admission: DurableAdmission) -> AdmissionAccepted:
        manifest = manifest_from_admission(admission)
        replay = self._ledger.replay(manifest)
        if replay is not None:
            return replay
        now = utc(self._clock())
        if now >= admission.deadline.expires_at:
            raise SpoolAdmissionError(ProblemCode.EXPIRED)
        self._capacity.assert_volume_reserve(
            self._files.root,
            required_bytes=self._capacity.persistent_bytes(manifest),
        )

        while True:
            prepared = self._ledger.prepare(manifest, now=now)
            if isinstance(prepared, AdmissionAccepted):
                return prepared
            plan = prepared

            try:
                existing = self._resume_promoted(plan, now=now)
                if existing is not None:
                    return existing
                lease, data_key = self._data_key_for(plan, now=now)
                encrypted = self._crypto.encrypt_artifact(
                    data_key=data_key,
                    job_id=plan.job_id,
                    intent_id=manifest.intent_id,
                    kind=ArtifactKind.ORIGINAL,
                    plaintext=admission.work.document.content,
                    reserve_nonce=lambda nonce: self._reserve_nonce(
                        admission_id=plan.admission_id,
                        domain="artifact",
                        purpose="original",
                        root_version=None,
                        planned_job_id=plan.job_id,
                        nonce=nonce,
                        now=now,
                    ),
                )
                staged = self._files.stage_bytes(
                    encrypted.ciphertext,
                    max_bytes=_MAX_ENCRYPTED_RECEIPT_BYTES,
                )
                self._ledger.record_staged_artifact(
                    plan,
                    encrypted=encrypted,
                    storage_ref=staged.storage_ref,
                    plaintext_sha256=hashlib.sha256(
                        admission.work.document.content
                    ).digest(),
                    now=now,
                )
                self._files.commit(staged)
                self._verify_artifact(plan, lease=lease, data_key=data_key)
                return self._finalize(plan, now=utc(self._clock()))
            except _RestartAdmission:
                continue
            except SpoolAdmissionError as error:
                if error.code in {
                    ProblemCode.EXPIRED,
                    ProblemCode.CAPABILITY_CHANGED,
                    ProblemCode.CERTIFICATION_REQUIRED,
                }:
                    self._abort_preaccept(
                        plan.admission_id,
                        code=error.code,
                        now=utc(self._clock()),
                    )
                raise
            except SpoolCommitUncertainError:
                raise SpoolAdmissionError(
                    ProblemCode.RECOVERY_UNCERTAIN,
                    details=ProblemDetails(
                        device_id=manifest.device_id,
                        job_id=plan.job_id,
                    ),
                ) from None
            except (DatabaseCommitUncertainError, NonceReservationUncertainError):
                raise SpoolAdmissionError(
                    ProblemCode.RECOVERY_UNCERTAIN,
                    details=ProblemDetails(
                        device_id=manifest.device_id,
                        job_id=plan.job_id,
                    ),
                ) from None
            except SpoolRootKeyUnavailable:
                self._ledger.release_reservation(plan.reservation_id, now=now)
                raise SpoolAdmissionError(ProblemCode.SERVICE_UNAVAILABLE) from None
            except SpoolStorageError:
                if self._ledger.has_staging_artifact(plan.admission_id):
                    raise SpoolAdmissionError(
                        ProblemCode.RECOVERY_UNCERTAIN,
                        details=ProblemDetails(
                            device_id=manifest.device_id,
                            job_id=plan.job_id,
                        ),
                    ) from None
                self._ledger.release_reservation(plan.reservation_id, now=now)
                raise SpoolAdmissionError(ProblemCode.SPOOL_STORAGE_LOW) from None
            except SpoolCryptoError:
                self._abort_preaccept(
                    plan.admission_id,
                    code=ProblemCode.RECOVERY_UNCERTAIN,
                    now=utc(self._clock()),
                )
                raise SpoolAdmissionError(ProblemCode.RECOVERY_UNCERTAIN) from None
            except IntegrityError:
                self._ledger.release_reservation(plan.reservation_id, now=now)
                raise SpoolAdmissionError(ProblemCode.REQUEST_CONFLICT) from None

    def _data_key_for(
        self, plan: AdmissionPlan, *, now: datetime
    ) -> tuple[_RootKeyLease, bytes]:
        with self._store.connection() as connection:
            row = (
                connection.execute(
                    select(spool_admission_keys_table).where(
                        spool_admission_keys_table.c.admission_id == plan.admission_id
                    )
                )
                .mappings()
                .first()
            )
        if row is not None:
            root_key = self._root_keys.read(row["root_version"])
            if root_key is None:
                raise SpoolRootKeyUnavailable
            lease = _RootKeyLease(version=row["root_version"], key=root_key)
            wrapped = WrappedDataKey(
                format_version=row["format_version"],
                root_key_version=row["root_version"],
                job_id=plan.job_id,
                intent_id=plan.manifest.intent_id,
                nonce=row["wrap_nonce"],
                ciphertext=row["wrapped_key"],
            )
            return lease, self._crypto.unwrap_data_key(
                wrapped=wrapped,
                root_key=root_key,
                job_id=plan.job_id,
                intent_id=plan.manifest.intent_id,
            )

        lease = self._ensure_current_root(now=now)
        data_key = self._crypto.new_data_key()
        wrapped = self._crypto.wrap_data_key(
            data_key=data_key,
            root_key=lease.key,
            root_key_version=lease.version,
            job_id=plan.job_id,
            intent_id=plan.manifest.intent_id,
            reserve_nonce=lambda nonce: self._reserve_nonce(
                admission_id=plan.admission_id,
                domain="root_wrap",
                purpose="data_key_wrap",
                root_version=lease.version,
                planned_job_id=None,
                nonce=nonce,
                now=now,
            ),
        )
        with self._store.immediate_transaction() as connection:
            connection.execute(
                insert(spool_admission_keys_table).values(
                    admission_id=plan.admission_id,
                    planned_job_id=plan.job_id,
                    root_version=lease.version,
                    intent_id=plan.manifest.intent_id,
                    format_version=wrapped.format_version,
                    wrap_nonce=wrapped.nonce,
                    wrapped_key=wrapped.ciphertext,
                    created_at=format_timestamp(now),
                )
            )
        return lease, data_key

    def _ensure_current_root(self, *, now: datetime) -> _RootKeyLease:
        descriptor: RowMapping | None
        with self._store.immediate_transaction() as connection:
            descriptor = (
                connection.execute(
                    select(spool_root_keys_table).where(
                        spool_root_keys_table.c.state == "current"
                    )
                )
                .mappings()
                .first()
            )
            if descriptor is None:
                descriptor = (
                    connection.execute(
                        select(spool_root_keys_table).where(
                            spool_root_keys_table.c.state == "provisioning"
                        )
                    )
                    .mappings()
                    .first()
                )
            if descriptor is None:
                highest = connection.execute(
                    select(func.max(spool_root_keys_table.c.root_version))
                ).scalar_one()
                version = int(highest or 0) + 1
                key_identifier = f"inari/device-spool/root-key/v{version}"
                timestamp = format_timestamp(now)
                connection.execute(
                    insert(spool_root_keys_table).values(
                        root_version=version,
                        key_identifier=key_identifier,
                        state="provisioning",
                        created_at=timestamp,
                        rotate_after=format_timestamp(now + _ROOT_ROTATION_PERIOD),
                        retiring_at=None,
                        retired_at=None,
                    )
                )
                connection.execute(
                    insert(spool_root_key_provisioning_table).values(
                        root_version=version,
                        state="provisioning",
                        protected_key_identifier=key_identifier,
                        provisioned_at=None,
                        error_code=None,
                        updated_at=timestamp,
                    )
                )
                descriptor = (
                    connection.execute(
                        select(spool_root_keys_table).where(
                            spool_root_keys_table.c.root_version == version
                        )
                    )
                    .mappings()
                    .one()
                )

        version = descriptor["root_version"]
        try:
            key = self._root_keys.read(version)
            if key is None:
                if descriptor["state"] != "provisioning":
                    self._quarantine_root(version, now=now)
                    raise SpoolRootKeyUnavailable
                try:
                    key = self._root_keys.generate_current(version)
                except SpoolRootKeyAlreadyExists:
                    key = self._root_keys.read(version)
                    if key is None:
                        raise SpoolRootKeyUnavailable
        except SpoolRootKeyUnavailable:
            self._quarantine_root(version, now=now)
            raise

        if descriptor["state"] == "provisioning":
            with self._store.immediate_transaction() as connection:
                current_state = connection.execute(
                    select(spool_root_keys_table.c.state).where(
                        spool_root_keys_table.c.root_version == version
                    )
                ).scalar_one()
                if current_state == "provisioning":
                    connection.execute(
                        update(spool_root_keys_table)
                        .where(spool_root_keys_table.c.root_version == version)
                        .values(state="current")
                    )
                    connection.execute(
                        update(spool_root_key_provisioning_table)
                        .where(
                            spool_root_key_provisioning_table.c.root_version == version
                        )
                        .values(
                            state="available",
                            provisioned_at=format_timestamp(now),
                            error_code=None,
                            updated_at=format_timestamp(now),
                        )
                    )
        return _RootKeyLease(version=version, key=key)

    def _quarantine_root(self, version: int, *, now: datetime) -> None:
        with self._store.immediate_transaction() as connection:
            connection.execute(
                update(spool_root_key_provisioning_table)
                .where(spool_root_key_provisioning_table.c.root_version == version)
                .values(
                    state="quarantined",
                    provisioned_at=None,
                    error_code="spool_key_unavailable",
                    updated_at=format_timestamp(now),
                )
            )

    def _reserve_nonce(
        self,
        *,
        admission_id: str,
        domain: str,
        purpose: str,
        root_version: int | None,
        planned_job_id: str | None,
        nonce: bytes,
        now: datetime,
    ) -> bool:
        try:
            with self._store.immediate_transaction() as connection:
                connection.execute(
                    insert(spool_nonce_reservations_table).values(
                        id=self._ledger.new_id(),
                        domain=domain,
                        root_version=root_version,
                        planned_job_id=planned_job_id,
                        purpose=purpose,
                        admission_id=admission_id,
                        nonce=nonce,
                        created_at=format_timestamp(now),
                    )
                )
        except IntegrityError:
            return False
        except DatabaseCommitUncertainError:
            raise NonceReservationUncertainError from None
        return True

    def _resume_promoted(
        self, plan: AdmissionPlan, *, now: datetime
    ) -> AdmissionAccepted | None:
        artifact = self._ledger.original_artifact(plan.admission_id)
        if artifact is None:
            return None
        if not self._files.complete_publication(artifact["storage_ref"]):
            self._ledger.remove_missing_artifact(plan, artifact["storage_ref"], now=now)
            raise _RestartAdmission
        lease, data_key = self._data_key_for(plan, now=now)
        self._verify_artifact(plan, lease=lease, data_key=data_key)
        return self._finalize(plan, now=now)

    def _verify_artifact(
        self,
        plan: AdmissionPlan,
        *,
        lease: _RootKeyLease,
        data_key: bytes,
    ) -> None:
        del lease
        row = self._ledger.original_artifact(plan.admission_id)
        if row is None:
            raise SpoolAdmissionError(ProblemCode.RECOVERY_UNCERTAIN)
        ciphertext = self._files.read_bytes(
            row["storage_ref"], max_bytes=_MAX_ENCRYPTED_RECEIPT_BYTES
        )
        plaintext = self._crypto.decrypt_artifact(
            data_key=data_key,
            artifact=EncryptedArtifact(
                format_version=row["format_version"],
                job_id=row["aad_job_id"],
                intent_id=row["intent_id"],
                kind=ArtifactKind(row["artifact_kind"]),
                nonce=row["nonce"],
                ciphertext=ciphertext,
            ),
        )
        if (
            len(plaintext) != row["plaintext_size_bytes"]
            or hashlib.sha256(plaintext).digest() != row["plaintext_sha256"]
        ):
            raise ArtifactIntegrityError

    def _finalize(self, plan: AdmissionPlan, *, now: datetime) -> AdmissionAccepted:
        timestamp = format_timestamp(now)
        with self._store.immediate_transaction() as connection:
            row = (
                connection.execute(
                    select(device_work_admissions_table).where(
                        device_work_admissions_table.c.id == plan.admission_id
                    )
                )
                .mappings()
                .one()
            )
            if row["state"] == "accepted":
                return _accepted_from_row(row, replayed=True)
            if parse_timestamp(row["deadline_at"]) <= now:
                raise SpoolAdmissionError(ProblemCode.EXPIRED)
            artifact = (
                connection.execute(
                    select(spool_artifacts_table).where(
                        spool_artifacts_table.c.admission_id == plan.admission_id,
                        spool_artifacts_table.c.artifact_kind == "original",
                    )
                )
                .mappings()
                .one()
            )
            if not self._files.exists(artifact["storage_ref"]):
                raise SpoolAdmissionError(ProblemCode.RECOVERY_UNCERTAIN)
            staging_key = (
                connection.execute(
                    select(spool_admission_keys_table).where(
                        spool_admission_keys_table.c.admission_id == plan.admission_id
                    )
                )
                .mappings()
                .one()
            )
            self._authority_guard.check(
                connection,
                plan.manifest.authority_proof,
                now=now,
            )
            connection.execute(
                update(device_work_admissions_table)
                .where(
                    device_work_admissions_table.c.id == plan.admission_id,
                    device_work_admissions_table.c.state == "staging",
                )
                .values(state="finalizing", updated_at=timestamp)
            )
            connection.execute(
                insert(public_print_jobs_table).values(
                    id=plan.job_id,
                    admission_id=plan.admission_id,
                    authority_proof_id=plan.manifest.authority_proof.proof_id,
                    intent_id=row["intent_id"],
                    device_id=row["device_id"],
                    scope_kind=row["scope_kind"],
                    organization_id=row["organization_id"],
                    site_id=row["site_id"],
                    pos_configuration_id=row["pos_configuration_id"],
                    paired_client_id=row["paired_client_id"],
                    origin_kind=row["origin_kind"],
                    origin_json=row["origin_json"],
                    managed_work_id=None,
                    state="accepted",
                    state_version=1,
                    accepted_at=timestamp,
                    started_at=None,
                    terminal_at=None,
                    expires_at=row["deadline_at"],
                    retryable=False,
                    error_code=None,
                    message_key=None,
                    confirmation_evidence=None,
                    contract_version=f"v{row['contract_major']}",
                )
            )
            connection.execute(
                insert(spool_job_keys_table).values(
                    job_id=plan.job_id,
                    root_version=staging_key["root_version"],
                    intent_id=staging_key["intent_id"],
                    format_version=staging_key["format_version"],
                    wrap_nonce=staging_key["wrap_nonce"],
                    wrapped_key=staging_key["wrapped_key"],
                    created_at=staging_key["created_at"],
                    key_deleted_at=None,
                )
            )
            connection.execute(
                update(spool_reservations_table)
                .where(spool_reservations_table.c.id == plan.reservation_id)
                .values(job_id=plan.job_id, state="committed")
            )
            connection.execute(
                update(spool_artifacts_table)
                .where(spool_artifacts_table.c.id == artifact["id"])
                .values(
                    job_id=plan.job_id,
                    state="committed",
                    committed_at=timestamp,
                )
            )
            connection.execute(
                update(device_work_admissions_table)
                .where(device_work_admissions_table.c.id == plan.admission_id)
                .values(
                    state="accepted",
                    job_id=plan.job_id,
                    accepted_at=timestamp,
                    updated_at=timestamp,
                )
            )
            snapshot = canonical_json(
                {
                    "accepted_at": timestamp,
                    "contract_version": f"v{row['contract_major']}",
                    "device_id": row["device_id"],
                    "intent_id": row["intent_id"],
                    "job_id": plan.job_id,
                    "state": "accepted",
                    "state_version": 1,
                }
            )
            connection.execute(
                insert(public_print_job_events_table).values(
                    job_id=plan.job_id,
                    state_version=1,
                    event_type="accepted",
                    snapshot_json=snapshot,
                    occurred_at=timestamp,
                )
            )
            connection.execute(
                delete(spool_admission_keys_table).where(
                    spool_admission_keys_table.c.admission_id == plan.admission_id
                )
            )
        return AdmissionAccepted(
            print_intent_id=plan.manifest.intent_id,
            print_job_id=plan.job_id,
            device_id=plan.manifest.device_id,
            accepted_at=now,
            state_version=1,
            replayed=False,
        )

    def _reconcile(self) -> ReconciliationReport:
        finalized = 0
        released = 0
        deleted_staging = 0
        deleted_objects = 0
        failed_jobs = 0
        now = utc(self._clock())
        admissions = self._ledger.staging_admissions()
        referenced: set[str] = set()
        for row in admissions:
            try:
                artifact = self._ledger.original_artifact(row["id"])
                if artifact is None:
                    released += self._ledger.release_admission_reservations(
                        row["id"], now=now
                    )
                    continue
                referenced.add(artifact["storage_ref"])
                plan = self._ledger.plan_from_rows(row, artifact)
                if not self._files.complete_publication(artifact["storage_ref"]):
                    released += self._ledger.remove_missing_artifact(
                        plan, artifact["storage_ref"], now=now
                    )
                    continue
                lease, data_key = self._data_key_for(plan, now=now)
                self._verify_artifact(plan, lease=lease, data_key=data_key)
                self._finalize(plan, now=now)
                finalized += 1
            except (SpoolCommitUncertainError, DatabaseCommitUncertainError):
                continue
            except SpoolAdmissionError as error:
                if error.code is ProblemCode.SERVICE_UNAVAILABLE:
                    continue
                code = error.code
                if code not in {
                    ProblemCode.EXPIRED,
                    ProblemCode.CAPABILITY_CHANGED,
                    ProblemCode.CERTIFICATION_REQUIRED,
                }:
                    code = ProblemCode.RECOVERY_UNCERTAIN
                released += self._abort_preaccept(row["id"], code=code, now=now)
            except SpoolCryptoError:
                released += self._abort_preaccept(
                    row["id"], code=ProblemCode.RECOVERY_UNCERTAIN, now=now
                )
            except SpoolStorageError:
                continue

        failed_jobs += self._fail_accepted_jobs_with_missing_content(now=now)

        for storage_ref in self._files.staged_references():
            if storage_ref not in referenced:
                self._files.discard_staged(storage_ref)
                deleted_staging += 1
        with self._store.connection() as connection:
            referenced_objects = set(
                connection.execute(
                    select(spool_artifacts_table.c.storage_ref)
                ).scalars()
            )
        for storage_ref in self._files.object_references():
            if storage_ref not in referenced_objects:
                self._files.delete(storage_ref)
                deleted_objects += 1
        return ReconciliationReport(
            finalized_admissions=finalized,
            failed_print_jobs=failed_jobs,
            released_admissions=released,
            deleted_staging_files=deleted_staging,
            deleted_orphan_objects=deleted_objects,
        )

    def _fail_accepted_jobs_with_missing_content(self, *, now: datetime) -> int:
        with self._store.connection() as connection:
            jobs = tuple(
                connection.execute(
                    select(public_print_jobs_table).where(
                        public_print_jobs_table.c.state == "accepted"
                    )
                ).mappings()
            )
        failed = 0
        for job in jobs:
            with self._store.connection() as connection:
                artifact = (
                    connection.execute(
                        select(spool_artifacts_table).where(
                            spool_artifacts_table.c.job_id == job["id"],
                            spool_artifacts_table.c.artifact_kind == "original",
                        )
                    )
                    .mappings()
                    .first()
                )
            try:
                if artifact is not None and self._files.exists(artifact["storage_ref"]):
                    continue
            except SpoolStorageError:
                continue
            failed += self._fail_accepted_job_without_content(
                job,
                artifact=artifact,
                now=now,
            )
        return failed

    def _fail_accepted_job_without_content(
        self,
        job: RowMapping,
        *,
        artifact: RowMapping | None,
        now: datetime,
    ) -> int:
        timestamp = format_timestamp(now)
        next_version = int(job["state_version"]) + 1
        with self._store.immediate_transaction() as connection:
            result = connection.execute(
                update(public_print_jobs_table)
                .where(
                    public_print_jobs_table.c.id == job["id"],
                    public_print_jobs_table.c.state == "accepted",
                    public_print_jobs_table.c.state_version == job["state_version"],
                )
                .values(
                    state="failed",
                    state_version=next_version,
                    terminal_at=timestamp,
                    retryable=False,
                    error_code=ProblemCode.SERVICE_UNAVAILABLE.value,
                    message_key="print.service_unavailable",
                )
            )
            if not result.rowcount:
                return 0
            snapshot = canonical_json(
                {
                    "device_id": job["device_id"],
                    "error_code": ProblemCode.SERVICE_UNAVAILABLE.value,
                    "intent_id": job["intent_id"],
                    "job_id": job["id"],
                    "message_key": "print.service_unavailable",
                    "state": "failed",
                    "state_version": next_version,
                    "terminal_at": timestamp,
                }
            )
            connection.execute(
                insert(public_print_job_events_table).values(
                    job_id=job["id"],
                    state_version=next_version,
                    event_type="failed",
                    snapshot_json=snapshot,
                    occurred_at=timestamp,
                )
            )
            if artifact is not None:
                connection.execute(
                    update(spool_artifacts_table)
                    .where(
                        spool_artifacts_table.c.id == artifact["id"],
                        spool_artifacts_table.c.state == "committed",
                    )
                    .values(
                        state="deleted",
                        retention_policy="integrity_immediate",
                        delete_after=timestamp,
                        deleted_at=timestamp,
                        last_delete_error_code=None,
                    )
                )
            connection.execute(
                update(spool_job_keys_table)
                .where(
                    spool_job_keys_table.c.job_id == job["id"],
                    spool_job_keys_table.c.key_deleted_at.is_(None),
                )
                .values(wrapped_key=b"", key_deleted_at=timestamp)
            )
            connection.execute(
                update(spool_reservations_table)
                .where(
                    spool_reservations_table.c.job_id == job["id"],
                    spool_reservations_table.c.state == "committed",
                )
                .values(state="released", released_at=timestamp)
            )
        return 1

    def _abort_preaccept(
        self,
        admission_id: str,
        *,
        code: ProblemCode,
        now: datetime,
    ) -> int:
        if code not in {
            ProblemCode.EXPIRED,
            ProblemCode.CAPABILITY_CHANGED,
            ProblemCode.CERTIFICATION_REQUIRED,
            ProblemCode.RECOVERY_UNCERTAIN,
            ProblemCode.SERVICE_UNAVAILABLE,
        }:
            raise ValueError("The staging admission failure code is not supported.")
        storage_refs = self._ledger.staging_storage_refs(admission_id)
        for storage_ref in storage_refs:
            self._files.discard_staged(storage_ref)
            self._files.delete(storage_ref)

        return self._ledger.abort_preaccept(admission_id, code=code, now=now)


def _accepted_from_row(row: RowMapping, *, replayed: bool) -> AdmissionAccepted:
    return AdmissionAccepted(
        print_intent_id=row["intent_id"],
        print_job_id=row["job_id"],
        device_id=row["device_id"],
        accepted_at=parse_timestamp(row["accepted_at"]),
        state_version=1,
        replayed=replayed,
    )
