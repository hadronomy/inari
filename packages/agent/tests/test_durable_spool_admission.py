from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.engine import Connection

from inari.db.migrations import DatabaseMigrator
from inari.device_authority import AuthorityProof, canonical_digest
from inari.documents import (
    AdmissionDeadline,
    DurableAdmission,
    DocumentKind,
    DocumentWork,
    PosPrintOrigin,
    ReceiptImage,
    SubmissionContext,
)
from inari.documents.models import AdmissionGrantScope
from inari.documents.fingerprint import (
    DeviceWorkFingerprintInput,
    fingerprint_device_work,
)
from inari.spool.admission import (
    DurableSpoolAdmissionStore,
    ReconciliationReport,
    SpoolAdmissionError,
)
from inari.spool.authority import ActiveAuthorityGuard, SqlActiveAuthorityGuard
from inari.spool.filesystem import (
    ArtifactFileStore,
    SpoolCommitUncertainError,
    SpoolStorageError,
    StagedArtifact,
)
from inari.spool.keys import SpoolRootKeyService
from inari.runtime.store import RuntimeStore
from inari.spool.owner import SpoolOwner
from tests.support.device_authority import authority_proof


NOW = datetime(2026, 8, 28, 10, tzinfo=UTC)
ORIGINAL = b"receipt-image-content"
PERSISTENT_OVERHEAD = 16 * 1024 * 1024 + 64 * 1024


@dataclass(slots=True)
class DeterministicIds:
    values: list[str] = field(default_factory=list)

    def __call__(self) -> str:
        if not self.values:
            raise AssertionError("the test ID source ran out of IDs")
        return self.values.pop(0)


@dataclass(slots=True)
class RecordingAuthorityGuard:
    calls: list[tuple[AuthorityProof, datetime]] = field(default_factory=list)

    def check(
        self,
        connection: Connection,
        proof: AuthorityProof,
        *,
        now: datetime,
    ) -> None:
        assert connection.in_transaction()
        self.calls.append((proof, now))


