"""Drop obsolete gateway outbox acknowledgement fields.

Revision ID: 20260418_0003
Revises: 20260418_0002
Create Date: 2026-04-18 18:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260418_0003"
down_revision = "20260418_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE gateway_outbox
        SET state = 'sent'
        WHERE state = 'acknowledged'
        """
    )
    with op.batch_alter_table("gateway_outbox") as batch_op:
        batch_op.drop_column("acknowledged_at")


def downgrade() -> None:
    with op.batch_alter_table("gateway_outbox") as batch_op:
        batch_op.add_column(sa.Column("acknowledged_at", sa.String(), nullable=True))
