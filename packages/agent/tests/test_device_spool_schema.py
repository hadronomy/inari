from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from inari.db.migrations import DatabaseMigrator
from inari.db.schema import MANAGED_TABLE_NAMES, create_database_engine, metadata


NOW = "2026-08-27T10:00:00Z"
LATER = "2026-08-27T10:05:00Z"
RETENTION = "2026-11-25T10:00:00Z"
AUTHORITY_DIGESTS = {
    "revision": b"r" * 32,
    "profile": b"p" * 32,
    "matrix": b"m" * 32,
    "binding": b"b" * 32,
    "evidence": b"e" * 32,
    "identity": b"i" * 32,
    "options": b"b" * 32,
}


def _migrate(tmp_path: Path) -> Path:
    path = tmp_path / "runtime.sqlite3"
    DatabaseMigrator(path).ensure_current()
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO devices (
                id, kind, driver_key, identity_transport, name,
                identity_os_instance_id,
                connection_state, first_seen_at, last_seen_at, updated_at,
                is_default, capabilities_json, metadata_json
            ) VALUES (
                'device_1', 'printer', 'test', 'spooler', 'Printer', 'printer-test',
                'ready', ?, ?, ?, 0, '{}', '{}'
            )
            """,
            (NOW, NOW, NOW),
        )
        _insert_authority_graph(connection)
    return path


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _insert_authority_graph(connection: sqlite3.Connection) -> None:
    for key_id, purpose, marker in (
        ("authority_key", "authority_revision", 1),
        ("profile_key", "driver_profile", 2),
        ("matrix_key", "certification_matrix", 3),
        ("binding_key", "binding_revision", 4),
        ("evidence_key", "device_test_evidence", 5),
    ):
        connection.execute(
            """
            INSERT INTO device_authority_signer_keys (
                key_id, purpose, public_key, state, not_before
            ) VALUES (?, ?, ?, 'active', ?)
            """,
            (key_id, purpose, bytes([marker]) * 32, NOW),
        )
    connection.execute(
        """
        INSERT INTO device_authority_revisions (
            revision_id, revision_number, manifest_digest, effective_at,
            expires_at, revision_digest, signer_key_id, signature
        ) VALUES ('authority_revision_1', 1, ?, ?, ?, ?, 'authority_key', ?)
        """,
        (b"a" * 32, NOW, RETENTION, AUTHORITY_DIGESTS["revision"], b"s" * 64),
    )
    connection.execute(
        """
        INSERT INTO device_driver_profiles (
            profile_id, version, driver_id, min_agent_version, capabilities,
            profile_digest, signer_key_id, signature, effective_at,
            expires_at, authority_revision_id
        ) VALUES ('profile_1', '1.0', 'test', '1.0', ?, ?, 'profile_key', ?,
            ?, ?, 'authority_revision_1')
        """,
        (
            '[{"capability_id":"receipt","contract_major":1,"max_copies":1,"max_payload_bytes":1048576,"media_type":"image/jpeg","operation":"print","options_digest":"'
            + AUTHORITY_DIGESTS["options"].hex()
            + '","output_evidence":"transport"}]',
            AUTHORITY_DIGESTS["profile"],
            b"s" * 64,
            NOW,
            RETENTION,
        ),
    )
    connection.execute(
        """
        INSERT INTO hardware_certification_matrix_rows (
            row_id, version, device_id, device_identity_digest, manufacturer,
            model, firmware_version, firmware_build, driver_id,
            driver_profile_digest, capability_id, platform_backend_id,
            connection, media_profile, operating_system, release_set_id,
            effective_at, expires_at, matrix_row_digest, signer_key_id,
            signature, authority_revision_id
        ) VALUES ('matrix_1', 1, 'device_1', ?, 'Test', 'Printer', '1', '1',
            'test', ?, 'receipt', 'test', 'spooler', 'receipt', 'test', 'test',
            ?, ?, ?, 'matrix_key', ?, 'authority_revision_1')
        """,
        (
            AUTHORITY_DIGESTS["identity"],
            AUTHORITY_DIGESTS["profile"],
            NOW,
            RETENTION,
            AUTHORITY_DIGESTS["matrix"],
            b"s" * 64,
        ),
    )
    connection.execute(
        """
        INSERT INTO device_binding_revisions (
            revision_id, binding_id, revision_number, database,
            organization_id, site_id, scope_kind, pos_configuration_id,
            device_purpose, device_id, device_identity_digest, capability_id,
            driver_profile_digest, matrix_row_id, options_digest,
            binding_digest, signer_key_id, signature, effective_at,
            expires_at, authority_revision_id
        ) VALUES ('binding_1', 'binding_1', 1, 'odoo', 'org_1', 'site_1',
            'pos_configuration', 'pos_1', 'receipt', 'device_1', ?, 'receipt',
            ?, 'matrix_1', ?, ?, 'binding_key', ?, ?, ?,
            'authority_revision_1')
        """,
        (
            AUTHORITY_DIGESTS["identity"],
            AUTHORITY_DIGESTS["profile"],
            AUTHORITY_DIGESTS["options"],
            AUTHORITY_DIGESTS["binding"],
            b"s" * 64,
            NOW,
            RETENTION,
        ),
    )
    connection.execute(
        """
        INSERT INTO device_test_evidence (
            evidence_id, revision_id, device_id, device_identity_digest,
            capability_id, driver_profile_digest, matrix_row_id,
            output_evidence, result, test_pattern_digest, tested_at,
            valid_until, evidence_digest, signer_key_id, signature,
            authority_revision_id
        ) VALUES ('evidence_1', 'binding_1', 'device_1', ?, 'receipt', ?,
            'matrix_1', 'transport', 'passed', ?, ?, ?, ?, 'evidence_key', ?,
            'authority_revision_1')
        """,
        (
            AUTHORITY_DIGESTS["identity"],
            AUTHORITY_DIGESTS["profile"],
            b"t" * 32,
            NOW,
            RETENTION,
            AUTHORITY_DIGESTS["evidence"],
            b"s" * 64,
        ),
    )


def _insert_authority_proof(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO device_work_authority_proofs (
            proof_id, admission_id, authority_revision_id,
            authority_revision_number, authority_revision_digest,
            snapshot_digest, graph_digest, scope_digest, observation_digest,
            binding_revision_id, binding_revision_digest, driver_profile_id,
            driver_profile_digest, matrix_row_id, matrix_row_digest,
            test_evidence_id, test_evidence_digest, device_id,
            device_identity_digest, capability_id, device_purpose, operation,
            media_type, contract_major, options_digest, issued_at, valid_until
        ) VALUES ('proof_1', 'admission_1', 'authority_revision_1', 1, ?, ?, ?,
            ?, ?, 'binding_1', ?, 'profile_1', ?, 'matrix_1', ?, 'evidence_1',
            ?, 'device_1', ?, 'receipt', 'receipt', 'print', 'image/jpeg', 1,
            ?, ?, ?)
        """,
        (
            AUTHORITY_DIGESTS["revision"],
            b"s" * 32,
            b"g" * 32,
            b"c" * 32,
            b"o" * 32,
            AUTHORITY_DIGESTS["binding"],
            AUTHORITY_DIGESTS["profile"],
            AUTHORITY_DIGESTS["matrix"],
            AUTHORITY_DIGESTS["evidence"],
            AUTHORITY_DIGESTS["identity"],
            AUTHORITY_DIGESTS["options"],
            NOW,
            RETENTION,
        ),
    )