@dataclass(slots=True)
class FakeRootSecretStore:
    values: dict[str, str] = field(default_factory=dict)

    def get_secret(self, key: str) -> str | None:
        return self.values.get(key)

    def set_secret(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete_secret(self, key: str) -> None:
        self.values.pop(key, None)


def _migrate(tmp_path: Path) -> Path:
    database_path = tmp_path / "agent.sqlite3"
    DatabaseMigrator(database_path).ensure_current()
    with sqlite3.connect(database_path) as connection:
        for device_id in ("device-1", "device-2", "device-3"):
            connection.execute(
                """
                INSERT INTO devices (
                    id, kind, driver_key, identity_transport, name,
                    connection_state, first_seen_at, last_seen_at, updated_at,
                    is_default, capabilities_json, metadata_json
                ) VALUES (?, 'printer', 'test', 'test', ?, 'ready', ?, ?, ?, 0, '{}', '{}')
                """,
                (
                    device_id,
                    device_id,
                    NOW.isoformat(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
        for device_id in ("device-1", "device-2", "device-3"):
            _seed_authority_graph(connection, _authority_proof_for(device_id))
    return database_path


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _authority_proof_for(
    device_id: str,
    *,
    proof_id: str = "proof-fixture",
) -> AuthorityProof:
    suffix = device_id.rsplit("-", 1)[-1]
    scope = {
        "database": "odoo",
        "organization_id": "org-1",
        "site_id": "site-1",
        "kind": "pos_configuration",
        "pos_configuration_id": "pos-1",
    }
    return replace(
        authority_proof(),
        proof_id=proof_id,
        authority_revision_id="authority-revision-1",
        authority_revision_digest=_digest("authority-revision-1"),
        scope_digest=canonical_digest(scope),
        observation_digest=_digest(f"{device_id}:observation"),
        binding_revision_id=f"binding-{suffix}",
        binding_revision_digest=_digest(f"{device_id}:binding"),
        driver_profile_id="driver-profile-1",
        driver_profile_digest=_digest("driver-profile-1"),
        matrix_row_id=f"matrix-row-{suffix}",
        matrix_row_digest=_digest(f"{device_id}:matrix"),
        test_evidence_id=f"device-test-{suffix}",
        test_evidence_digest=_digest(f"{device_id}:test"),
        device_id=device_id,
        device_identity_digest=_digest(f"{device_id}:identity"),
        issued_at=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=30),
    )


def _seed_authority_graph(
    connection: sqlite3.Connection,
    proof: AuthorityProof,
) -> None:
    timestamps = (
        proof.issued_at.isoformat().replace("+00:00", "Z"),
        proof.valid_until.isoformat().replace("+00:00", "Z"),
    )
    for key_id, purpose, marker in (
        ("authority-key", "authority_revision", 1),
        ("profile-key", "driver_profile", 2),
        ("matrix-key", "certification_matrix", 3),
        ("binding-key", "binding_revision", 4),
        ("test-key", "device_test_evidence", 5),
    ):
        connection.execute(
            """
            INSERT OR IGNORE INTO device_authority_signer_keys (
                key_id, purpose, public_key, state, not_before
            ) VALUES (?, ?, ?, 'active', ?)
            """,
            (key_id, purpose, bytes([marker]) * 32, timestamps[0]),
        )
    connection.execute(
        """
        INSERT OR IGNORE INTO device_authority_revisions (
            revision_id, revision_number, manifest_digest, effective_at,
            expires_at, revision_digest, signer_key_id, signature
        ) VALUES (?, ?, ?, ?, ?, ?, 'authority-key', ?)
        """,
        (
            proof.authority_revision_id,
            proof.authority_revision_number,
            b"m" * 32,
            *timestamps,
            bytes.fromhex(proof.authority_revision_digest),
            b"s" * 64,
        ),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO device_authority_state (
            singleton_id, current_revision_id, current_revision_number,
            status, updated_at
        ) VALUES (1, ?, ?, 'ready', ?)
        """,
        (
            proof.authority_revision_id,
            proof.authority_revision_number,
            timestamps[0],
        ),
    )
    capabilities = json.dumps(
        [
            {
                "capability_id": proof.capability_id,
                "contract_major": proof.contract_major,
                "max_copies": 1,
                "max_payload_bytes": 2 * 1024 * 1024,
                "media_type": proof.media_type,
                "operation": proof.operation,
                "options_digest": proof.options_digest,
                "output_evidence": "transport",
            }
        ],
        separators=(",", ":"),
        sort_keys=True,
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO device_driver_profiles (
            profile_id, version, driver_id, min_agent_version, capabilities,
            profile_digest, signer_key_id, signature, effective_at,
            expires_at, authority_revision_id
        ) VALUES (?, '1.0.0', 'test', '1.0.0', ?, ?, 'profile-key', ?, ?, ?, ?)
        """,
        (
            proof.driver_profile_id,
            capabilities,
            bytes.fromhex(proof.driver_profile_digest),
            b"s" * 64,
            *timestamps,
            proof.authority_revision_id,
        ),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO hardware_certification_matrix_rows (
            row_id, version, device_id, device_identity_digest, manufacturer,
            model, firmware_version, firmware_build, driver_id,
            driver_profile_digest, capability_id, platform_backend_id,
            connection, media_profile, operating_system, release_set_id,
            effective_at, expires_at, matrix_row_digest, signer_key_id,
            signature, authority_revision_id
        ) VALUES (?, 1, ?, ?, 'Test', 'Printer', '1', '1', 'test', ?, ?,
            'test', 'test', 'receipt-80mm', 'test', 'test', ?, ?, ?,
            'matrix-key', ?, ?)
        """,
        (
            proof.matrix_row_id,
            proof.device_id,
            bytes.fromhex(proof.device_identity_digest),
            bytes.fromhex(proof.driver_profile_digest),
            proof.capability_id,
            *timestamps,
            bytes.fromhex(proof.matrix_row_digest),
            b"s" * 64,
            proof.authority_revision_id,
        ),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO device_binding_revisions (
            revision_id, binding_id, revision_number, database,
            organization_id, site_id, scope_kind, pos_configuration_id,
            device_purpose, device_id, device_identity_digest, capability_id,
            driver_profile_digest, matrix_row_id, options_digest,
            binding_digest, signer_key_id, signature, effective_at,
            expires_at, authority_revision_id
        ) VALUES (?, ?, 1, 'odoo', 'org-1', 'site-1', 'pos_configuration',
            'pos-1', ?, ?, ?, ?, ?, ?, ?, ?, 'binding-key', ?, ?, ?, ?)
        """,
        (
            proof.binding_revision_id,
            "binding:" + proof.binding_revision_id,
            proof.purpose,
            proof.device_id,
            bytes.fromhex(proof.device_identity_digest),
            proof.capability_id,
            bytes.fromhex(proof.driver_profile_digest),
            proof.matrix_row_id,
            bytes.fromhex(proof.options_digest),
            bytes.fromhex(proof.binding_revision_digest),
            b"s" * 64,
            *timestamps,
            proof.authority_revision_id,
        ),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO device_test_evidence (
            evidence_id, revision_id, device_id, device_identity_digest,
            capability_id, driver_profile_digest, matrix_row_id,
            output_evidence, result, test_pattern_digest, tested_at,
            valid_until, evidence_digest, signer_key_id, signature,
            authority_revision_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'transport', 'passed', ?, ?, ?, ?,
            'test-key', ?, ?)
        """,
        (
            proof.test_evidence_id,
            proof.binding_revision_id,
            proof.device_id,
            bytes.fromhex(proof.device_identity_digest),
            proof.capability_id,
            bytes.fromhex(proof.driver_profile_digest),
            proof.matrix_row_id,
            b"t" * 32,
            *timestamps,
            bytes.fromhex(proof.test_evidence_digest),
            b"s" * 64,
            proof.authority_revision_id,
        ),
    )
    if proof.device_id == "device-1":
        connection.execute(
            """
            INSERT OR IGNORE INTO device_binding_authority_state (
                state_id, active_revision_id, active_test_evidence_id, database,
                organization_id, site_id, scope_kind, pos_configuration_id,
                device_purpose, status, updated_at
            ) VALUES ('receipt-binding', ?, ?, 'odoo', 'org-1', 'site-1',
                'pos_configuration', 'pos-1', ?, 'active', ?)
            """,
            (
                proof.binding_revision_id,
                proof.test_evidence_id,
                proof.purpose,
                timestamps[0],
            ),
        )


def _jpeg_like_work(
    *,
    idempotency_key: str = "idem-1",
    intent_id: str = "intent-1",
    origin_submission_key: str = "origin-1",
    device_id: str = "device-1",
    content: bytes = ORIGINAL,
) -> DocumentWork:
    return DocumentWork(
        idempotency_key=idempotency_key,
        context=SubmissionContext(
            contract_major=1,
            organization_id="org-1",
            site_id="site-1",
            paired_client_id="client-1",
            print_intent_id=intent_id,
            origin_submission_key=origin_submission_key,
            origin=PosPrintOrigin(
                database="odoo",
                pos_configuration_id="pos-1",
                pos_session_id="session-1",
                offline_order_id="order-1",
                server_order_id=None,
                document_kind="customer_receipt",
                content_revision="revision-1",
            ),
            binding_revision_id=f"binding-{device_id.rsplit('-', 1)[-1]}",
            device_id=device_id,
            actor_id="actor-1",
            authorization_digest="authorization-1",
            copy_ordinal=1,
        ),
        document=ReceiptImage(content=content),
    )


def _admission(work: DocumentWork) -> DurableAdmission:
    deadline = AdmissionDeadline(
        expires_at=NOW + timedelta(minutes=5), monotonic_deadline=305.0
    )
    return DurableAdmission(
        work=work,
        grant_scope=AdmissionGrantScope(
            organization_id=work.context.organization_id,
            site_id=work.context.site_id,
            database=work.context.origin.database,
            pos_configuration_id=work.context.origin.pos_configuration_id,
            paired_client_id=work.context.paired_client_id,
            actor_id=work.context.actor_id,
            device_id=work.context.device_id,
            binding_revision_id=work.context.binding_revision_id,
            operation=DocumentKind.RECEIPT_IMAGE,
            authorization_digest=work.context.authorization_digest,
        ),
        deadline=deadline,
        payload_fingerprint=fingerprint_device_work(
            DeviceWorkFingerprintInput(
                contract_major=work.context.contract_major,
                operation=work.operation,
                device_id=work.context.device_id,
                media_type="image/jpeg",
                document=work.document.content,
                options={},
                expires_at=deadline.expires_at,
            )
        ),
        media_type="image/jpeg",
        normalized_options=b"{}",
        authority_proof=_authority_proof_for(
            work.context.device_id,
            proof_id=f"proof:{work.context.print_intent_id}",
        ),
    )


def _store(
    database_path: Path,
    spool_path: Path,
    secret_store: FakeRootSecretStore,
    ids: DeterministicIds,
    *,
    device_queue_limit: int = 16,
    agent_queue_limit: int = 128,
    clock=lambda: NOW,
    files: ArtifactFileStore | None = None,
    authority_guard: ActiveAuthorityGuard | None = None,
) -> DurableSpoolAdmissionStore:
    return DurableSpoolAdmissionStore(
        store=RuntimeStore(database_path),
        files=files or ArtifactFileStore(spool_path),
        root_keys=SpoolRootKeyService(secret_store),
        owner=SpoolOwner(owner_id="agent-test", generation=1),
        authority_guard=authority_guard or RecordingAuthorityGuard(),
        clock=clock,
        id_factory=ids,
        device_queue_limit=device_queue_limit,
        agent_queue_limit=agent_queue_limit,
    )


class InterruptedPublicationStore(ArtifactFileStore):
    def commit(self, staged: StagedArtifact) -> str:
        os.link(staged.staging_path, self.objects_root / staged.storage_ref)
        raise SpoolCommitUncertainError(staged.storage_ref)


class LostPublicationStore(ArtifactFileStore):
    def commit(self, staged: StagedArtifact) -> str:
        storage_ref = super().commit(staged)
        self.delete(storage_ref)
        return storage_ref


class RevokingPublicationStore(ArtifactFileStore):
    def __init__(self, root: Path, database_path: Path, proof: AuthorityProof) -> None:
        super().__init__(root)
        self._database_path = database_path
        self._proof = proof

    def commit(self, staged: StagedArtifact) -> str:
        storage_ref = super().commit(staged)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                INSERT INTO device_authority_signer_keys (
                    key_id, purpose, public_key, state, not_before
                ) VALUES ('revocation-key', 'authority_revocation', ?, 'active', ?)
                """,
                (b"r" * 32, NOW.isoformat()),
            )
            connection.execute(
                """
                INSERT INTO device_authority_revocations (
                    revocation_id, authority_revision_id, signer_key_id,
                    subject_kind, subject_id, subject_digest, reason_code,
                    revoked_at, signature
                ) VALUES ('revocation-1', ?, 'revocation-key',
                    'binding_revision', ?, ?, 'policy', ?, ?)
                """,
                (
                    self._proof.authority_revision_id,
                    self._proof.binding_revision_id,
                    bytes.fromhex(self._proof.binding_revision_digest),
                    NOW.isoformat(),
                    b"s" * 64,
                ),
            )
        return storage_ref


class UnreadableArtifactStore(ArtifactFileStore):
    def read_bytes(self, storage_ref: str, *, max_bytes: int) -> bytes:
        del storage_ref, max_bytes
        raise SpoolStorageError


def _rows(database_path: Path, statement: str, parameters: tuple[object, ...] = ()):
    with sqlite3.connect(database_path) as connection:
        return connection.execute(statement, parameters).fetchall()


def _code(error: BaseException) -> str:
    return str(getattr(error, "code", ""))


@pytest.mark.anyio
async def test_accept_persists_artifact_job_and_event_as_one_barrier(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    ids = DeterministicIds(
        ["admission-1", "job-1", "reservation-1", "artifact-1", "nonce-1", "nonce-2"]
    )
    authority_guard = RecordingAuthorityGuard()
    store = _store(
        database_path,
        spool_path,
        secret_store,
        ids,
        authority_guard=authority_guard,
    )

    accepted = await store.accept(_admission(_jpeg_like_work()))

    assert accepted.print_job_id == "job-1"
    assert accepted.replayed is False
    assert authority_guard.calls == [
        (_admission(_jpeg_like_work()).authority_proof, NOW)
    ]
    assert _rows(
        database_path,
        "SELECT state, job_id FROM device_work_admissions",
    ) == [("accepted", "job-1")]
    assert _rows(
        database_path,
        "SELECT state, admission_id, authority_proof_id FROM public_print_jobs",
    ) == [("accepted", "admission-1", "proof:intent-1")]
    assert _rows(
        database_path,
        "SELECT proof_id, admission_id FROM device_work_authority_proofs",
    ) == [("proof:intent-1", "admission-1")]
    assert _rows(
        database_path,
        "SELECT subject_kind, subject_id FROM device_work_authority_proof_subjects ORDER BY subject_kind",
    ) == [
        ("authority_revision", "authority-revision-1"),
        ("binding_revision", "binding-1"),
        ("certification_matrix_row", "matrix-row-1"),
        ("device_test_evidence", "device-test-1"),
        ("driver_profile", "driver-profile-1"),
    ]
    assert _rows(
        database_path,
        "SELECT state_version, event_type FROM public_print_job_events",
    ) == [(1, "accepted")]
    assert _rows(
        database_path,
        "SELECT state, job_id FROM spool_artifacts",
    ) == [("committed", "job-1")]
    assert _rows(
        database_path,
        "SELECT state, job_id FROM spool_reservations",
    ) == [("committed", "job-1")]


@pytest.mark.anyio
async def test_sql_authority_guard_accepts_the_exact_current_graph(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    store = _store(
        database_path,
        tmp_path / "spool",
        FakeRootSecretStore(),
        DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
            ]
        ),
        authority_guard=SqlActiveAuthorityGuard(),
    )

    accepted = await store.accept(_admission(_jpeg_like_work()))

    assert accepted.print_job_id == "job-1"
    assert _rows(database_path, "SELECT state FROM public_print_jobs") == [
        ("accepted",)
    ]


@pytest.mark.anyio
async def test_binding_revocation_before_publication_aborts_without_a_job(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    admission = _admission(_jpeg_like_work())
    files = RevokingPublicationStore(
        tmp_path / "spool",
        database_path,
        admission.authority_proof,
    )
    store = _store(
        database_path,
        tmp_path / "spool",
        FakeRootSecretStore(),
        DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
            ]
        ),
        files=files,
        authority_guard=SqlActiveAuthorityGuard(),
    )

    with pytest.raises(SpoolAdmissionError) as error:
        await store.accept(admission)

    assert _code(error.value) == "capability_changed"
    assert _rows(database_path, "SELECT COUNT(*) FROM public_print_jobs") == [(0,)]
    assert _rows(
        database_path,
        "SELECT state, failure_code FROM device_work_admissions",
    ) == [("aborted", "capability_changed")]
    assert _rows(database_path, "SELECT COUNT(*) FROM spool_artifacts") == [(0,)]


@pytest.mark.anyio
async def test_exact_replay_returns_the_persisted_receipt_without_new_rows(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    ids = DeterministicIds(
        ["admission-1", "job-1", "reservation-1", "artifact-1", "nonce-1", "nonce-2"]
    )
    store = _store(database_path, spool_path, secret_store, ids)
    admission = _admission(_jpeg_like_work())

    first = await store.accept(admission)
    second = await store.accept(admission)

    assert second == first.__class__(
        print_intent_id=first.print_intent_id,
        print_job_id=first.print_job_id,
        device_id=first.device_id,
        accepted_at=first.accepted_at,
        state_version=first.state_version,
        replayed=True,
    )
    assert _rows(database_path, "SELECT COUNT(*) FROM public_print_jobs") == [(1,)]
    assert _rows(database_path, "SELECT COUNT(*) FROM spool_artifacts") == [(1,)]


@pytest.mark.anyio
async def test_concurrent_exact_replays_converge_to_one_job(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    store = _store(
        database_path,
        tmp_path / "spool",
        FakeRootSecretStore(),
        DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
            ]
        ),
    )
    admission = _admission(_jpeg_like_work())

    results = await asyncio.gather(store.accept(admission), store.accept(admission))

    assert {result.print_job_id for result in results} == {"job-1"}
    assert sorted(result.replayed for result in results) == [False, True]
    assert _rows(database_path, "SELECT COUNT(*) FROM public_print_jobs") == [(1,)]


@pytest.mark.anyio
async def test_same_origin_with_a_new_transport_key_replays_the_job(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    store = _store(
        database_path,
        tmp_path / "spool",
        FakeRootSecretStore(),
        DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
            ]
        ),
    )
    first = await store.accept(_admission(_jpeg_like_work()))

    replay = await store.accept(
        _admission(
            _jpeg_like_work(
                idempotency_key="idem-2",
                intent_id="intent-2",
            )
        )
    )

    assert replay.print_job_id == first.print_job_id
    assert replay.print_intent_id == first.print_intent_id
    assert replay.replayed is True


@pytest.mark.anyio
async def test_accepted_replay_ignores_current_deadline_and_disk_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_time = [NOW]
    database_path = _migrate(tmp_path)
    store = _store(
        database_path,
        tmp_path / "spool",
        FakeRootSecretStore(),
        DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
            ]
        ),
        clock=lambda: current_time[0],
    )
    admission = _admission(_jpeg_like_work())
    first = await store.accept(admission)
    current_time[0] = NOW + timedelta(days=1)
    low_disk = shutil.disk_usage(tmp_path).__class__(1, 1, 0)
    monkeypatch.setattr(
        "inari.spool.limits.shutil.disk_usage",
        lambda path: low_disk,
    )

    replay = await store.accept(admission)

    assert replay.print_job_id == first.print_job_id
    assert replay.replayed is True


