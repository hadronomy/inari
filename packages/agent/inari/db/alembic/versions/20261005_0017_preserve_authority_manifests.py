"""Retain signed manifest membership with each authority revision."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20261005_0017"
down_revision = "20260906_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A digest cannot recover membership. Historical revisions remain audit data.
    op.add_column("device_authority_revisions", sa.Column("manifest", sa.Text()))


def downgrade() -> None:
    op.drop_column("device_authority_revisions", "manifest")
