"""Add Managed Work admission and dispatch replay state."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260904_0013"
down_revision = "20260901_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_work_admissions",
        sa.Column("managed_work_id", sa.String(), nullable=True),
    )
    op.create_index(
        "uq_device_work_admissions_managed_work_id",
        "device_work_admissions",
        ["managed_work_id"],
        unique=True,
        sqlite_where=sa.text("managed_work_id IS NOT NULL"),
    )
    op.add_column(
        "gateway_inbound_commands",
        sa.Column("dispatch_epoch", sa.Integer(), nullable=True),
    )
    op.create_index(
        "uq_gateway_inbound_dispatch_epoch_sequence",
        "gateway_inbound_commands",
        ["dispatch_epoch", "sequence"],
        unique=True,
        sqlite_where=sa.text("dispatch_epoch IS NOT NULL"),
    )
    op.create_table(
        "gateway_managed_dispatch_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dispatch_epoch", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "id = 1", name="ck_gateway_managed_dispatch_state_singleton"
        ),
        sa.CheckConstraint(
            "dispatch_epoch > 0 AND last_sequence >= 0",
            name="ck_gateway_managed_dispatch_state_position",
        ),
    )


def downgrade() -> None:
    op.drop_table("gateway_managed_dispatch_state")
    op.drop_index(
        "uq_gateway_inbound_dispatch_epoch_sequence",
        table_name="gateway_inbound_commands",
    )
    op.drop_column("gateway_inbound_commands", "dispatch_epoch")
    op.drop_index(
        "uq_device_work_admissions_managed_work_id",
        table_name="device_work_admissions",
    )
    op.drop_column("device_work_admissions", "managed_work_id")
