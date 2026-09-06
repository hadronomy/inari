"""Request Controller storage receipts for previously published Print Job evidence."""

from alembic import op


revision = "20260906_0016"
down_revision = "20260906_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE gateway_outbox
        SET state = 'pending', sent_at = NULL, last_error = NULL,
            updated_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
        WHERE state = 'sent'
          AND message_type = 'agent.runtime.event'
          AND json_extract(payload_json, '$.event.resource_kind') = 'print_job'
          AND json_type(payload_json, '$.event.payload.state_envelope') = 'text'
    """)


def downgrade() -> None:
    pass