def _insert_job(
    connection: sqlite3.Connection,
    job_id: str = "job_1",
    *,
    prepare_admission: bool = True,
) -> None:
    _insert_authority_proof(connection)
    if prepare_admission:
        connection.execute(
            "UPDATE device_work_admissions SET state = 'finalizing' WHERE id = 'admission_1'"
        )
    connection.execute(
        """
        INSERT INTO public_print_jobs (
            id, admission_id, authority_proof_id, intent_id, device_id, scope_kind, organization_id, site_id,
            pos_configuration_id, paired_client_id, origin_kind, origin_json,
            state, state_version, accepted_at, expires_at, retryable,
            contract_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "admission_1",
            "proof_1",
            "intent_1",
            "device_1",
            "paired_client",
            "org_1",
            "site_1",
            "pos_1",
            "client_1",
            "pos",
            '{"organization_id":"org_1","site_id":"site_1"}',
            "accepted",
            1,
            NOW,
            LATER,
            0,
            "v1",
        ),
    )


def _insert_admission(
    connection: sqlite3.Connection,
    *,
    admission_id: str = "admission_1",
    job_id: str | None = None,
    state: str = "staging",
    planned_job_id: str = "job_1",
    intent_id: str = "intent_1",
    idempotency_key: str = "idem_1",
    origin_submission_key: str = "submission_1",
) -> None:
    connection.execute(
        """
        INSERT INTO device_work_admissions (
            normalized_options,
            id, planned_job_id, database, scope_kind, organization_id, site_id, pos_configuration_id,
            paired_client_id, idempotency_key, fingerprint, state, job_id,
            intent_id, device_id, deadline_at, original_size_bytes, created_at,
            updated_at, accepted_at, idempotency_expires_at, content_expires_at,
            actor_id, binding_revision_id, authorization_digest, operation, media_type,
            normalized_options_digest, grant_scope_digest, origin_submission_key,
            origin_kind, origin_json, contract_major, copy_ordinal
        ) VALUES (X'7b7d', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            admission_id,
            planned_job_id,
            "odoo",
            "paired_client",
            "org_1",
            "site_1",
            "pos_1",
            "client_1",
            idempotency_key,
            b"f" * 32,
            state,
            job_id,
            intent_id,
            "device_1",
            LATER,
            100,
            NOW,
            NOW,
            NOW if state == "accepted" else None,
            RETENTION,
            RETENTION,
            "actor_1",
            "binding_1",
            b"a" * 32,
            "print",
            "image/jpeg",
            b"b" * 32,
            b"c" * 32,
            origin_submission_key,
            "pos",
            '{"organization_id":"org_1","site_id":"site_1"}',
            1,
            0,
        ),
    )