@pytest.mark.anyio
async def test_durable_boundary_rejects_tampered_grant_or_fingerprint(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    store = _store(
        database_path,
        tmp_path / "spool",
        FakeRootSecretStore(),
        DeterministicIds([]),
    )
    admission = _admission(_jpeg_like_work())

    with pytest.raises(SpoolAdmissionError) as grant_error:
        await store.accept(
            replace(
                admission,
                grant_scope=replace(admission.grant_scope, actor_id="other-actor"),
            )
        )
    assert _code(grant_error.value) == "permission_denied"

    with pytest.raises(SpoolAdmissionError) as fingerprint_error:
        await store.accept(replace(admission, payload_fingerprint=b"x" * 32))
    assert _code(fingerprint_error.value) == "payload_invalid"
    assert _rows(database_path, "SELECT COUNT(*) FROM device_work_admissions") == [(0,)]


@pytest.mark.anyio
async def test_idempotency_and_origin_conflicts_are_rejected(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    ids = DeterministicIds(
        [
            "admission-1",
            "job-1",
            "reservation-1",
            "artifact-1",
            "nonce-1",
            "nonce-2",
            "admission-2",
            "job-2",
        ]
    )
    store = _store(database_path, spool_path, secret_store, ids)
    await store.accept(_admission(_jpeg_like_work()))

    with pytest.raises(SpoolAdmissionError) as idempotency_error:
        await store.accept(_admission(_jpeg_like_work(content=b"different-content")))
    assert _code(idempotency_error.value) == "idempotency_conflict"

    with pytest.raises(SpoolAdmissionError) as origin_error:
        await store.accept(
            _admission(
                _jpeg_like_work(
                    idempotency_key="idem-2",
                    intent_id="intent-2",
                    origin_submission_key="origin-1",
                    content=b"different-origin-content",
                )
            )
        )
    assert _code(origin_error.value) == "request_conflict"

    with pytest.raises(SpoolAdmissionError) as changed_device_error:
        await store.accept(
            _admission(
                _jpeg_like_work(
                    idempotency_key="idem-3",
                    intent_id="intent-3",
                    origin_submission_key="origin-1",
                    device_id="device-2",
                )
            )
        )
    assert _code(changed_device_error.value) == "request_conflict"


@pytest.mark.anyio
async def test_device_and_agent_queue_limits_reject_new_work(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    ids = DeterministicIds(
        [
            "admission-1",
            "job-1",
            "reservation-1",
            "artifact-1",
            "nonce-1",
            "nonce-2",
            "admission-2",
            "job-2",
            "reservation-2",
            "artifact-2",
            "nonce-3",
            "nonce-4",
            "admission-3",
            "job-3",
        ]
    )
    store = _store(
        database_path,
        spool_path,
        secret_store,
        ids,
        device_queue_limit=1,
        agent_queue_limit=2,
    )
    await store.accept(_admission(_jpeg_like_work()))

    with pytest.raises(SpoolAdmissionError) as device_error:
        await store.accept(
            _admission(
                _jpeg_like_work(
                    idempotency_key="idem-device-2",
                    intent_id="intent-device-2",
                    origin_submission_key="origin-device-2",
                )
            )
        )
    assert _code(device_error.value) == "queue_full"

    await store.accept(
        _admission(
            _jpeg_like_work(
                idempotency_key="idem-agent-2",
                intent_id="intent-agent-2",
                origin_submission_key="origin-agent-2",
                device_id="device-2",
            )
        )
    )
    with pytest.raises(SpoolAdmissionError) as agent_error:
        await store.accept(
            _admission(
                _jpeg_like_work(
                    idempotency_key="idem-agent-3",
                    intent_id="intent-agent-3",
                    origin_submission_key="origin-agent-3",
                    device_id="device-3",
                )
            )
        )
    assert _code(agent_error.value) == "queue_full"


@pytest.mark.anyio
async def test_receipt_reservation_has_the_required_persistent_footprint(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    ids = DeterministicIds(
        ["admission-1", "job-1", "reservation-1", "artifact-1", "nonce-1", "nonce-2"]
    )
    store = _store(database_path, spool_path, secret_store, ids)

    await store.accept(_admission(_jpeg_like_work()))

    assert _rows(
        database_path,
        "SELECT original_bytes, persistent_bytes, temporary_bytes FROM spool_reservations",
    ) == [(len(ORIGINAL), len(ORIGINAL) + PERSISTENT_OVERHEAD, 0)]


@pytest.mark.anyio
async def test_accept_provisions_a_root_key_and_durable_nonce_rows(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    ids = DeterministicIds(
        ["admission-1", "job-1", "reservation-1", "artifact-1", "nonce-1", "nonce-2"]
    )
    store = _store(database_path, spool_path, secret_store, ids)

    await store.accept(_admission(_jpeg_like_work()))

    assert secret_store.get_secret("inari/device-spool/root-key/v1")
    assert _rows(
        database_path,
        "SELECT root_version, state FROM spool_root_keys",
    ) == [(1, "current")]
    nonces = _rows(
        database_path,
        "SELECT domain, purpose, nonce FROM spool_nonce_reservations ORDER BY id",
    )
    assert [(domain, purpose) for domain, purpose, _ in nonces] == [
        ("root_wrap", "data_key_wrap"),
        ("artifact", "original"),
    ]
    assert len({nonce for _, _, nonce in nonces}) == len(nonces)


@pytest.mark.anyio
async def test_encrypted_artifact_has_no_plaintext_and_reconcile_keeps_promoted_work(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    ids = DeterministicIds(
        ["admission-1", "job-1", "reservation-1", "artifact-1", "nonce-1", "nonce-2"]
    )
    artifact_store = ArtifactFileStore(spool_path)
    store = _store(database_path, spool_path, secret_store, ids)
    accepted = await store.accept(_admission(_jpeg_like_work()))
    storage_ref = _rows(
        database_path,
        "SELECT storage_ref FROM spool_artifacts WHERE job_id = ?",
        (accepted.print_job_id,),
    )[0][0]

    encrypted = artifact_store.read_bytes(storage_ref, max_bytes=2 * 1024 * 1024)
    assert ORIGINAL not in encrypted
    assert all(
        path.read_bytes() != ORIGINAL for path in (spool_path / "objects").iterdir()
    )
    assert not list((spool_path / "staging").iterdir())

    orphan = artifact_store.stage_bytes(b"staged-encrypted-bytes", max_bytes=1024)
    restarted = _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds([]),
    )
    report = await restarted.reconcile()

    assert isinstance(report, ReconciliationReport)
    assert not orphan.staging_path.exists()
    assert artifact_store.exists(storage_ref)
    assert _rows(database_path, "SELECT COUNT(*) FROM public_print_jobs") == [(1,)]


@pytest.mark.anyio
async def test_reconcile_completes_an_uncertain_publication(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    interrupted_files = InterruptedPublicationStore(spool_path)
    store = _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
            ]
        ),
        files=interrupted_files,
    )

    admission = _admission(_jpeg_like_work())
    with pytest.raises(SpoolAdmissionError) as uncertain_error:
        await store.accept(admission)
    assert _code(uncertain_error.value) == "recovery_uncertain"
    assert _rows(database_path, "SELECT state FROM spool_reservations") == [("held",)]
    assert len(list(interrupted_files.staging_root.iterdir())) == 1
    assert len(list(interrupted_files.objects_root.iterdir())) == 1

    recovery_guard = RecordingAuthorityGuard()
    restarted = _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds([]),
        authority_guard=recovery_guard,
    )
    report = await restarted.reconcile()

    assert report.finalized_admissions == 1
    assert recovery_guard.calls == [(admission.authority_proof, NOW)]
    assert not list(interrupted_files.staging_root.iterdir())
    assert _rows(database_path, "SELECT state FROM public_print_jobs") == [
        ("accepted",)
    ]


@pytest.mark.anyio
async def test_retry_replaces_a_missing_staged_artifact_and_reservation(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    store = _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
            ]
        ),
        files=LostPublicationStore(spool_path),
    )
    admission = _admission(_jpeg_like_work())

    with pytest.raises(SpoolAdmissionError):
        await store.accept(admission)

    restarted = _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds(
            [
                "reservation-2",
                "artifact-2",
                "nonce-3",
            ]
        ),
    )
    accepted = await restarted.accept(admission)

    assert accepted.print_job_id == "job-1"
    assert _rows(
        database_path,
        "SELECT id, state, released_at IS NOT NULL FROM spool_reservations ORDER BY id",
    ) == [
        ("reservation-1", "released", 1),
        ("reservation-2", "committed", 0),
    ]
    assert _rows(database_path, "SELECT id, state FROM spool_artifacts") == [
        ("artifact-2", "committed")
    ]


