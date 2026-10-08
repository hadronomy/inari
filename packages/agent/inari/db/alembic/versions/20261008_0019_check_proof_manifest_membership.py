"""Check signed manifest membership independently of installation provenance."""

from alembic import op
from sqlalchemy import text


revision = "20261008_0019"
down_revision = "20261006_0018"
branch_labels = None
depends_on = None

_TRIGGER = "ck_device_work_authority_proofs_graph"
_PROVENANCE = (
    "              AND binding.authority_revision_id = NEW.authority_revision_id\n",
    "              AND matrix.authority_revision_id = NEW.authority_revision_id\n",
)
_MEMBERSHIP = (
    "".join(
        f"""
              AND EXISTS (
                  SELECT 1 FROM json_each(authority.manifest, '$.{group}') AS member
                  WHERE json_extract(member.value, '$.{body}.{identifier}') = NEW.{proof_id}
                    AND json_extract(member.value, '$.digest') = lower(hex(NEW.{proof_digest}))
              )"""
        for group, body, identifier, proof_id, proof_digest in (
            (
                "bindings",
                "revision",
                "revision_id",
                "binding_revision_id",
                "binding_revision_digest",
            ),
            (
                "profiles",
                "profile",
                "profile_id",
                "driver_profile_id",
                "driver_profile_digest",
            ),
            (
                "certification_rows",
                "row",
                "row_id",
                "matrix_row_id",
                "matrix_row_digest",
            ),
            (
                "evidence",
                "evidence",
                "evidence_id",
                "test_evidence_id",
                "test_evidence_digest",
            ),
        )
    )
    + """
              AND EXISTS (
                  SELECT 1 FROM json_each(authority.manifest, '$.activations') AS member
                  WHERE json_extract(member.value, '$.revision_id') = NEW.binding_revision_id
                    AND json_extract(member.value, '$.evidence_id') = NEW.test_evidence_id
              )"""
)
_ANCHOR = "              AND evidence.matrix_row_id = NEW.matrix_row_id"


def _sql() -> str:
    return (
        op.get_bind()
        .execute(
            text("SELECT sql FROM sqlite_master WHERE type = :type AND name = :name"),
            {"type": "trigger", "name": _TRIGGER},
        )
        .scalar_one()
    )


def _replace(sql: str) -> None:
    op.execute(f"DROP TRIGGER {_TRIGGER}")
    op.execute(sql)


def upgrade() -> None:
    sql = _sql()
    for condition in _PROVENANCE:
        if sql.count(condition) != 1:
            raise RuntimeError("The authority proof trigger has an unexpected shape.")
        sql = sql.replace(condition, "")
    if sql.count(_ANCHOR) != 1:
        raise RuntimeError("The authority proof trigger has an unexpected shape.")
    _replace(sql.replace(_ANCHOR, _ANCHOR + _MEMBERSHIP))


def downgrade() -> None:
    sql = _sql()
    if sql.count(_MEMBERSHIP) != 1:
        raise RuntimeError("The authority proof trigger has an unexpected shape.")
    sql = sql.replace(_MEMBERSHIP, "")
    _replace(sql.replace(_ANCHOR, "".join(_PROVENANCE) + _ANCHOR))
