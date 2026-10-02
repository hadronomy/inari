"""Add the durable, content-free Drawer Intent ledger."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260901_0011"
down_revision = "20260831_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drawer_intents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("intent_id", sa.String(), nullable=False),
        sa.Column("database", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("site_id", sa.String(), nullable=False),
        sa.Column("pos_configuration_id", sa.String(), nullable=False),
        sa.Column("paired_client_id", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("binding_revision_id", sa.String(), nullable=False),
        sa.Column("pos_session_id", sa.String(), nullable=False),
        sa.Column("action_sequence", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("contract_major", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("accepted_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("started_at", sa.String()),
        sa.Column("terminal_at", sa.String()),
        sa.Column("error_code", sa.String()),
        sa.Column("message_key", sa.String()),
        sa.Column("printer_name", sa.String()),
        sa.Column("transport", sa.String()),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(id) BETWEEN 1 AND 128 AND length(intent_id) BETWEEN 1 AND 256 AND "
            "length(database) BETWEEN 1 AND 256 AND length(organization_id) BETWEEN 1 AND 256 AND "
            "length(site_id) BETWEEN 1 AND 256 AND length(pos_configuration_id) BETWEEN 1 AND 256 AND "
            "length(paired_client_id) BETWEEN 1 AND 256 AND length(actor_id) BETWEEN 1 AND 256 AND "
            "length(device_id) BETWEEN 1 AND 256 AND length(binding_revision_id) BETWEEN 1 AND 256 AND "
            "length(pos_session_id) BETWEEN 1 AND 256 AND "
            "action_sequence BETWEEN 1 AND 9007199254740991 AND "
            "reason IN ('payment', 'manual_open') AND contract_major = 1 AND state_version >= 1",
            name="ck_drawer_intents_identity",
        ),
        sa.CheckConstraint(
            "length(fingerprint) = 32", name="ck_drawer_intents_fingerprint"
        ),
        sa.CheckConstraint(
            "state IN ('accepted', 'in_progress', 'succeeded', 'outcome_unknown', 'failed')",
            name="ck_drawer_intents_state",
        ),
        sa.CheckConstraint(
            "expires_at > accepted_at", name="ck_drawer_intents_retention"
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= accepted_at",
            name="ck_drawer_intents_started_at",
        ),
        sa.CheckConstraint(
            "terminal_at IS NULL OR terminal_at >= accepted_at",
            name="ck_drawer_intents_terminal_at",
        ),
        sa.CheckConstraint(
            "(state = 'accepted' AND started_at IS NULL AND terminal_at IS NULL AND error_code IS NULL AND message_key IS NULL) OR "
            "(state = 'in_progress' AND started_at IS NOT NULL AND terminal_at IS NULL AND error_code IS NULL AND message_key IS NULL) OR "
            "(state = 'succeeded' AND started_at IS NOT NULL AND terminal_at IS NOT NULL AND error_code IS NULL AND message_key IS NULL) OR "
            "(state = 'outcome_unknown' AND started_at IS NOT NULL AND terminal_at IS NOT NULL AND error_code IS NOT NULL AND message_key IS NOT NULL) OR "
            "(state = 'failed' AND started_at IS NULL AND terminal_at IS NOT NULL AND error_code IS NOT NULL AND message_key IS NOT NULL)",
            name="ck_drawer_intents_lifecycle",
        ),
    )
    op.create_index(
        "uq_drawer_intents_scope_identity",
        "drawer_intents",
        (
            "database",
            "organization_id",
            "site_id",
            "pos_configuration_id",
            "paired_client_id",
            "intent_id",
        ),
        unique=True,
    )
    op.create_index(
        "uq_drawer_intents_action",
        "drawer_intents",
        (
            "database",
            "organization_id",
            "site_id",
            "pos_configuration_id",
            "pos_session_id",
            "actor_id",
            "device_id",
            "action_sequence",
        ),
        unique=True,
    )
    op.create_index(
        "idx_drawer_intents_scope_accepted_at",
        "drawer_intents",
        (
            "database",
            "organization_id",
            "site_id",
            "pos_configuration_id",
            "paired_client_id",
            "accepted_at",
        ),
    )
    op.create_index("idx_drawer_intents_expiry", "drawer_intents", ("expires_at",))


def downgrade() -> None:
    op.drop_index("idx_drawer_intents_expiry", table_name="drawer_intents")
    op.drop_index("idx_drawer_intents_scope_accepted_at", table_name="drawer_intents")
    op.drop_index("uq_drawer_intents_action", table_name="drawer_intents")
    op.drop_index("uq_drawer_intents_scope_identity", table_name="drawer_intents")
    op.drop_table("drawer_intents")