@pytest.mark.anyio
async def test_reconcile_isolates_corrupt_staging_content(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    interrupted_files = InterruptedPublicationStore(spool_path)
    store = _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
                "admission-2",
                "job-2",
                "reservation-2",
                "artifact-2",
                "nonce-3",
                "nonce-4",
            ]
        ),
        files=interrupted_files,
    )
    first = _admission(_jpeg_like_work())
    second = _admission(
        _jpeg_like_work(
            idempotency_key="idem-2",
            intent_id="intent-2",
            origin_submission_key="origin-2",
            device_id="device-2",
        )
    )

    with pytest.raises(SpoolAdmissionError):
        await store.accept(first)
    with pytest.raises(SpoolAdmissionError):
        await store.accept(second)

    first_ref = _rows(
        database_path,
        "SELECT storage_ref FROM spool_artifacts WHERE admission_id = 'admission-1'",
    )[0][0]
    (interrupted_files.objects_root / first_ref).write_bytes(b"corrupt")

    report = await _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds([]),
    ).reconcile()

    assert report.finalized_admissions == 1
    assert report.released_admissions == 1
    assert _rows(
        database_path,
        "SELECT id, state FROM public_print_jobs",
    ) == [("job-2", "accepted")]
    assert _rows(
        database_path,
        "SELECT admission_id, state FROM spool_artifacts",
    ) == [("admission-2", "committed")]
    assert (
        _rows(
            database_path,
            "SELECT admission_id FROM spool_admission_keys",
        )
        == []
    )
    assert _rows(
        database_path,
        "SELECT state, failure_code, failed_at IS NOT NULL FROM device_work_admissions ORDER BY id",
    ) == [
        ("aborted", "recovery_uncertain", 1),
        ("accepted", None, 0),
    ]
    assert _rows(
        database_path,
        "SELECT COUNT(*) FROM spool_nonce_reservations",
    ) == [(4,)]
    assert len(list(interrupted_files.objects_root.iterdir())) == 1
    assert not list(interrupted_files.staging_root.iterdir())

    with pytest.raises(SpoolAdmissionError) as replay_error:
        await store.accept(first)
    assert _code(replay_error.value) == "recovery_uncertain"


