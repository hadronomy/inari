from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
import logging
import os
from pathlib import Path
import sqlite3
import time

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from alembic.runtime.migration import MigrationContext

from .schema import MANAGED_TABLE_NAMES, create_database_engine

logger = logging.getLogger(__name__)

BASELINE_REVISION = "20260414_0001"
ALEMBIC_VERSION_TABLE = "alembic_version"
_SQLITE_INTERNAL_TABLES = frozenset({"sqlite_sequence"})


class DatabaseMigrationError(RuntimeError):
    """Raised when the runtime database cannot be migrated safely."""


@dataclass(slots=True, frozen=True)
class DatabaseMigrationResult:
    previous_revision: str | None
    current_revision: str
    backup_path: Path | None
    migrated: bool
    stamped_legacy: bool


@dataclass(slots=True, frozen=True)
class _DatabaseState:
    current_revision: str | None
    table_names: frozenset[str]

    @property
    def user_tables(self) -> frozenset[str]:
        return self.table_names - _SQLITE_INTERNAL_TABLES - {ALEMBIC_VERSION_TABLE}

    @property
    def is_empty(self) -> bool:
        return not self.user_tables

    @property
    def is_legacy_compatible(self) -> bool:
        return self.current_revision is None and MANAGED_TABLE_NAMES.issubset(
            self.user_tables
        )


class DatabaseMigrator:
    def __init__(
        self,
        database_path: Path,
        *,
        lock_timeout_seconds: float = 30.0,
    ) -> None:
        self.database_path = database_path
        self.lock_timeout_seconds = lock_timeout_seconds
        self.lock_path = database_path.with_suffix(
            f"{database_path.suffix}.migrate.lock"
        )

    def ensure_current(self) -> DatabaseMigrationResult:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._migration_lock():
            config = self._build_alembic_config()
            target_revision = self._head_revision(config)
            state = self._inspect_state()
            self._validate_known_revision(config, state.current_revision)

            if state.current_revision == target_revision:
                return DatabaseMigrationResult(
                    previous_revision=state.current_revision,
                    current_revision=target_revision,
                    backup_path=None,
                    migrated=False,
                    stamped_legacy=False,
                )

            backup_path = self.backup_database() if not state.is_empty else None
            previous_revision = state.current_revision
            stamped_legacy = False

            if state.current_revision is None:
                if state.is_empty:
                    logger.info(
                        "Upgrading empty database to revision %s", target_revision
                    )
                    command.upgrade(config, "head")
                elif state.is_legacy_compatible:
                    logger.info(
                        "Stamping legacy unversioned database at revision %s",
                        BASELINE_REVISION,
                    )
                    command.stamp(config, BASELINE_REVISION)
                    stamped_legacy = True
                    if BASELINE_REVISION != target_revision:
                        logger.info(
                            "Upgrading stamped legacy database to revision %s",
                            target_revision,
                        )
                        command.upgrade(config, "head")
                else:
                    raise DatabaseMigrationError(
                        "Existing database is not versioned and does not match the supported legacy schema."
                    )
            else:
                logger.info(
                    "Upgrading database from revision %s to %s",
                    state.current_revision,
                    target_revision,
                )
                command.upgrade(config, "head")

            current_revision = self.current_revision()
            if current_revision != target_revision:
                raise DatabaseMigrationError(
                    f"Database migration completed unexpectedly at revision {current_revision!r}; expected {target_revision!r}."
                )
            return DatabaseMigrationResult(
                previous_revision=previous_revision,
                current_revision=current_revision,
                backup_path=backup_path,
                migrated=True,
                stamped_legacy=stamped_legacy,
            )

    def current_revision(self) -> str | None:
        if not self.database_path.exists():
            return None
        state = self._inspect_state()
        return state.current_revision

    def backup_database(self) -> Path | None:
        if not self.database_path.exists() or self.database_path.stat().st_size == 0:
            return None
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.database_path.with_name(
            f"{self.database_path.stem}.backup-{timestamp}{self.database_path.suffix}"
        )
        suffix_counter = 1
        while backup_path.exists():
            backup_path = self.database_path.with_name(
                f"{self.database_path.stem}.backup-{timestamp}-{suffix_counter}{self.database_path.suffix}"
            )
            suffix_counter += 1
        logger.info("Creating database backup at %s", backup_path)
        source = sqlite3.connect(self.database_path)
        try:
            destination = sqlite3.connect(backup_path)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        return backup_path

    def _build_alembic_config(self) -> Config:
        config = Config()
        config.set_main_option(
            "script_location", str(resources.files("inari.db.alembic"))
        )
        config.set_main_option(
            "sqlalchemy.url", f"sqlite+pysqlite:///{self.database_path}"
        )
        return config

    def _head_revision(self, config: Config) -> str:
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()
        if len(heads) != 1:
            raise DatabaseMigrationError(
                f"Expected exactly one migration head, found {len(heads)}."
            )
        return heads[0]

    def _validate_known_revision(
        self, config: Config, current_revision: str | None
    ) -> None:
        if current_revision is None:
            return
        script = ScriptDirectory.from_config(config)
        if script.get_revision(current_revision) is None:
            raise DatabaseMigrationError(
                f"Database revision {current_revision!r} is newer than this agent understands."
            )

    def _inspect_state(self) -> _DatabaseState:
        engine = create_database_engine(self.database_path)
        try:
            with engine.connect() as connection:
                inspector = inspect(connection)
                table_names = frozenset(inspector.get_table_names())
                current_revision = _read_current_revision(connection)
        finally:
            engine.dispose()
        return _DatabaseState(
            current_revision=current_revision,
            table_names=table_names,
        )

    @contextlib.contextmanager
    def _migration_lock(self):
        deadline = time.monotonic() + self.lock_timeout_seconds
        handle: int | None = None
        while handle is None:
            try:
                handle = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                payload = f"pid={os.getpid()} created_at={datetime.now(UTC).isoformat()}".encode(
                    "utf-8"
                )
                os.write(handle, payload)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise DatabaseMigrationError(
                        f"Timed out waiting for database migration lock: {self.lock_path}"
                    ) from None
                time.sleep(0.1)
        try:
            yield
        finally:
            os.close(handle)
            with contextlib.suppress(FileNotFoundError):
                self.lock_path.unlink()


def _read_current_revision(connection: Connection) -> str | None:
    context = MigrationContext.configure(connection)
    return context.get_current_revision()
