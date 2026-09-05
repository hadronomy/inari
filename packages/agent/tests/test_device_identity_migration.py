from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from uuid import UUID, uuid5

import pytest
from alembic import command

from inari.db.migrations import DatabaseMigrator
from inari.drivers import DeviceIdentity, DeviceKind, DeviceTransport
from inari.runtime.models import build_device_id


NOW = "2026-08-28T10:00:00Z"
LATER = "2026-08-28T10:05:00Z"
EXPIRY = "2026-11-26T10:00:00Z"
NAMESPACE = UUID("efdbfb52-14ac-5c5c-a01c-b2a846f71d76")


def test_upgrade_merges_duplicate_devices_and_rewrites_all_references(
    tmp_path: Path,
) -> None:
    database_path = _database_at_0005(tmp_path)
    old_a = "old-device-a"
    old_b = "old-device-b"
    with sqlite3.connect(database_path) as connection:
        _insert_device(
            connection,
            old_a,
            name="Counter printer",
            first_seen_at="2026-08-01T00:00:00Z",
            last_seen_at="2026-08-02T00:00:00Z",
            updated_at="2026-08-02T00:00:00Z",
            is_default=0,
        )
        _insert_device(
            connection,
            old_b,
            name="Receipt printer",
            first_seen_at="2026-07-01T00:00:00Z",
            last_seen_at="2026-08-03T00:00:00Z",
            updated_at="2026-08-03T00:00:00Z",
            is_default=1,
        )
        _insert_references(connection, old_a)
        _insert_references(connection, old_b)
        _insert_json_references(connection, old_a, old_b)

    _upgrade_identity(database_path)
    expected_id = build_device_id(
        kind=DeviceKind.PRINTER,
        identity=DeviceIdentity(
            transport=DeviceTransport.USB,
            serial_number=" serial-1 ",
            vendor_id=0x1234,
            product_id=0x5678,
        ),
    )
    with sqlite3.connect(database_path) as connection:
        devices = connection.execute(
            "SELECT id, first_seen_at, last_seen_at, updated_at, is_default FROM devices"
        ).fetchall()
        assert devices == [
            (
                expected_id,
                "2026-07-01T00:00:00Z",
                "2026-08-03T00:00:00Z",
                "2026-08-03T00:00:00Z",
                1,
            )
        ]
        for table in (
            "device_events",
            "jobs",
            "public_print_jobs",
            "device_work_admissions",
            "spool_reservations",
        ):
            assert connection.execute(
                f"SELECT DISTINCT device_id FROM {table}"
            ).fetchall() == [(expected_id,)]
        payload = connection.execute(
            "SELECT payload_json FROM device_events WHERE sequence = 1"
        ).fetchone()
        assert json.loads(payload[0]) == {
            "device_id": expected_id,
            "nested": {"other_device_id": expected_id},
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_upgrade_rewrites_legacy_fallback_with_normalized_unicode_name(
    tmp_path: Path,
) -> None:
    database_path = _database_at_0005(tmp_path)
    old_id = "legacy-device"
    name = "  Cafe\u0301  "
    with sqlite3.connect(database_path) as connection:
        _insert_device(
            connection,
            old_id,
            name=name,
            identity_serial_number=None,
            identity_transport="spooler",
            identity_os_instance_id=" legacy:old.driver:raw name ",
        )

    _upgrade_identity(database_path)
    expected_id = build_device_id(
        kind=DeviceKind.PRINTER,
        identity=DeviceIdentity(
            transport=DeviceTransport.SPOOLER,
            os_instance_id="legacy-name:Café",
        ),
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT id, identity_os_instance_id FROM devices"
        ).fetchone() == (expected_id, "legacy-name:Café")

    config = DatabaseMigrator(database_path)._build_alembic_config()
    command.downgrade(config, "20260827_0005")
    expected_old_id = (
        "dev_"
        + uuid5(
            NAMESPACE,
            "printer\0driver.one\0os:spooler:legacy:driver.one:  Cafe\u0301  ",
        ).hex
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT id, identity_os_instance_id FROM devices"
        ).fetchone() == (expected_old_id, "legacy:driver.one:  Cafe\u0301  ")


def test_upgrade_validates_before_write_and_rolls_back_invalid_json(
    tmp_path: Path,
) -> None:
    database_path = _database_at_0005(tmp_path)
    with sqlite3.connect(database_path) as connection:
        _insert_device(connection, "old-device")
        connection.execute(
            "UPDATE devices SET metadata_json = '{invalid' WHERE id = 'old-device'"
        )
    with pytest.raises(RuntimeError, match="Invalid or too-deep JSON"):
        _upgrade_identity(database_path)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT id FROM devices").fetchone() == (
            "old-device",
        )
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("20260827_0005",)


