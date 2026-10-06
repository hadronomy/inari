"""Retain idempotent Device Test execution and signed physical answers."""

from alembic import op
import sqlalchemy as sa


revision = "20261006_0018"
down_revision = "20261005_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_tests",
        sa.Column("record_id", sa.String(), primary_key=True),
        *[
            sa.Column(name, sa.String(), nullable=False)
            for name in (
                "test_id",
                "database",
                "company_id",
                "organization_id",
                "site_id",
                "pos_configuration_id",
                "paired_client_id",
                "actor_id",
                "device_id",
                "binding_revision_id",
                "state",
                "accepted_at",
                "io_deadline",
            )
        ],
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        *[
            sa.Column(name, sa.String())
            for name in (
                "started_at",
                "terminal_at",
                "marker_id",
                "output_evidence",
                "platform_job_id",
                "error_code",
            )
        ],
        *[sa.Column(name, sa.Text()) for name in ("graph", "checks", "signed_result")],
        sa.CheckConstraint(
            "length(fingerprint) = 32 AND state_version > 0",
            name="ck_device_tests_identity",
        ),
        sa.CheckConstraint(
            "state IN ('accepted', 'in_progress', 'awaiting_checks', 'outcome_unknown', 'failed_environment', 'completed')",
            name="ck_device_tests_state",
        ),
        sa.CheckConstraint(
            "(started_at IS NULL AND marker_id IS NULL) OR (started_at IS NOT NULL AND marker_id IS NOT NULL AND graph IS NOT NULL)",
            name="ck_device_tests_marker",
        ),
        sa.CheckConstraint(
            "state != 'completed' OR (checks IS NOT NULL AND signed_result IS NOT NULL AND terminal_at IS NOT NULL)",
            name="ck_device_tests_result",
        ),
        sa.UniqueConstraint(
            "database",
            "company_id",
            "organization_id",
            "site_id",
            "pos_configuration_id",
            "test_id",
            name="uq_device_tests_scope",
        ),
    )
    op.create_index(
        "idx_device_tests_execution",
        "device_tests",
        ["device_id", "state", "io_deadline"],
    )


def downgrade() -> None:
    op.drop_index("idx_device_tests_execution", table_name="device_tests")
    op.drop_table("device_tests")
