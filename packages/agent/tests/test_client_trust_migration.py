from __future__ import annotations

from pathlib import Path
import sqlite3

from alembic import command

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