@pytest.mark.anyio
async def test_transient_artifact_read_failure_keeps_recoverable_state(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    admission = _admission(_jpeg_like_work())
    unreadable_files = UnreadableArtifactStore(spool_path)
    store = _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
            ]
        ),
        files=unreadable_files,
    )

    with pytest.raises(SpoolAdmissionError) as read_error:
        await store.accept(admission)
    assert _code(read_error.value) == "recovery_uncertain"
    assert _rows(database_path, "SELECT state FROM device_work_admissions") == [
        ("staging",)
    ]
    assert _rows(database_path, "SELECT state FROM spool_artifacts") == [("staging",)]
    assert _rows(database_path, "SELECT state FROM spool_reservations") == [("held",)]

    report = await store.reconcile()
    assert report.released_admissions == 0
    assert _rows(database_path, "SELECT state FROM spool_reservations") == [("held",)]

    accepted = await _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds([]),
    ).accept(admission)
    assert accepted.print_job_id == "job-1"


@pytest.mark.anyio
async def test_reconcile_fails_only_the_accepted_job_with_missing_content(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    spool_path = tmp_path / "spool"
    secret_store = FakeRootSecretStore()
    files = ArtifactFileStore(spool_path)
    store = _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds(
            [
                "admission-1",
                "job-1",
                "reservation-1",
                "artifact-1",
                "nonce-1",
                "nonce-2",
                "admission-2",
                "job-2",
                "reservation-2",
                "artifact-2",
                "nonce-3",
                "nonce-4",
            ]
        ),
        files=files,
    )
    await store.accept(_admission(_jpeg_like_work()))
    await store.accept(
        _admission(
            _jpeg_like_work(
                idempotency_key="idem-2",
                intent_id="intent-2",
                origin_submission_key="origin-2",
                device_id="device-2",
            )
        )
    )
    missing_ref = _rows(
        database_path,
        "SELECT storage_ref FROM spool_artifacts WHERE job_id = 'job-1'",
    )[0][0]
    files.delete(missing_ref)

    restarted = _store(
        database_path,
        spool_path,
        secret_store,
        DeterministicIds([]),
    )
    report = await restarted.reconcile()

    assert report.failed_print_jobs == 1
    assert _rows(
        database_path,
        "SELECT id, state, state_version, retryable, error_code, message_key FROM public_print_jobs ORDER BY id",
    ) == [
        (
            "job-1",
            "failed",
            2,
            0,
            "service_unavailable",
            "print.service_unavailable",
        ),
        ("job-2", "accepted", 1, 0, None, None),
    ]
    assert _rows(
        database_path,
        "SELECT job_id, state_version, event_type FROM public_print_job_events ORDER BY sequence",
    ) == [
        ("job-1", 1, "accepted"),
        ("job-2", 1, "accepted"),
        ("job-1", 2, "failed"),
    ]
    assert _rows(
        database_path,
        "SELECT job_id, state, retention_policy, deleted_at IS NOT NULL FROM spool_artifacts ORDER BY job_id",
    ) == [
        ("job-1", "deleted", "integrity_immediate", 1),
        ("job-2", "committed", "active", 0),
    ]
    assert _rows(
        database_path,
        "SELECT job_id, length(wrapped_key), key_deleted_at IS NOT NULL FROM spool_job_keys ORDER BY job_id",
    ) == [("job-1", 0, 1), ("job-2", 48, 0)]
    assert _rows(
        database_path,
        "SELECT job_id, state FROM spool_reservations ORDER BY job_id",
    ) == [("job-1", "released"), ("job-2", "committed")]

    second_report = await restarted.reconcile()
    assert second_report.failed_print_jobs == 0
    assert _rows(
        database_path,
        "SELECT COUNT(*) FROM public_print_job_events WHERE job_id = 'job-1'",
    ) == [(2,)]
