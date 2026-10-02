from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from inari.db.migrations import DatabaseMigrator
from inari.runtime.store import DatabaseCommitUncertainError, RuntimeStore


def runtime_store(database_path: Path) -> RuntimeStore:
    DatabaseMigrator(database_path).ensure_current()
    return RuntimeStore(database_path)


def test_runtime_store_configures_durable_sqlite_pragmas(tmp_path: Path) -> None:
    store = runtime_store(tmp_path / "runtime.sqlite3")

    with store.connection() as connection:
        assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"


def test_immediate_transaction_rolls_back_on_exception(tmp_path: Path) -> None:
    store = runtime_store(tmp_path / "runtime.sqlite3")

    with pytest.raises(RuntimeError, match="abort"):
        with store.immediate_transaction() as connection:
            connection.execute(
                text(
                    "INSERT INTO gateway_outbox "
                    "(message_id, message_type, state, payload_json, created_at, updated_at) "
                    "VALUES ('message-1', 'test', 'pending', '{}', 'now', 'now')"
                )
            )
            raise RuntimeError("abort")

    with store.connection() as connection:
        count = connection.execute(
            text("SELECT COUNT(*) FROM gateway_outbox WHERE message_id = 'message-1'")
        ).scalar_one()
    assert count == 0


def test_immediate_transaction_excludes_another_writer(tmp_path: Path) -> None:
    store = runtime_store(tmp_path / "runtime.sqlite3")
    second = store.engine.connect()
    try:
        second.exec_driver_sql("PRAGMA busy_timeout = 0")
        with store.immediate_transaction() as first:
            first.execute(
                text(
                    "INSERT INTO gateway_outbox "
                    "(message_id, message_type, state, payload_json, created_at, updated_at) "
                    "VALUES ('message-1', 'test', 'pending', '{}', 'now', 'now')"
                )
            )
            with pytest.raises(OperationalError, match="locked"):
                second.exec_driver_sql("BEGIN IMMEDIATE")
    finally:
        second.close()


def test_immediate_transaction_rejects_nesting(tmp_path: Path) -> None:
    store = runtime_store(tmp_path / "runtime.sqlite3")

    with store.immediate_transaction():
        with pytest.raises(RuntimeError, match="already active"):
            with store.immediate_transaction():
                pass


def test_immediate_transaction_reports_an_uncertain_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    store = runtime_store(database_path)
    real_commit = Connection.commit

    def commit_then_fail(connection: Connection) -> None:
        real_commit(connection)
        raise OSError("connection failed after commit")

    with monkeypatch.context() as patch:
        patch.setattr(Connection, "commit", commit_then_fail)
        with pytest.raises(DatabaseCommitUncertainError):
            with store.immediate_transaction() as connection:
                connection.execute(
                    text(
                        "INSERT INTO gateway_outbox "
                        "(message_id, message_type, state, payload_json, created_at, updated_at) "
                        "VALUES ('message-uncertain', 'test', 'pending', '{}', 'now', 'now')"
                    )
                )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM gateway_outbox WHERE message_id = 'message-uncertain'"
        ).fetchone() == (1,)
