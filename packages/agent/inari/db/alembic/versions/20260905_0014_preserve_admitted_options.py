"""Preserve the canonical options authorized for physical execution."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260905_0014"
down_revision = "20260904_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    active = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM device_work_admissions "
                "WHERE state IN ('staging', 'finalizing')) OR "
                "EXISTS (SELECT 1 FROM public_print_jobs "
                "WHERE state IN ('accepted', 'in_progress'))"
            )
        )
        .scalar_one()
    )
    if active:
        raise RuntimeError(
            "Drain Device Work before upgrading. Active admissions do not contain "
            "the canonical options required for physical execution."
        )
    # Historical outcomes retain their digest. A digest cannot recover options.
    op.add_column(
        "device_work_admissions",
        sa.Column("normalized_options", sa.LargeBinary(), nullable=True),
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_work_admissions_options_insert
        BEFORE INSERT ON device_work_admissions
        WHEN NEW.normalized_options IS NULL
          OR typeof(NEW.normalized_options) != 'blob'
          OR NOT json_valid(CAST(NEW.normalized_options AS TEXT))
          OR json_type(CAST(NEW.normalized_options AS TEXT)) != 'object'
        BEGIN
            SELECT RAISE(ABORT, 'admission requires canonical options');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_work_admissions_options_immutable
        BEFORE UPDATE OF normalized_options ON device_work_admissions
        BEGIN
            SELECT RAISE(ABORT, 'admission options are immutable');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER ck_device_work_admissions_options_immutable")
    op.execute("DROP TRIGGER ck_device_work_admissions_options_insert")
    op.drop_column("device_work_admissions", "normalized_options")
