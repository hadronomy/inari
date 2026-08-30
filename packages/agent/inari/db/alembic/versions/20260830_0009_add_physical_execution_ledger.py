"""Add the fenced physical execution ledger and exact Grant references."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260830_0009"
down_revision = "20260830_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE gateway_inbound_commands SET job_id = NULL "
        "WHERE job_id IN (SELECT id FROM jobs WHERE kind = 'print_job')"
    )
    op.execute(
        "DELETE FROM job_events "
        "WHERE job_id IN (SELECT id FROM jobs WHERE kind = 'print_job')"
    )
    op.execute(
        "DELETE FROM job_attempts "
        "WHERE job_id IN (SELECT id FROM jobs WHERE kind = 'print_job')"
    )
    op.execute("DELETE FROM jobs WHERE kind = 'print_job'")

    for table_name in ("device_work_admissions", "device_work_authority_proofs"):
        op.add_column(table_name, sa.Column("grant_id", sa.String(), nullable=True))
        op.add_column(
            table_name, sa.Column("grant_pairing_id", sa.String(), nullable=True)
        )
        op.add_column(
            table_name, sa.Column("grant_generation", sa.Integer(), nullable=True)
        )
        op.add_column(
            table_name,
            sa.Column("grant_authorization_digest", sa.LargeBinary(), nullable=True),
        )

    op.drop_index(
        "uq_spool_reservations_execution_owner_active",
        table_name="spool_reservations",
    )
    op.create_index(
        "uq_spool_reservations_execution_device_active",
        "spool_reservations",
        ["device_id"],
        unique=True,
        sqlite_where=sa.text("reservation_kind = 'execution_temp' AND state = 'held'"),
    )

    op.create_table(
        "physical_execution_attempts",
        sa.Column("attempt_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("lease_id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("owner_generation", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("lease_expires_at", sa.String(), nullable=False),
        sa.Column("marker_id", sa.String(), nullable=True),
        sa.Column("marker_at", sa.String(), nullable=True),
        sa.Column("marker_sequence", sa.Integer(), nullable=True),
        sa.Column("io_permission_issued", sa.Boolean(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=False),
        sa.Column("platform_job_id", sa.String(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("finished_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "length(attempt_id) BETWEEN 1 AND 128 AND length(job_id) BETWEEN 1 AND 128 AND "
            "length(lease_id) BETWEEN 1 AND 128 AND length(owner_id) BETWEEN 1 AND 256 AND "
            "owner_generation > 0 AND attempt_number > 0 AND state_version > 0 AND "
            "length(execution_id) BETWEEN 1 AND 128",
            name="ck_physical_execution_attempts_identity",
        ),
        sa.CheckConstraint(
            "phase IN ('claimed', 'prepared', 'marker_committed', 'permission_delivered', 'finished', 'retryable', 'recovered')",
            name="ck_physical_execution_attempts_phase",
        ),
        sa.CheckConstraint(
            "(marker_id IS NULL AND marker_at IS NULL AND marker_sequence IS NULL) OR "
            "(marker_id IS NOT NULL AND marker_at IS NOT NULL AND marker_sequence IS NOT NULL AND marker_sequence > 0)",
            name="ck_physical_execution_attempts_marker",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["public_print_jobs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.UniqueConstraint("lease_id"),
    )
    op.create_index(
        "idx_physical_execution_attempts_job_phase",
        "physical_execution_attempts",
        ["job_id", "phase"],
    )
    op.create_index(
        "idx_physical_execution_attempts_owner",
        "physical_execution_attempts",
        ["owner_id", "owner_generation", "phase"],
    )
    op.create_index(
        "uq_physical_execution_attempts_active_job",
        "physical_execution_attempts",
        ["job_id"],
        unique=True,
        sqlite_where=sa.text(
            "phase IN ('claimed', 'prepared', 'marker_committed', 'permission_delivered')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_physical_execution_attempts_active_job",
        table_name="physical_execution_attempts",
    )
    op.drop_index(
        "idx_physical_execution_attempts_owner",
        table_name="physical_execution_attempts",
    )
    op.drop_index(
        "idx_physical_execution_attempts_job_phase",
        table_name="physical_execution_attempts",
    )
    op.drop_table("physical_execution_attempts")
    op.drop_index(
        "uq_spool_reservations_execution_device_active",
        table_name="spool_reservations",
    )
    op.create_index(
        "uq_spool_reservations_execution_owner_active",
        "spool_reservations",
        ["job_id", "owner_id", "owner_generation"],
        unique=True,
        sqlite_where=sa.text("reservation_kind = 'execution_temp' AND state = 'held'"),
    )
    for table_name in ("device_work_authority_proofs", "device_work_admissions"):
        op.drop_column(table_name, "grant_authorization_digest")
        op.drop_column(table_name, "grant_generation")
        op.drop_column(table_name, "grant_pairing_id")
        op.drop_column(table_name, "grant_id")
