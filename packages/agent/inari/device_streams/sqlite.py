from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db.schema import device_stream_generations_table, device_stream_state_table
from ..runtime.store import RuntimeStore


class SqliteDeviceStreamLedger:
    """Allocate durable stream sequences and fencing generations."""

    def __init__(self, store: RuntimeStore) -> None:
        self._store = store

    def current_sequence(self) -> int:
        with self._store.connection() as connection:
            value = connection.execute(
                select(device_stream_state_table.c.current_sequence).where(
                    device_stream_state_table.c.id == 1
                )
            ).scalar_one_or_none()
        return int(value or 0)

    def next_sequence(self) -> int:
        with self._store.immediate_transaction() as connection:
            connection.execute(
                sqlite_insert(device_stream_state_table)
                .values(id=1, current_sequence=0)
                .on_conflict_do_nothing()
            )
            connection.execute(
                update(device_stream_state_table)
                .where(device_stream_state_table.c.id == 1)
                .values(
                    current_sequence=device_stream_state_table.c.current_sequence + 1
                )
            )
            value = connection.execute(
                select(device_stream_state_table.c.current_sequence).where(
                    device_stream_state_table.c.id == 1
                )
            ).scalar_one()
        return int(value)

    def next_generation(self, scope_digest: str) -> int:
        with self._store.immediate_transaction() as connection:
            connection.execute(
                sqlite_insert(device_stream_generations_table)
                .values(scope_digest=scope_digest, generation=0)
                .on_conflict_do_nothing()
            )
            connection.execute(
                update(device_stream_generations_table)
                .where(device_stream_generations_table.c.scope_digest == scope_digest)
                .values(generation=device_stream_generations_table.c.generation + 1)
            )
            value = connection.execute(
                select(device_stream_generations_table.c.generation).where(
                    device_stream_generations_table.c.scope_digest == scope_digest
                )
            ).scalar_one()
        return int(value)


__all__ = ["SqliteDeviceStreamLedger"]
