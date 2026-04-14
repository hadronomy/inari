"""Create the initial runtime and gateway persistence schema.

Revision ID: 20260414_0001
Revises:
Create Date: 2026-04-14 09:30:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260414_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("driver_key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("connection_state", sa.String(), nullable=False),
        sa.Column("first_seen_at", sa.String(), nullable=False),
        sa.Column("last_seen_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("preferred_transport", sa.String(), nullable=True),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "device_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"]),
        sa.PrimaryKeyConstraint("sequence"),
    )
    op.create_index(
        "idx_device_events_device_id",
        "device_events",
        ["device_id", sa.text("sequence DESC")],
        unique=False,
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("device_kind", sa.String(), nullable=False),
        sa.Column("device_name", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("request_metadata_json", sa.Text(), nullable=False),
        sa.Column("content_kind", sa.String(), nullable=True),
        sa.Column("command_kind", sa.String(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("queued_at", sa.String(), nullable=False),
        sa.Column("next_run_at", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("finished_at", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.String(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.String(), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_jobs_state_next_run_at",
        "jobs",
        ["state", "next_run_at", "created_at"],
        unique=False,
    )
    op.create_index("idx_jobs_device_id", "jobs", ["device_id", "created_at"], unique=False)

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("finished_at", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_job_attempts_job_id",
        "job_attempts",
        ["job_id", sa.text("attempt_number DESC")],
        unique=False,
    )

    op.create_table(
        "job_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("sequence"),
    )
    op.create_index(
        "idx_job_events_job_id",
        "job_events",
        ["job_id", sa.text("sequence DESC")],
        unique=False,
    )

    op.create_table(
        "gateway_inbound_commands",
        sa.Column("command_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("message_type", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("received_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("command_id"),
    )
    op.create_index(
        "idx_gateway_inbound_job_id",
        "gateway_inbound_commands",
        ["job_id", sa.text("updated_at DESC")],
        unique=False,
    )

    op.create_table(
        "gateway_outbox",
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("message_type", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(), nullable=True),
        sa.Column("dedupe_key", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("sent_at", sa.String(), nullable=True),
        sa.Column("acknowledged_at", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        "idx_gateway_outbox_dedupe_key",
        "gateway_outbox",
        ["dedupe_key"],
        unique=True,
        sqlite_where=sa.text("dedupe_key IS NOT NULL"),
    )
    op.create_index(
        "idx_gateway_outbox_state_created_at",
        "gateway_outbox",
        ["state", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_gateway_outbox_state_created_at", table_name="gateway_outbox")
    op.drop_index("idx_gateway_outbox_dedupe_key", table_name="gateway_outbox")
    op.drop_table("gateway_outbox")

    op.drop_index("idx_gateway_inbound_job_id", table_name="gateway_inbound_commands")
    op.drop_table("gateway_inbound_commands")

    op.drop_index("idx_job_events_job_id", table_name="job_events")
    op.drop_table("job_events")

    op.drop_index("idx_job_attempts_job_id", table_name="job_attempts")
    op.drop_table("job_attempts")

    op.drop_index("idx_jobs_device_id", table_name="jobs")
    op.drop_index("idx_jobs_state_next_run_at", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("idx_device_events_device_id", table_name="device_events")
    op.drop_table("device_events")

    op.drop_table("devices")
