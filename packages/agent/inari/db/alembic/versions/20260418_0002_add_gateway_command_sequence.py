"""Add controller command sequence tracking for gateway resume semantics.

Revision ID: 20260418_0002
Revises: 20260414_0001
Create Date: 2026-04-18 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260418_0002"
down_revision = "20260414_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gateway_inbound_commands") as batch_op:
        batch_op.add_column(sa.Column("sequence", sa.Integer(), nullable=True))

    op.create_index(
        "idx_gateway_inbound_sequence",
        "gateway_inbound_commands",
        ["sequence"],
        unique=True,
        sqlite_where=sa.text("sequence IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_gateway_inbound_sequence", table_name="gateway_inbound_commands")
    with op.batch_alter_table("gateway_inbound_commands") as batch_op:
        batch_op.drop_column("sequence")
