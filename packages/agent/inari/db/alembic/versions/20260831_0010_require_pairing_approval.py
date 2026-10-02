"""Require Device Manager approval before Client Pairing admission."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260831_0010"
down_revision = "20260830_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _replace_state_guard(allow_pending_completion=False)


def downgrade() -> None:
    _replace_state_guard(allow_pending_completion=True)


def _replace_state_guard(*, allow_pending_completion: bool) -> None:
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_client_trust_pairing_requests_state")
    )
    pending_states = "'approved', 'denied', 'canceled', 'expired'"
    if allow_pending_completion:
        pending_states = f"{pending_states}, 'completed'"
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_client_trust_pairing_requests_state
            BEFORE UPDATE OF state ON client_trust_pairing_requests
            WHEN NOT (
                OLD.state = NEW.state OR
                (OLD.state = 'pending' AND NEW.state IN ({pending_states})) OR
                (OLD.state = 'approved' AND NEW.state IN ('completed', 'canceled', 'expired'))
            )
            BEGIN
                SELECT RAISE(ABORT, 'Pairing Request state transition is invalid');
            END
            """
        )
    )
