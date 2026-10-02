from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from sqlalchemy.engine import Connection

from ..db.schema import create_database_engine


class DatabaseCommitUncertainError(RuntimeError):
    """The caller cannot prove whether SQLite committed the transaction."""

    def __init__(self) -> None:
        super().__init__("The database commit result is uncertain.")


class RuntimeStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()
        self._immediate_transaction_active = False
        self.engine = create_database_engine(database_path)

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        with self._lock, self.engine.begin() as connection:
            yield connection

    @contextmanager
    def immediate_transaction(self) -> Iterator[Connection]:
        """Run one writer transaction with SQLite's RESERVED lock acquired first."""
        with self._lock:
            if self._immediate_transaction_active:
                raise RuntimeError("An immediate transaction is already active.")
            self._immediate_transaction_active = True
            connection: Connection | None = None
            try:
                connection = self.engine.connect()
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                try:
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise DatabaseCommitUncertainError from None
            finally:
                if connection is not None:
                    connection.close()
                self._immediate_transaction_active = False


def dump_json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


def load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    loaded = json.loads(value)
    if isinstance(loaded, dict):
        return dict(loaded)
    return {"value": loaded}