def test_current_schema_contains_durable_job_and_spool_tables(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    engine = create_database_engine(database_path)
    try:
        table_names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert {
        "public_print_jobs",
        "device_work_admissions",
        "spool_root_keys",
        "spool_security_state",
        "spool_job_keys",
        "spool_artifacts",
        "spool_reservations",
        "spool_nonce_reservations",
        "public_print_job_events",
    } <= table_names
    assert {
        "public_print_jobs",
        "device_work_admissions",
        "spool_root_keys",
        "spool_security_state",
        "spool_job_keys",
        "spool_artifacts",
        "spool_reservations",
        "spool_nonce_reservations",
        "public_print_job_events",
    } <= MANAGED_TABLE_NAMES


def test_migrated_schema_matches_runtime_metadata(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    engine = create_database_engine(database_path)
    try:
        with engine.connect() as connection:
            differences = compare_metadata(
                MigrationContext.configure(connection), metadata
            )
    finally:
        engine.dispose()

    assert differences == []


def test_admission_stages_before_job_and_enforces_scoped_idempotency(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    with _connect(database_path) as connection:
        _insert_admission(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_job(connection, prepare_admission=False)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE device_work_admissions SET state = 'accepted' WHERE id = 'admission_1'"
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO device_work_admissions (
            normalized_options,
                id, planned_job_id, database, scope_kind, organization_id, site_id, pos_configuration_id,
                paired_client_id, idempotency_key, fingerprint, state, intent_id,
                device_id, deadline_at, original_size_bytes, created_at,
                updated_at, idempotency_expires_at, content_expires_at,
                actor_id, binding_revision_id, authorization_digest, operation, media_type,
                normalized_options_digest, grant_scope_digest, origin_submission_key,
                origin_kind, origin_json, contract_major, copy_ordinal
            ) VALUES (X'7b7d', 'admission_2', 'job_2', 'odoo', 'paired_client', 'org_1', 'site_1',
                'pos_1', 'client_1', 'idem_1', ?, 'staging', 'intent_2',
                'device_1', ?, 100, ?, ?, ?, ?, 'actor_1', 'binding_1', ?,
                'print', 'image/jpeg', ?, ?, 'submission_2', 'pos', '{}', 1, 0)
                """,
                (
                    b"g" * 32,
                    LATER,
                    NOW,
                    NOW,
                    RETENTION,
                    RETENTION,
                    b"a" * 32,
                    b"b" * 32,
                    b"c" * 32,
                ),
            )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_admission(
                connection,
                admission_id="admission_3",
                planned_job_id="job_3",
                intent_id="intent_3",
                idempotency_key="idem_3",
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE device_work_admissions SET origin_json = ? WHERE id = 'admission_1'",
                ('{"document_bytes":"secret"}',),
            )

        _insert_job(connection)
        connection.execute(
            "UPDATE device_work_admissions SET state = 'accepted', job_id = 'job_1', accepted_at = ? WHERE id = 'admission_1'",
            (NOW,),
        )


def test_manager_idempotency_is_unique_when_optional_scope_is_null(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    statement = """
        INSERT INTO device_work_admissions (
            normalized_options,
            id, planned_job_id, database, scope_kind, organization_id, site_id, pos_configuration_id,
            paired_client_id, idempotency_key, fingerprint, state, intent_id,
            device_id, deadline_at, original_size_bytes, created_at,
            updated_at, idempotency_expires_at, content_expires_at,
            actor_id, binding_revision_id, authorization_digest, operation, media_type,
            normalized_options_digest, grant_scope_digest, origin_submission_key,
            origin_kind, origin_json, contract_major, copy_ordinal
        ) VALUES (X'7b7d', ?, ?, 'odoo', 'device_manager', 'org_1', 'site_1', NULL, NULL,
            'idem_1', ?, 'staging', ?, 'device_1', ?, 100, ?, ?, ?, ?,
            'actor_1', 'binding_1', ?, 'print', 'image/jpeg', ?, ?, 'submission_1', 'pos', '{}', 1, 0)
    """
    with _connect(database_path) as connection:
        connection.execute(
            statement,
            (
                "admission_1",
                "job_1",
                b"f" * 32,
                "intent_1",
                LATER,
                NOW,
                NOW,
                RETENTION,
                RETENTION,
                b"a" * 32,
                b"b" * 32,
                b"c" * 32,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                statement,
                (
                    "admission_2",
                    "job_2",
                    b"g" * 32,
                    "intent_2",
                    LATER,
                    NOW,
                    NOW,
                    RETENTION,
                    RETENTION,
                    b"a" * 32,
                    b"b" * 32,
                    b"c" * 32,
                ),
            )


def test_reservation_and_artifact_lifecycle_guards(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    with _connect(database_path) as connection:
        _insert_admission(connection)
        connection.execute(
            """
            INSERT INTO spool_reservations (
                id, reservation_kind, admission_id, device_id, owner_id,
                owner_generation, queue_slots, original_bytes, persistent_bytes,
                temporary_bytes, state, created_at, expires_at
            ) VALUES ('reservation_1', 'admission', 'admission_1', 'device_1',
                'worker_1', 1, 1, 100, 65636, 0, 'held', ?, ?)
            """,
            (NOW, LATER),
        )
        connection.execute(
            """
            INSERT INTO spool_nonce_reservations (
                id, domain, root_version, planned_job_id, purpose, admission_id, nonce, created_at
            ) VALUES ('nonce_1', 'artifact', NULL, 'job_1', 'original', 'admission_1', ?, ?)
            """,
            (b"n" * 12, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO spool_nonce_reservations (
                    id, domain, root_version, planned_job_id, purpose, admission_id, nonce, created_at
                ) VALUES ('nonce_2', 'artifact', NULL, 'job_1', 'derived_raster',
                    'admission_1', ?, ?)
                """,
                (b"n" * 12, NOW),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE spool_nonce_reservations SET purpose = 'other' WHERE id = 'nonce_1'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM spool_nonce_reservations WHERE id = 'nonce_1'"
            )
        connection.execute(
            """
            INSERT INTO spool_artifacts (
                id, admission_id, reservation_id, aad_job_id, intent_id,
                format_version, artifact_kind, storage_ref, nonce,
                plaintext_size_bytes, ciphertext_size_bytes, plaintext_sha256,
                state, retention_policy, created_at, delete_attempts
            ) VALUES ('artifact_1', 'admission_1', 'reservation_1', 'job_1',
                'intent_1', 1, 'original', 'artifact-ref-1', ?, 100, 116, ?,
                'staging', 'active', ?, 0)
            """,
            (b"n" * 12, b"h" * 32, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE spool_reservations SET device_id = 'other' WHERE id = 'reservation_1'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE spool_artifacts SET intent_id = 'other' WHERE id = 'artifact_1'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE spool_artifacts SET state = 'committed', committed_at = ? WHERE id = 'artifact_1'",
                (LATER,),
            )

        _insert_job(connection)
        connection.execute(
            "UPDATE device_work_admissions SET state = 'accepted', job_id = 'job_1', accepted_at = ? WHERE id = 'admission_1'",
            (NOW,),
        )
        connection.execute(
            "UPDATE spool_reservations SET state = 'committed', job_id = 'job_1' WHERE id = 'reservation_1'"
        )
        connection.execute(
            "UPDATE spool_artifacts SET state = 'committed', job_id = 'job_1', committed_at = ? WHERE id = 'artifact_1'",
            (LATER,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE spool_artifacts SET ciphertext_size_bytes = 115 WHERE id = 'artifact_1'"
            )


def test_root_key_rotation_anchor_and_wrapped_key_constraints(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    with _connect(database_path) as connection:
        _insert_admission(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO spool_root_keys VALUES (4294967296, 'root-x', 'current', ?, ?, NULL, NULL)",
                (NOW, LATER),
            )
        connection.execute(
            "INSERT INTO spool_root_keys VALUES (1, 'root-1', 'current', ?, ?, NULL, NULL)",
            (NOW, LATER),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO spool_root_keys VALUES (2, 'root-2', 'current', ?, ?, NULL, NULL)",
                (NOW, LATER),
            )
        connection.execute(
            "INSERT INTO spool_security_state VALUES (1, 'software', 'anchor-1', ?, 1, 'ready', NULL, NULL, ?)",
            (b"e" * 32, NOW),
        )
        connection.execute(
            "UPDATE spool_security_state SET committed_generation = 2, updated_at = ? WHERE singleton_id = 1",
            (LATER,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE spool_security_state SET committed_generation = 1 WHERE singleton_id = 1"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO spool_security_state VALUES (1, 'software', 'anchor-2', ?, 2, 'ready', NULL, NULL, ?)",
                (b"e" * 32, LATER),
            )
        _insert_job(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO spool_job_keys (job_id, root_version, intent_id, format_version, wrap_nonce, wrapped_key, created_at) VALUES ('job_1', 1, 'intent_1', 1, ?, ?, ?)",
                (b"n" * 12, b"k" * 47, NOW),
            )
        connection.execute(
            "INSERT INTO spool_job_keys (job_id, root_version, intent_id, format_version, wrap_nonce, wrapped_key, created_at) VALUES ('job_1', 1, 'intent_1', 1, ?, ?, ?)",
            (b"n" * 12, b"k" * 48, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE spool_job_keys SET intent_id = 'other' WHERE job_id = 'job_1'"
            )
        connection.execute(
            "UPDATE spool_job_keys SET wrapped_key = ?, key_deleted_at = ? WHERE job_id = 'job_1'",
            (b"", LATER),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE spool_job_keys SET wrapped_key = ?, key_deleted_at = NULL WHERE job_id = 'job_1'",
                (b"k" * 48,),
            )


def test_events_are_agent_wide_and_content_free(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    with _connect(database_path) as connection:
        _insert_admission(connection)
        _insert_job(connection)
        connection.execute(
            "INSERT INTO public_print_job_events (job_id, state_version, event_type, snapshot_json, occurred_at) VALUES (?, ?, ?, ?, ?)",
            (
                "job_1",
                1,
                "accepted",
                '{"job_id":"job_1","state":"accepted","state_version":1}',
                NOW,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO public_print_job_events (job_id, state_version, event_type, snapshot_json, occurred_at) VALUES (?, ?, ?, ?, ?)",
                ("job_1", 2, "accepted", '{"receipt_payload":"secret"}', NOW),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO public_print_job_events (job_id, state_version, event_type, snapshot_json, occurred_at) VALUES (?, ?, ?, ?, ?)",
                (
                    "job_1",
                    2,
                    "accepted",
                    '{"job_id":"job_1","state":"accepted","state_version":2}',
                    NOW,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO public_print_job_events (job_id, state_version, event_type, snapshot_json, occurred_at) VALUES (?, ?, ?, ?, ?)",
                (
                    "job_1",
                    2,
                    "accepted",
                    '{"details":{"document_bytes":"secret"},"job_id":"job_1","state":"accepted","state_version":2}',
                    NOW,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE public_print_jobs SET state = 'in_progress', state_version = 1, started_at = ? WHERE id = 'job_1'",
                (LATER,),
            )


def test_schema_downgrades_cleanly(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    migrator = DatabaseMigrator(database_path)
    config = migrator._build_alembic_config()

    from alembic import command

    command.downgrade(config, "20260418_0003")

    with _connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "public_print_jobs" not in tables
    assert "spool_artifacts" not in tables
