from __future__ import annotations

import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from iot_agent.cli import app
from iot_agent.db.migrations import DatabaseMigrator

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_migrator_upgrades_empty_database_to_head(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    expected_revision = _head_revision(database_path)

    result = DatabaseMigrator(database_path).ensure_current()

    assert result.current_revision == expected_revision
    assert result.backup_path is None
    assert result.migrated is True
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert revision == (expected_revision,)


def test_migrator_stamps_legacy_unversioned_database_and_creates_backup(
    tmp_path: Path,
) -> None:
    database_path = _create_legacy_database(tmp_path / "legacy.sqlite3")
    migrator = DatabaseMigrator(database_path)
    expected_revision = _head_revision(database_path)

    result = migrator.ensure_current()

    assert result.stamped_legacy is True
    assert result.current_revision == expected_revision
    assert result.backup_path is not None
    assert result.backup_path.exists()
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        device_count = connection.execute("SELECT COUNT(*) FROM devices").fetchone()
        outbox_state = connection.execute(
            "SELECT state FROM gateway_outbox WHERE message_id = ?",
            ("msg_legacy_ack",),
        ).fetchone()
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(gateway_outbox)")
        }
    assert revision == (expected_revision,)
    assert device_count == (1,)
    assert outbox_state == ("sent",)
    assert "acknowledged_at" not in columns


def test_db_cli_upgrade_and_current_commands(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    runner = CliRunner()
    expected_revision = _head_revision(tmp_path / "runtime.sqlite3")

    upgrade_result = runner.invoke(app, ["db", "upgrade", "--config", str(config_path)])
    assert upgrade_result.exit_code == 0, upgrade_result.output
    assert expected_revision in upgrade_result.output

    current_result = runner.invoke(app, ["db", "current", "--config", str(config_path)])
    assert current_result.exit_code == 0, current_result.output
    assert current_result.output.strip() == expected_revision


def _create_legacy_database(database_path: Path) -> Path:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    schema = (FIXTURES_DIR / "legacy_runtime_schema.sql").read_text(encoding="utf-8")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(schema)
        connection.execute(
            """
            INSERT INTO devices (
                id, kind, driver_key, name, connection_state,
                first_seen_at, last_seen_at, updated_at,
                is_default, preferred_transport, capabilities_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "dev_legacy_1",
                "printer",
                "legacy.driver",
                "Legacy Printer",
                "online",
                "2026-04-13T00:00:00Z",
                "2026-04-13T00:00:00Z",
                "2026-04-13T00:00:00Z",
                1,
                "raw",
                "{}",
                "{}",
            ),
        )
        connection.execute(
            """
            INSERT INTO gateway_outbox (
                message_id, message_type, state, payload_json, correlation_id, dedupe_key,
                created_at, updated_at, sent_at, acknowledged_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "msg_legacy_ack",
                "agent.runtime.event",
                "acknowledged",
                "{}",
                None,
                None,
                "2026-04-13T00:00:00Z",
                "2026-04-13T00:00:00Z",
                "2026-04-13T00:00:01Z",
                "2026-04-13T00:00:02Z",
                None,
            ),
        )
    return database_path


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "iot-agent.toml"
    config_path.write_text(
        '[paths]\ndata_dir = "."\nruntime_database = "./runtime.sqlite3"\n',
        encoding="utf-8",
    )
    return config_path


def _head_revision(database_path: Path) -> str:
    migrator = DatabaseMigrator(database_path)
    return migrator._head_revision(migrator._build_alembic_config())
