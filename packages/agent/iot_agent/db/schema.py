from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine, URL
from sqlalchemy.pool import NullPool

metadata = MetaData()

devices_table = Table(
    "devices",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("driver_key", String, nullable=False),
    Column("name", String, nullable=False),
    Column("connection_state", String, nullable=False),
    Column("first_seen_at", String, nullable=False),
    Column("last_seen_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("is_default", Boolean, nullable=False),
    Column("preferred_transport", String),
    Column("capabilities_json", Text, nullable=False),
    Column("metadata_json", Text, nullable=False),
)

device_events_table = Table(
    "device_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("device_id", String, ForeignKey("devices.id"), nullable=False),
    Column("event_type", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("occurred_at", String, nullable=False),
)
Index(
    "idx_device_events_device_id",
    device_events_table.c.device_id,
    device_events_table.c.sequence.desc(),
)

jobs_table = Table(
    "jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("operation", String, nullable=False),
    Column("device_id", String, nullable=False),
    Column("device_kind", String, nullable=False),
    Column("device_name", String, nullable=False),
    Column("state", String, nullable=False),
    Column("request_json", Text, nullable=False),
    Column("request_metadata_json", Text, nullable=False),
    Column("content_kind", String),
    Column("command_kind", String),
    Column("attempt_count", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("queued_at", String, nullable=False),
    Column("next_run_at", String, nullable=False),
    Column("started_at", String),
    Column("finished_at", String),
    Column("lease_expires_at", String),
    Column("result_json", Text),
    Column("last_error_code", String),
    Column("last_error_detail", Text),
)
Index(
    "idx_jobs_state_next_run_at",
    jobs_table.c.state,
    jobs_table.c.next_run_at,
    jobs_table.c.created_at,
)
Index("idx_jobs_device_id", jobs_table.c.device_id, jobs_table.c.created_at)

job_attempts_table = Table(
    "job_attempts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", String, ForeignKey("jobs.id"), nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("state", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("finished_at", String),
    Column("error_code", String),
    Column("error_detail", Text),
    Column("result_json", Text),
)
Index(
    "idx_job_attempts_job_id",
    job_attempts_table.c.job_id,
    job_attempts_table.c.attempt_number.desc(),
)

job_events_table = Table(
    "job_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("job_id", String, ForeignKey("jobs.id"), nullable=False),
    Column("event_type", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("occurred_at", String, nullable=False),
)
Index(
    "idx_job_events_job_id",
    job_events_table.c.job_id,
    job_events_table.c.sequence.desc(),
)

gateway_inbound_commands_table = Table(
    "gateway_inbound_commands",
    metadata,
    Column("command_id", String, primary_key=True),
    Column("message_id", String, nullable=False),
    Column("sequence", Integer),
    Column("message_type", String, nullable=False),
    Column("state", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("response_json", Text),
    Column("error_code", String),
    Column("error_detail", Text),
    Column("job_id", String),
    Column("received_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
Index(
    "idx_gateway_inbound_job_id",
    gateway_inbound_commands_table.c.job_id,
    gateway_inbound_commands_table.c.updated_at.desc(),
)
Index(
    "idx_gateway_inbound_sequence",
    gateway_inbound_commands_table.c.sequence,
    unique=True,
    sqlite_where=gateway_inbound_commands_table.c.sequence.is_not(None),
)

gateway_outbox_table = Table(
    "gateway_outbox",
    metadata,
    Column("message_id", String, primary_key=True),
    Column("message_type", String, nullable=False),
    Column("state", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("correlation_id", String),
    Column("dedupe_key", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("sent_at", String),
    Column("acknowledged_at", String),
    Column("last_error", Text),
)
Index(
    "idx_gateway_outbox_dedupe_key",
    gateway_outbox_table.c.dedupe_key,
    unique=True,
    sqlite_where=gateway_outbox_table.c.dedupe_key.is_not(None),
)
Index(
    "idx_gateway_outbox_state_created_at",
    gateway_outbox_table.c.state,
    gateway_outbox_table.c.created_at,
)

MANAGED_TABLE_NAMES = frozenset(
    {
        devices_table.name,
        device_events_table.name,
        jobs_table.name,
        job_attempts_table.name,
        job_events_table.name,
        gateway_inbound_commands_table.name,
        gateway_outbox_table.name,
    }
)


def create_database_engine(database_path: Path) -> Engine:
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(database_path)),
        connect_args={"timeout": 30, "check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
        finally:
            cursor.close()

    return engine
