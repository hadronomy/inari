from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect

from inari.db.migrations import DatabaseMigrator
from inari.db.schema import create_database_engine, metadata


NOW = "2026-08-28T10:00:00Z"
LATER = "2026-08-28T10:05:00Z"
DIGESTS = {
    "manifest": b"m" * 32,
    "revision": b"r" * 32,
    "profile": b"p" * 32,
    "matrix": b"x" * 32,
    "binding": b"b" * 32,
    "test": b"t" * 32,
    "identity": b"i" * 32,
    "options": b"o" * 32,
}


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "runtime.sqlite3"
    DatabaseMigrator(path).ensure_current()
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO devices (
                id, kind, driver_key, identity_transport, name,
                connection_state, first_seen_at, last_seen_at, updated_at,
                is_default, capabilities_json, metadata_json
            ) VALUES ('device_1', 'printer', 'driver.test', 'spooler', 'Printer',
                'ready', ?, ?, ?, 0, '{}', '{}')
            """,
            (NOW, NOW, NOW),
        )
    return path


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _insert_key(
    connection: sqlite3.Connection,
    key_id: str,
    purpose: str,
    marker: int,
) -> None:
    connection.execute(
        """
        INSERT INTO device_authority_signer_keys (
            key_id, purpose, public_key, state, not_before
        ) VALUES (?, ?, ?, 'active', ?)
        """,
        (key_id, purpose, bytes([marker]) * 32, NOW),
    )


def _insert_revision(connection: sqlite3.Connection) -> None:
    _insert_key(connection, "key_authority", "authority_revision", 1)
    connection.execute(
        """
        INSERT INTO device_authority_revisions (
            revision_id, revision_number, manifest_digest, effective_at,
            expires_at, revision_digest, signer_key_id, signature
        ) VALUES ('revision_1', 1, ?, ?, ?, ?, 'key_authority', ?)
        """,
        (
            DIGESTS["manifest"],
            NOW,
            LATER,
            DIGESTS["revision"],
            b"s" * 64,
        ),
    )


def _insert_profile(
    connection: sqlite3.Connection,
    *,
    profile_id: str = "profile_1",
    profile_digest: bytes = DIGESTS["profile"],
    driver_id: str = "driver.test",
) -> None:
    if "key_profile" not in {
        row[0]
        for row in connection.execute("SELECT key_id FROM device_authority_signer_keys")
    }:
        _insert_key(connection, "key_profile", "driver_profile", 2)
    connection.execute(
        """
        INSERT INTO device_driver_profiles (
            profile_id, version, driver_id, min_agent_version, capabilities,
            profile_digest, signer_key_id, signature, effective_at,
            expires_at, authority_revision_id
        ) VALUES (?, '1.0', ?, '1.0.0', ?, ?, 'key_profile', ?, ?, ?, 'revision_1')
        """,
        (
            profile_id,
            driver_id,
            '[{"capability_id":"cap","operation":"print","media_type":"text/plain","contract_major":1,"output_evidence":"device","max_payload_bytes":1024,"max_copies":1,"options_digest":"'
            + "0" * 64
            + '"}]',
            profile_digest,
            b"s" * 64,
            NOW,
            LATER,
        ),
    )


def _insert_matrix(
    connection: sqlite3.Connection,
    *,
    row_id: str = "matrix_1",
    device_id: str = "device_1",
    driver_id: str = "driver.test",
    profile_digest: bytes = DIGESTS["profile"],
    capability_id: str = "cap",
) -> None:
    _insert_key_if_missing(connection, "key_matrix", "certification_matrix", 3)
    connection.execute(
        """
        INSERT INTO hardware_certification_matrix_rows (
            row_id, version, device_id, device_identity_digest, manufacturer,
            model, firmware_version, firmware_build, driver_id,
            driver_profile_digest, capability_id, platform_backend_id,
            connection, media_profile, operating_system, release_set_id,
            effective_at, expires_at, matrix_row_digest, signer_key_id,
            signature, authority_revision_id
        ) VALUES (?, 1, ?, ?, 'Maker', 'Model', '1', 'build', ?,
            ?, ?, 'backend', 'spooler', 'receipt', 'Linux', 'release',
            ?, ?, ?, 'key_matrix', ?, 'revision_1')
        """,
        (
            row_id,
            device_id,
            DIGESTS["identity"],
            driver_id,
            profile_digest,
            capability_id,
            NOW,
            LATER,
            DIGESTS["matrix"],
            b"s" * 64,
        ),
    )


def _insert_binding(
    connection: sqlite3.Connection,
    *,
    revision_id: str = "binding_revision_1",
    binding_id: str = "binding_1",
    database: str = "db",
    scope_kind: str = "site",
    pos_configuration_id: str | None = None,
    device_purpose: str = "receipt",
    device_id: str = "device_1",
    matrix_row_id: str = "matrix_1",
) -> None:
    _insert_key_if_missing(connection, "key_binding", "binding_revision", 4)
    connection.execute(
        """
        INSERT INTO device_binding_revisions (
            revision_id, binding_id, revision_number, database,
            organization_id, site_id, scope_kind, pos_configuration_id,
            device_purpose, device_id, device_identity_digest, capability_id,
            driver_profile_digest, matrix_row_id, options_digest,
            binding_digest, signer_key_id, signature, effective_at,
            expires_at, authority_revision_id
        ) VALUES (?, ?, 1, ?, 'org', 'site', ?, ?, ?, ?, ?, 'cap',
            ?, ?, ?, ?, 'key_binding', ?, ?, ?, 'revision_1')
        """,
        (
            revision_id,
            binding_id,
            database,
            scope_kind,
            pos_configuration_id,
            device_purpose,
            device_id,
            DIGESTS["identity"],
            DIGESTS["profile"],
            matrix_row_id,
            DIGESTS["options"],
            DIGESTS["binding"],
            b"s" * 64,
            NOW,
            LATER,
        ),
    )


def _insert_test(
    connection: sqlite3.Connection,
    *,
    evidence_id: str = "test_1",
    revision_id: str = "binding_revision_1",
    result: str = "passed",
) -> None:
    _insert_key_if_missing(connection, "key_test", "device_test_evidence", 5)
    connection.execute(
        """
        INSERT INTO device_test_evidence (
            evidence_id, revision_id, device_id, device_identity_digest,
            capability_id, driver_profile_digest, matrix_row_id,
            output_evidence, result, test_pattern_digest, tested_at,
            valid_until, evidence_digest, signer_key_id, signature,
            authority_revision_id
        ) VALUES (?, ?, 'device_1', ?, 'cap', ?, 'matrix_1', 'device',
            ?, ?, ?, ?, ?, 'key_test', ?, 'revision_1')
        """,
        (
            evidence_id,
            revision_id,
            DIGESTS["identity"],
            DIGESTS["profile"],
            result,
            DIGESTS["test"],
            NOW,
            LATER,
            DIGESTS["test"],
            b"s" * 64,
        ),
    )


def _insert_key_if_missing(
    connection: sqlite3.Connection,
    key_id: str,
    purpose: str,
    marker: int,
) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM device_authority_signer_keys WHERE key_id = ?", (key_id,)
        ).fetchone()
        is None
    ):
        _insert_key(connection, key_id, purpose, marker)


def _insert_authority_graph(connection: sqlite3.Connection) -> None:
    _insert_revision(connection)
    _insert_profile(connection)
    _insert_matrix(connection)
    _insert_binding(connection)
    _insert_test(connection)


def _insert_admission(
    connection: sqlite3.Connection,
    *,
    admission_id: str = "admission_1",
    planned_job_id: str = "job_1",
    state: str = "staging",
) -> None:
    connection.execute(
        """
        INSERT INTO device_work_admissions (
            id, planned_job_id, database, scope_kind, organization_id, site_id,
            pos_configuration_id, paired_client_id, idempotency_key, fingerprint,
            state, job_id, intent_id, device_id, deadline_at, original_size_bytes,
            created_at, updated_at, accepted_at, failed_at, failure_code,
            idempotency_expires_at, content_expires_at, actor_id,
            binding_revision_id, authorization_digest, operation, media_type,
            normalized_options_digest, grant_scope_digest, origin_submission_key,
            origin_kind, origin_json, contract_major, copy_ordinal
        ) VALUES (?, ?, 'db', 'device_manager', 'org', 'site', NULL, NULL,
            ?, ?, ?, NULL, ?, 'device_1', ?, 10, ?, ?, NULL, NULL, NULL,
            ?, ?, 'actor', 'binding_revision_1', ?, 'print', 'text/plain',
            ?, ?, 'origin', 'pos', '{}', 1, 0)
        """,
        (
            admission_id,
            planned_job_id,
            "idempotency_" + admission_id,
            b"f" * 32,
            state,
            "intent_" + admission_id,
            LATER,
            NOW,
            NOW,
            LATER,
            LATER,
            b"a" * 32,
            DIGESTS["options"],
            b"g" * 32,
        ),
    )


def _insert_proof(
    connection: sqlite3.Connection,
    *,
    proof_id: str = "proof_1",
    admission_id: str = "admission_1",
) -> None:
    connection.execute(
        """
        INSERT INTO device_work_authority_proofs (
            proof_id, admission_id, authority_revision_id,
            authority_revision_number, authority_revision_digest,
            snapshot_digest, graph_digest, scope_digest, observation_digest,
            binding_revision_id, binding_revision_digest, driver_profile_id,
            driver_profile_digest, matrix_row_id, matrix_row_digest,
            test_evidence_id, test_evidence_digest, device_id,
            device_identity_digest, capability_id, device_purpose, operation,
            media_type, contract_major, options_digest, issued_at, valid_until
        ) VALUES (?, ?, 'revision_1', 1, ?, ?, ?, ?, ?, 'binding_revision_1',
            ?, 'profile_1', ?, 'matrix_1', ?, 'test_1', ?, 'device_1',
            ?, 'cap', 'receipt', 'print', 'text/plain', 1, ?, ?, ?)
        """,
        (
            proof_id,
            admission_id,
            DIGESTS["revision"],
            b"1" * 32,
            b"2" * 32,
            b"3" * 32,
            b"4" * 32,
            DIGESTS["binding"],
            DIGESTS["profile"],
            DIGESTS["matrix"],
            DIGESTS["test"],
            DIGESTS["identity"],
            DIGESTS["options"],
            NOW,
            LATER,
        ),
    )


def _insert_public_job(
    connection: sqlite3.Connection,
    *,
    job_id: str = "job_1",
    admission_id: str = "admission_1",
    proof_id: str | None = "proof_1",
    state: str = "accepted",
) -> None:
    connection.execute(
        """
        INSERT INTO public_print_jobs (
            id, admission_id, authority_proof_id, intent_id, device_id, scope_kind,
            organization_id, site_id, pos_configuration_id, paired_client_id,
            origin_kind, origin_json, managed_work_id, state, state_version,
            accepted_at, started_at, terminal_at, expires_at, retryable,
            error_code, message_key, confirmation_evidence, contract_version
        ) VALUES (?, ?, ?, 'intent_admission_1', 'device_1', 'device_manager',
            'org', 'site', NULL, NULL, 'pos', '{}', NULL, ?, 1, ?, NULL,
            NULL, ?, 0, NULL, NULL, NULL, 'v1')
        """,
        (job_id, admission_id, proof_id, state, NOW, LATER),
    )


def test_signer_purposes_and_public_key_uniqueness(tmp_path: Path) -> None:
    path = _database(tmp_path)
    with _connect(path) as connection:
        _insert_key(connection, "key_one", "authority_revision", 20)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_key(connection, "key_two", "driver_profile", 20)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO device_authority_signer_keys (
                    key_id, purpose, public_key, state, not_before
                ) VALUES ('bad', 'hardware_certification', ?, 'active', ?)
                """,
                (b"z" * 32, NOW),
            )


