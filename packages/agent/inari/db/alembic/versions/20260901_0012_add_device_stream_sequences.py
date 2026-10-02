"""Add durable Device Stream sequences and fencing generations."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260901_0012"
down_revision = "20260901_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_stream_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("current_sequence", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_device_stream_state_singleton"),
        sa.CheckConstraint(
            "current_sequence BETWEEN 0 AND 9007199254740991",
            name="ck_device_stream_state_sequence",
        ),
    )
    op.execute(
        sa.text("INSERT INTO device_stream_state (id, current_sequence) VALUES (1, 0)")
    )
    op.create_table(
        "device_stream_generations",
        sa.Column("scope_digest", sa.String(), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(scope_digest) BETWEEN 8 AND 256",
            name="ck_device_stream_generations_scope",
        ),
        sa.CheckConstraint(
            "generation BETWEEN 0 AND 9007199254740991",
            name="ck_device_stream_generations_value",
        ),
    )


def downgrade() -> None:
    op.drop_table("device_stream_generations")
    op.drop_table("device_stream_state")
