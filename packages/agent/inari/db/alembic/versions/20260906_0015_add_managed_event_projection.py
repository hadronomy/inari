"""Bind managed publications and Print Job cursors to their enrollment scope."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260906_0015"
down_revision = "20260905_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gateway_outbox", sa.Column("recipient_scope", sa.Text()))
    op.execute(
        sa.text("""
        UPDATE gateway_outbox SET recipient_scope = (
            SELECT json_object(
                'agent_id', json_extract(payload_json, '$.payload.authenticated_data.agent_id'),
                'organization_id', json_extract(payload_json, '$.payload.authenticated_data.organization_id'),
                'site_id', json_extract(payload_json, '$.payload.authenticated_data.site_id')
            ) FROM gateway_inbound_commands
            WHERE command_id = gateway_outbox.correlation_id
              AND message_type = 'controller.command.dispatch_device_work'
        ) WHERE correlation_id IN (
            SELECT command_id FROM gateway_inbound_commands
            WHERE message_type = 'controller.command.dispatch_device_work'
        )
    """)
    )
    op.create_table(
        "gateway_print_job_cursors",
        sa.Column("recipient_scope", sa.Text(), primary_key=True),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "last_sequence BETWEEN 0 AND 9007199254740991",
            name="ck_gateway_print_job_cursor_sequence",
        ),
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_gateway_inbound_managed_work ON gateway_inbound_commands (
            json_extract(payload_json, '$.payload.managed_work_id')
        ) WHERE message_type = 'controller.command.dispatch_device_work'
    """)
    )


def downgrade() -> None:
    op.drop_index(
        "idx_gateway_inbound_managed_work", table_name="gateway_inbound_commands"
    )
    op.drop_table("gateway_print_job_cursors")
    op.drop_column("gateway_outbox", "recipient_scope")