def test_matrix_and_binding_graphs_are_exact_and_binding_is_scope_only(
    tmp_path: Path,
) -> None:
    path = _database(tmp_path)
    with _connect(path) as connection:
        _insert_authority_graph(connection)
        matrix_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(hardware_certification_matrix_rows)"
            )
        }
        assert {
            "row_id",
            "version",
            "device_id",
            "device_identity_digest",
            "manufacturer",
            "model",
            "firmware_version",
            "firmware_build",
            "driver_id",
            "driver_profile_digest",
            "capability_id",
            "platform_backend_id",
            "connection",
            "media_profile",
            "operating_system",
            "release_set_id",
            "effective_at",
            "expires_at",
            "matrix_row_digest",
            "signature",
            "authority_revision_id",
        } <= matrix_columns
        binding_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(device_binding_revisions)")
        }
        assert "database" in binding_columns
        assert "paired_client_id" not in binding_columns
        assert "test_evidence_id" not in binding_columns

        with pytest.raises(sqlite3.IntegrityError):
            _insert_matrix(
                connection,
                row_id="matrix_bad",
                driver_id="different.driver",
            )


def test_pointer_graph_and_scope_uniqueness_normalize_null_pos(
    tmp_path: Path,
) -> None:
    path = _database(tmp_path)
    with _connect(path) as connection:
        _insert_authority_graph(connection)
        connection.execute(
            """
            INSERT INTO device_binding_authority_state (
                state_id, active_revision_id, active_test_evidence_id, database,
                organization_id, site_id, scope_kind, pos_configuration_id,
                device_purpose, status, updated_at
            ) VALUES ('state_1', 'binding_revision_1', 'test_1', 'db', 'org',
                'site', 'site', NULL, 'receipt', 'active', ?)
            """,
            (NOW,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO device_binding_authority_state (
                    state_id, active_revision_id, active_test_evidence_id, database,
                    organization_id, site_id, scope_kind, pos_configuration_id,
                    device_purpose, status, updated_at
                ) VALUES ('state_2', 'binding_revision_1', 'test_1', 'db', 'org',
                    'site', 'site', NULL, 'receipt', 'active', ?)
                """,
                (NOW,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO device_binding_authority_state (
                    state_id, active_revision_id, active_test_evidence_id, database,
                    organization_id, site_id, scope_kind, pos_configuration_id,
                    device_purpose, status, updated_at
                ) VALUES ('state_bad', 'binding_revision_1', 'missing', 'db',
                    'org', 'site', 'site', NULL, 'receipt', 'active', ?)
                """,
                (NOW,),
            )


def test_device_test_result_enum_is_exact(tmp_path: Path) -> None:
    path = _database(tmp_path)
    with _connect(path) as connection:
        _insert_authority_graph(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_test(connection, evidence_id="bad", result="failed")


def test_revocation_subject_kind_and_link_are_exact(tmp_path: Path) -> None:
    path = _database(tmp_path)
    with _connect(path) as connection:
        _insert_authority_graph(connection)
        _insert_key(connection, "key_revocation", "authority_revocation", 6)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO device_authority_revocations (
                    revocation_id, authority_revision_id, signer_key_id, subject_kind,
                    subject_id, subject_digest, reason_code, revoked_at, signature
                ) VALUES ('bad_kind', 'revision_1', 'key_revocation', 'hardware_certification',
                    'revision_1', ?, 'manual', ?, ?)
                """,
                (DIGESTS["revision"], NOW, b"s" * 64),
            )
        connection.execute(
            """
            INSERT INTO device_authority_revocations (
                revocation_id, authority_revision_id, signer_key_id, subject_kind,
                subject_id, subject_digest, reason_code, revoked_at, signature
            ) VALUES ('revocation_1', 'revision_1', 'key_revocation', 'authority_revision',
                'revision_1', ?, 'manual', ?, ?)
            """,
            (DIGESTS["revision"], NOW, b"s" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO device_authority_revocations (
                    revocation_id, authority_revision_id, signer_key_id, subject_kind,
                    subject_id, subject_digest, reason_code, revoked_at, signature
                ) VALUES ('bad_link', 'revision_1', 'key_revocation', 'authority_revision',
                    'revision_1', ?, 'manual', ?, ?)
                """,
                (b"q" * 32, NOW, b"s" * 64),
            )


def test_accepted_work_requires_an_exact_durable_proof(tmp_path: Path) -> None:
    path = _database(tmp_path)
    with _connect(path) as connection:
        _insert_authority_graph(connection)
        _insert_admission(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE device_work_admissions SET state = 'accepted' WHERE id = 'admission_1'"
            )

        _insert_proof(connection)
        connection.execute(
            "UPDATE device_work_admissions SET state = 'finalizing' WHERE id = 'admission_1'"
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_public_job(connection, proof_id=None)
        _insert_public_job(connection)
        connection.execute(
            """
            UPDATE device_work_admissions
            SET state = 'accepted', job_id = 'job_1', accepted_at = ?
            WHERE id = 'admission_1'
            """,
            (NOW,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE device_work_authority_proofs SET graph_digest = ? WHERE proof_id = 'proof_1'",
                (b"9" * 32,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM device_work_authority_proofs WHERE proof_id = 'proof_1'"
            )


def test_trigger_and_index_metadata_parity(tmp_path: Path) -> None:
    path = _database(tmp_path)
    with _connect(path) as connection:
        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert "ck_device_work_authority_proofs_graph" in trigger_names
        assert "ck_device_binding_authority_state_graph" in trigger_names
        index_sql = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index'
              AND name = 'uq_device_binding_authority_state_active_scope_purpose'
            """
        ).fetchone()[0]
        assert "coalesce(pos_configuration_id, '')" in index_sql
    engine = create_database_engine(path)
    try:
        assert "device_work_authority_proofs" in metadata.tables
        assert "device_work_authority_proof_subjects" in metadata.tables
        assert "authority_proof_id" in metadata.tables["public_print_jobs"].columns
        assert {
            index.name
            for index in metadata.tables["device_binding_authority_state"].indexes
        } >= {
            "uq_device_binding_authority_state_active_scope_purpose",
        }
        assert inspect(engine).has_table("device_work_authority_proofs")
    finally:
        engine.dispose()


def test_upgrade_and_downgrade_restore_0006_shape(tmp_path: Path) -> None:
    path = _database(tmp_path)
    migrator = DatabaseMigrator(path)
    config = migrator._build_alembic_config()
    command.downgrade(config, "20260828_0006")
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("20260828_0006",)
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'device_work_authority_proofs'"
        ).fetchone()
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(public_print_jobs)")
        }
        assert "authority_proof_id" not in columns
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO device_work_admissions (
                    id, planned_job_id, database, scope_kind, organization_id, site_id,
                    pos_configuration_id, paired_client_id, idempotency_key,
                    fingerprint, state, job_id, intent_id, device_id, deadline_at,
                    original_size_bytes, created_at, updated_at, accepted_at,
                    failed_at, failure_code, idempotency_expires_at,
                    content_expires_at, actor_id, binding_revision_id,
                    authorization_digest, operation, media_type,
                    normalized_options_digest, grant_scope_digest,
                    origin_submission_key, origin_kind, origin_json,
                    contract_major, copy_ordinal
                ) VALUES ('a', 'j', 'db', 'device_manager', 'org', 'site', NULL,
                    NULL, 'id', ?, 'aborted', NULL, 'i', 'device_1', ?, 1, ?,
                    ?, NULL, ?, 'capability_changed', ?, ?, 'actor', 'binding',
                    ?, 'print', 'text/plain', ?, ?, 'origin', 'pos', '{}', 1, 0)
                """,
                (
                    b"f" * 32,
                    LATER,
                    NOW,
                    NOW,
                    LATER,
                    LATER,
                    LATER,
                    b"a" * 32,
                    b"o" * 32,
                    b"g" * 32,
                ),
            )