def test_nonduplicate_upgrade_and_downgrade_restore_the_0004_id(
    tmp_path: Path,
) -> None:
    database_path = _database_at_0005(tmp_path)
    with sqlite3.connect(database_path) as connection:
        _insert_device(connection, "old-device")
    _upgrade_identity(database_path)
    config = DatabaseMigrator(database_path)._build_alembic_config()
    command.downgrade(config, "20260827_0005")

    expected_id = (
        "dev_"
        + uuid5(
            NAMESPACE,
            "printer\0driver.one\0hardware:1234:5678: serial-1 ",
        ).hex
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT id FROM devices").fetchone() == (expected_id,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _database_at_0005(tmp_path: Path) -> Path:
    database_path = tmp_path / "runtime.sqlite3"
    migrator = DatabaseMigrator(database_path)
    migrator.ensure_current()
    command.downgrade(migrator._build_alembic_config(), "20260827_0005")
    return database_path


def _upgrade_identity(database_path: Path) -> None:
    migrator = DatabaseMigrator(database_path)
    command.upgrade(migrator._build_alembic_config(), "20260828_0006")


def _insert_device(
    connection: sqlite3.Connection,
    device_id: str,
    *,
    name: str = "Receipt printer",
    identity_transport: str = "usb",
    identity_serial_number: str | None = " serial-1 ",
    identity_os_instance_id: str | None = None,
    first_seen_at: str = NOW,
    last_seen_at: str = NOW,
    updated_at: str = NOW,
    is_default: int = 0,
) -> None:
    connection.execute(
        """
        INSERT INTO devices (
            id, kind, driver_key, identity_transport, identity_serial_number,
            identity_vendor_id, identity_product_id, identity_os_instance_id,
            identity_port_id, name, connection_state, first_seen_at,
            last_seen_at, updated_at, is_default, preferred_transport,
            capabilities_json, metadata_json
        ) VALUES (?, 'printer', 'driver.one', ?, ?, 4660, 22136,
            ?, NULL, ?, 'online', ?, ?, ?, ?, 'raw', '{}', '{}')
        """,
        (
            device_id,
            identity_transport,
            identity_serial_number,
            identity_os_instance_id,
            name,
            first_seen_at,
            last_seen_at,
            updated_at,
            is_default,
        ),
    )


def _insert_references(connection: sqlite3.Connection, device_id: str) -> None:
    suffix = device_id.replace("-", "_")
    connection.execute(
        "INSERT INTO device_events (device_id, event_type, payload_json, occurred_at) VALUES (?, ?, ?, ?)",
        (device_id, "device.updated", "{}", NOW),
    )
    connection.execute(
        """
        INSERT INTO jobs (
            id, kind, operation, device_id, device_kind, device_name, state,
            request_json, request_metadata_json, attempt_count, max_attempts,
            created_at, updated_at, queued_at, next_run_at
        ) VALUES (?, 'print', 'print', ?, 'printer', 'Receipt printer', 'queued',
            '{}', '{}', 0, 1, ?, ?, ?, ?)
        """,
        (f"job-{suffix}", device_id, NOW, NOW, NOW, NOW),
    )
    connection.execute(
        """
        INSERT INTO public_print_jobs (
            id, admission_id, intent_id, device_id, scope_kind, organization_id, site_id,
            pos_configuration_id, paired_client_id, origin_kind, origin_json,
            state, state_version, accepted_at, expires_at, retryable,
            contract_version
        ) VALUES (?, ?, ?, ?, 'paired_client', 'org-1', 'site-1', 'pos-1',
            'client-1', 'pos', '{}', 'accepted', 1, ?, ?, 0, 'v1')
        """,
        (
            f"public-{suffix}",
            f"admission-{suffix}",
            f"intent-{suffix}",
            device_id,
            NOW,
            EXPIRY,
        ),
    )
    connection.execute(
        """
        INSERT INTO device_work_admissions (
            id, planned_job_id, database, scope_kind, organization_id, site_id, pos_configuration_id,
            paired_client_id, idempotency_key, fingerprint, state, job_id,
            intent_id, device_id, deadline_at, original_size_bytes, created_at,
            updated_at, accepted_at, idempotency_expires_at, content_expires_at,
            actor_id, binding_revision_id, authorization_digest, operation, media_type,
            normalized_options_digest, grant_scope_digest, origin_submission_key,
            origin_kind, origin_json, contract_major, copy_ordinal
        ) VALUES (?, ?, 'odoo', 'paired_client', 'org-1', 'site-1', 'pos-1', 'client-1',
            ?, ?, 'accepted', ?, ?, ?, ?, 100, ?, ?, ?, ?, ?,
            'actor-1', 'binding-1', ?, 'print', 'image/jpeg', ?, ?, ?, 'pos', '{}', 1, 0)
        """,
        (
            f"admission-{suffix}",
            f"public-{suffix}",
            f"idem-{suffix}",
            b"a" * 32,
            f"public-{suffix}",
            f"intent-{suffix}",
            device_id,
            LATER,
            NOW,
            NOW,
            NOW,
            EXPIRY,
            EXPIRY,
            b"a" * 32,
            b"b" * 32,
            b"c" * 32,
            f"submission-{suffix}",
        ),
    )
    connection.execute(
        """
        INSERT INTO spool_reservations (
            id, reservation_kind, admission_id, job_id, device_id, owner_id,
            owner_generation, queue_slots, original_bytes, persistent_bytes,
            temporary_bytes, state, created_at, expires_at
        ) VALUES (?, 'admission', ?, ?, ?, 'owner-1', 1, 1, 100, 65636,
            0, 'committed', ?, ?)
        """,
        (
            f"reservation-{suffix}",
            f"admission-{suffix}",
            f"public-{suffix}",
            device_id,
            NOW,
            EXPIRY,
        ),
    )


def _insert_json_references(
    connection: sqlite3.Connection, old_a: str, old_b: str
) -> None:
    connection.execute(
        "UPDATE devices SET metadata_json = ? WHERE id = ?",
        (json.dumps({"device_id": old_a, "nested": {"other_device_id": old_b}}), old_a),
    )
    connection.execute(
        "UPDATE device_events SET payload_json = ? WHERE device_id = ?",
        (json.dumps({"device_id": old_a, "nested": {"other_device_id": old_b}}), old_a),
    )
