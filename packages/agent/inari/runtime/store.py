from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from sqlalchemy.engine import Connection

from ..db.schema import create_database_engine, metadata


class RuntimeStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()
        self.engine = create_database_engine(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.engine.begin() as connection:
            metadata.create_all(connection)

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        with self._lock, self.engine.begin() as connection:
            yield connection


def dump_json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


def load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    loaded = json.loads(value)
    if isinstance(loaded, dict):
        return dict(loaded)
    return {"value": loaded}
