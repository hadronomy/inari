from __future__ import annotations

from pathlib import Path
import sqlite3

from alembic import command
import pytest

from inari.db.migrations import DatabaseMigrator


def test_client_trust_revision_creates_all_durable_tables_and_guards(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime.sqlite3"
    DatabaseMigrator(path).ensure_current()

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }

    assert {
        "client_trust_pairing_requests",
        "client_trust_pairings",
        "client_trust_grants",
        "client_trust_replays",
    } <= tables
    assert {
        "trg_client_trust_pairings_immutable",
        "trg_client_trust_grants_immutable",
        "trg_client_trust_pairing_requests_state",
        "trg_client_trust_pairings_lifecycle",
        "trg_client_trust_grants_lifecycle",
    } <= triggers


def test_pairing_request_database_guard_requires_approval(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    DatabaseMigrator(path).ensure_current()
    values = (
        "request_1",
        "agent_1",
        "https://odoo.example",
        "https://agent.example",
        "odoo",
        "company_1",
        "organization_1",
        "site_1",
        "pos_1",
        "inari.local",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        '["device_work:receipt_image"]',
        "session_1",
        "amber-river-seven",
        "2026-08-31T12:00:00.000000Z",
        "2026-08-31T12:10:00.000000Z",
        "pending",
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO client_trust_pairing_requests (
                request_id, agent_id, browser_origin, agent_endpoint, database,
                company_id, organization_id, site_id, pos_configuration_id,
                audience, browser_jwk_thumbprint, requested_permissions,
                session_nonce, phrase, created_at, expires_at, state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE client_trust_pairing_requests SET state = 'completed' WHERE request_id = 'request_1'"
            )
        connection.execute(
            "UPDATE client_trust_pairing_requests SET state = 'approved' WHERE request_id = 'request_1'"
        )
        connection.execute(
            "UPDATE client_trust_pairing_requests SET state = 'completed' WHERE request_id = 'request_1'"
        )


def test_client_trust_revision_downgrade_removes_tables(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator.ensure_current()
    config = migrator._build_alembic_config()
    command.downgrade(config, "20260828_0007")

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert not {
        "client_trust_pairing_requests",
        "client_trust_pairings",
        "client_trust_grants",
        "client_trust_replays",
    }.intersection(tables)
