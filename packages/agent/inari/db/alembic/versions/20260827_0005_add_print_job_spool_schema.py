"""Create the durable Print Job and Device Spool metadata tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260827_0005"
down_revision = "20260712_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_print_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("admission_id", sa.String(), nullable=False),
        sa.Column("intent_id", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("scope_kind", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("site_id", sa.String(), nullable=False),
        sa.Column("pos_configuration_id", sa.String(), nullable=True),
        sa.Column("paired_client_id", sa.String(), nullable=True),
        sa.Column("origin_kind", sa.String(), nullable=False),
        sa.Column("origin_json", sa.Text(), nullable=False),
        sa.Column("managed_work_id", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("terminal_at", sa.String(), nullable=True),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("message_key", sa.String(), nullable=True),
        sa.Column("confirmation_evidence", sa.String(), nullable=True),
        sa.Column("contract_version", sa.String(), nullable=False),
        sa.CheckConstraint(
            "state_version > 0", name="ck_public_print_jobs_state_version"
        ),
        sa.CheckConstraint(
            "scope_kind IN ('paired_client', 'device_manager')",
            name="ck_public_print_jobs_scope_kind",
        ),
        sa.CheckConstraint(
            "length(organization_id) > 0 AND length(site_id) > 0",
            name="ck_public_print_jobs_scope_identity",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'paired_client' AND pos_configuration_id IS NOT NULL AND paired_client_id IS NOT NULL) OR "
            "(scope_kind = 'device_manager' AND ((pos_configuration_id IS NULL AND paired_client_id IS NULL) OR (pos_configuration_id IS NOT NULL AND paired_client_id IS NOT NULL)))",
            name="ck_public_print_jobs_scope_shape",
        ),
        sa.CheckConstraint(
            "origin_kind IN ('pos', 'preparation', 'report')",
            name="ck_public_print_jobs_origin_kind",
        ),
        sa.CheckConstraint(
            "json_valid(origin_json) AND json_type(origin_json) = 'object' AND origin_json = json(origin_json) AND json_extract(origin_json, '$.receipt_payload') IS NULL AND json_extract(origin_json, '$.raw_driver_message') IS NULL AND json_extract(origin_json, '$.document_bytes') IS NULL",
            name="ck_public_print_jobs_origin_json",
        ),
        sa.CheckConstraint(
            "length(CAST(origin_json AS BLOB)) <= 16384",
            name="ck_public_print_jobs_origin_json_size",
        ),
        sa.CheckConstraint(
            "expires_at > accepted_at", name="ck_public_print_jobs_expires_at"
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= accepted_at",
            name="ck_public_print_jobs_started_at",
        ),
        sa.CheckConstraint(
            "terminal_at IS NULL OR terminal_at >= accepted_at",
            name="ck_public_print_jobs_terminal_at",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR terminal_at IS NULL OR terminal_at >= started_at",
            name="ck_public_print_jobs_lifecycle_order",
        ),
        sa.CheckConstraint(
            "confirmation_evidence IS NULL OR confirmation_evidence IN ('device', 'spooler', 'transport')",
            name="ck_public_print_jobs_evidence",
        ),
        sa.CheckConstraint(
            "(state = 'accepted' AND started_at IS NULL AND terminal_at IS NULL AND confirmation_evidence IS NULL AND error_code IS NULL) OR "
            "(state = 'in_progress' AND started_at IS NOT NULL AND terminal_at IS NULL AND confirmation_evidence IS NULL AND error_code IS NULL) OR "
            "(state = 'output_confirmed' AND started_at IS NOT NULL AND terminal_at IS NOT NULL AND confirmation_evidence IS NOT NULL AND error_code IS NULL) OR "
            "(state = 'failed' AND terminal_at IS NOT NULL AND confirmation_evidence IS NULL AND error_code IS NOT NULL) OR "
            "(state = 'outcome_unknown' AND started_at IS NOT NULL AND terminal_at IS NOT NULL AND confirmation_evidence IS NULL AND error_code IS NOT NULL) OR "
            "(state IN ('expired', 'canceled') AND started_at IS NULL AND terminal_at IS NOT NULL AND confirmation_evidence IS NULL AND error_code IS NULL)",
            name="ck_public_print_jobs_lifecycle",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["admission_id"], ["device_work_admissions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_public_print_jobs_scope_accepted_at",
        "public_print_jobs",
        [
            "scope_kind",
            "organization_id",
            "site_id",
            "pos_configuration_id",
            "paired_client_id",
            "accepted_at",
        ],
    )
    op.create_index(
        "idx_public_print_jobs_state_expires_at",
        "public_print_jobs",
        ["state", "expires_at"],
    )
    op.create_index(
        "uq_public_print_jobs_intent_id",
        "public_print_jobs",
        ["intent_id"],
        unique=True,
    )
    op.create_index(
        "uq_public_print_jobs_admission_id",
        "public_print_jobs",
        ["admission_id"],
        unique=True,
    )
    op.create_index(
        "uq_public_print_jobs_managed_work_id",
        "public_print_jobs",
        ["managed_work_id"],
        unique=True,
        sqlite_where=sa.text("managed_work_id IS NOT NULL"),
    )

    op.create_table(
        "device_work_admissions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("planned_job_id", sa.String(), nullable=False),
        sa.Column("database", sa.String(), nullable=False),
        sa.Column("scope_kind", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("site_id", sa.String(), nullable=False),
        sa.Column("pos_configuration_id", sa.String(), nullable=True),
        sa.Column("paired_client_id", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("intent_id", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("deadline_at", sa.String(), nullable=False),
        sa.Column("original_size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("accepted_at", sa.String(), nullable=True),
        sa.Column("failed_at", sa.String(), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("idempotency_expires_at", sa.String(), nullable=False),
        sa.Column("content_expires_at", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("binding_revision_id", sa.String(), nullable=False),
        sa.Column("authorization_digest", sa.LargeBinary(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("normalized_options_digest", sa.LargeBinary(), nullable=False),
        sa.Column("grant_scope_digest", sa.LargeBinary(), nullable=False),
        sa.Column("origin_submission_key", sa.String(), nullable=False),
        sa.Column("origin_kind", sa.String(), nullable=False),
        sa.Column("origin_json", sa.Text(), nullable=False),
        sa.Column("contract_major", sa.Integer(), nullable=False),
        sa.Column("copy_ordinal", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "scope_kind IN ('paired_client', 'device_manager') AND length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND length(idempotency_key) BETWEEN 1 AND 512 AND length(planned_job_id) BETWEEN 1 AND 128 AND length(database) BETWEEN 1 AND 256 AND length(actor_id) BETWEEN 1 AND 256 AND length(binding_revision_id) BETWEEN 1 AND 256 AND length(origin_submission_key) BETWEEN 1 AND 512",
            name="ck_device_work_admissions_identity",
        ),
        sa.CheckConstraint(
            "(scope_kind = 'paired_client' AND pos_configuration_id IS NOT NULL AND paired_client_id IS NOT NULL) OR "
            "(scope_kind = 'device_manager' AND ((pos_configuration_id IS NULL AND paired_client_id IS NULL) OR (pos_configuration_id IS NOT NULL AND paired_client_id IS NOT NULL)))",
            name="ck_device_work_admissions_scope_shape",
        ),
        sa.CheckConstraint(
            "length(fingerprint) = 32", name="ck_device_work_admissions_fingerprint"
        ),
        sa.CheckConstraint(
            "length(authorization_digest) = 32 AND length(normalized_options_digest) = 32 AND length(grant_scope_digest) = 32",
            name="ck_device_work_admissions_manifest_digests",
        ),
        sa.CheckConstraint(
            "length(operation) BETWEEN 1 AND 64 AND length(media_type) BETWEEN 1 AND 128",
            name="ck_device_work_admissions_manifest_operation",
        ),
        sa.CheckConstraint(
            "origin_kind IN ('pos', 'preparation', 'report') AND json_valid(origin_json) AND json_type(origin_json) = 'object' AND origin_json = json(origin_json) AND json_extract(origin_json, '$.receipt_payload') IS NULL AND json_extract(origin_json, '$.raw_driver_message') IS NULL AND json_extract(origin_json, '$.document_bytes') IS NULL AND length(CAST(origin_json AS BLOB)) <= 16384",
            name="ck_device_work_admissions_manifest_origin",
        ),
        sa.CheckConstraint(
            "contract_major > 0 AND copy_ordinal >= 0",
            name="ck_device_work_admissions_manifest_version",
        ),
        sa.CheckConstraint(
            "length(device_id) > 0 AND length(intent_id) > 0",
            name="ck_device_work_admissions_work_identity",
        ),
        sa.CheckConstraint(
            "original_size_bytes > 0", name="ck_device_work_admissions_original_size"
        ),
        sa.CheckConstraint(
            "deadline_at > created_at AND content_expires_at > created_at",
            name="ck_device_work_admissions_retention",
        ),
        sa.CheckConstraint(
            "(state IN ('staging', 'finalizing') AND job_id IS NULL AND accepted_at IS NULL AND failed_at IS NULL AND failure_code IS NULL AND idempotency_expires_at > created_at) OR "
            "(state = 'accepted' AND job_id IS NOT NULL AND job_id = planned_job_id AND accepted_at IS NOT NULL AND failed_at IS NULL AND failure_code IS NULL AND idempotency_expires_at > accepted_at AND content_expires_at > accepted_at) OR "
            "(state = 'aborted' AND job_id IS NULL AND accepted_at IS NULL AND failed_at IS NOT NULL AND failure_code IN ('expired', 'recovery_uncertain', 'service_unavailable') AND idempotency_expires_at > failed_at)",
            name="ck_device_work_admissions_lifecycle",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("planned_job_id", name="uq_device_work_admissions_planned_job_id"),
    )
    op.create_index(
        "uq_device_work_admissions_paired_idempotency",
        "device_work_admissions",
        [
            "scope_kind",
            "organization_id",
            "site_id",
            "pos_configuration_id",
            "paired_client_id",
            "idempotency_key",
        ],
        unique=True,
        sqlite_where=sa.text("scope_kind = 'paired_client'"),
    )
    op.create_index(
        "uq_device_work_admissions_manager_idempotency",
        "device_work_admissions",
        ["scope_kind", "organization_id", "site_id", "idempotency_key"],
        unique=True,
        sqlite_where=sa.text(
            "scope_kind = 'device_manager' AND pos_configuration_id IS NULL"
        ),
    )
    op.create_index(
        "uq_device_work_admissions_manager_pos_idempotency",
        "device_work_admissions",
        [
            "scope_kind",
            "organization_id",
            "site_id",
            "pos_configuration_id",
            "paired_client_id",
            "idempotency_key",
        ],
        unique=True,
        sqlite_where=sa.text(
            "scope_kind = 'device_manager' AND pos_configuration_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_device_work_admissions_origin_submission",
        "device_work_admissions",
        [
            "scope_kind",
            "organization_id",
            "site_id",
            "pos_configuration_id",
            "paired_client_id",
            "origin_submission_key",
        ],
        unique=True,
        sqlite_where=sa.text("pos_configuration_id IS NOT NULL"),
    )
    op.create_index(
        "idx_device_work_admissions_idempotency_expiry",
        "device_work_admissions",
        ["idempotency_expires_at", "state"],
    )
    op.create_index(
        "idx_device_work_admissions_job_id", "device_work_admissions", ["job_id"]
    )
    op.create_index(
        "uq_device_work_admissions_job_id",
        "device_work_admissions",
        ["job_id"],
        unique=True,
        sqlite_where=sa.text("job_id IS NOT NULL"),
    )

    op.create_table(
        "spool_root_keys",
        sa.Column("root_version", sa.Integer(), nullable=False),
        sa.Column("key_identifier", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("rotate_after", sa.String(), nullable=False),
        sa.Column("retiring_at", sa.String(), nullable=True),
        sa.Column("retired_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "root_version BETWEEN 1 AND 4294967295",
            name="ck_spool_root_keys_version",
        ),
        sa.CheckConstraint(
            "length(key_identifier) > 0", name="ck_spool_root_keys_identity"
        ),
        sa.CheckConstraint(
            "state IN ('provisioning', 'current', 'retiring', 'retired')",
            name="ck_spool_root_keys_state",
        ),
        sa.CheckConstraint(
            "(state = 'provisioning' AND retiring_at IS NULL AND retired_at IS NULL) OR (state = 'current' AND retiring_at IS NULL AND retired_at IS NULL) OR (state = 'retiring' AND retiring_at IS NOT NULL AND retired_at IS NULL) OR (state = 'retired' AND retiring_at IS NOT NULL AND retired_at IS NOT NULL)",
            name="ck_spool_root_keys_lifecycle",
        ),
        sa.CheckConstraint(
            "rotate_after > created_at", name="ck_spool_root_keys_rotation"
        ),
        sa.CheckConstraint(
            "retiring_at IS NULL OR retiring_at >= created_at",
            name="ck_spool_root_keys_retiring_at",
        ),
        sa.CheckConstraint(
            "retired_at IS NULL OR (retiring_at IS NOT NULL AND retired_at >= retiring_at)",
            name="ck_spool_root_keys_retired_at",
        ),
        sa.PrimaryKeyConstraint("root_version"),
        sa.UniqueConstraint("key_identifier"),
    )
    op.create_index(
        "uq_spool_root_keys_current",
        "spool_root_keys",
        ["state"],
        unique=True,
        sqlite_where=sa.text("state = 'current'"),
    )
    op.create_index(
        "uq_spool_root_keys_retiring",
        "spool_root_keys",
        ["state"],
        unique=True,
        sqlite_where=sa.text("state = 'retiring'"),
    )

    op.create_table(
        "spool_security_state",
        sa.Column("singleton_id", sa.Integer(), nullable=False),
        sa.Column("anchor_kind", sa.String(), nullable=False),
        sa.Column("anchor_identifier", sa.String(), nullable=False),
        sa.Column("anchor_evidence_digest", sa.LargeBinary(), nullable=False),
        sa.Column("committed_generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("quarantined_at", sa.String(), nullable=True),
        sa.Column("quarantine_reason_code", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "singleton_id = 1", name="ck_spool_security_state_singleton"
        ),
        sa.CheckConstraint(
            "anchor_kind IN ('tpm2', 'software') AND length(anchor_identifier) > 0",
            name="ck_spool_security_state_anchor_kind",
        ),
        sa.CheckConstraint(
            "length(anchor_evidence_digest) = 32",
            name="ck_spool_security_state_anchor_evidence",
        ),
        sa.CheckConstraint(
            "committed_generation > 0", name="ck_spool_security_state_generation"
        ),
        sa.CheckConstraint(
            "(status = 'ready' AND quarantined_at IS NULL AND quarantine_reason_code IS NULL) OR (status = 'quarantined' AND quarantined_at IS NOT NULL AND quarantine_reason_code IS NOT NULL)",
            name="ck_spool_security_state_status",
        ),
        sa.PrimaryKeyConstraint("singleton_id"),
    )

    op.create_table(
        "spool_job_keys",
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("root_version", sa.Integer(), nullable=False),
        sa.Column("intent_id", sa.String(), nullable=False),
        sa.Column("format_version", sa.Integer(), nullable=False),
        sa.Column("wrap_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("key_deleted_at", sa.String(), nullable=True),
        sa.CheckConstraint("length(intent_id) > 0", name="ck_spool_job_keys_intent"),
        sa.CheckConstraint(
            "format_version = 1", name="ck_spool_job_keys_format_version"
        ),
        sa.CheckConstraint(
            "length(wrap_nonce) = 12", name="ck_spool_job_keys_wrap_nonce"
        ),
        sa.CheckConstraint(
            "key_deleted_at IS NOT NULL OR length(wrapped_key) = 48",
            name="ck_spool_job_keys_wrapped_key",
        ),
        sa.CheckConstraint(
            "key_deleted_at IS NULL OR key_deleted_at >= created_at",
            name="ck_spool_job_keys_deleted_at",
        ),
        sa.CheckConstraint(
            "(key_deleted_at IS NULL AND length(wrapped_key) = 48) OR (key_deleted_at IS NOT NULL AND length(wrapped_key) = 0)",
            name="ck_spool_job_keys_key_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["public_print_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["root_version"], ["spool_root_keys.root_version"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("job_id"),
        sa.UniqueConstraint("root_version", "wrap_nonce", name="uq_spool_job_keys_root_nonce"),
    )

    op.create_table(
        "spool_admission_keys",
        sa.Column("admission_id", sa.String(), nullable=False),
        sa.Column("planned_job_id", sa.String(), nullable=False),
        sa.Column("root_version", sa.Integer(), nullable=False),
        sa.Column("intent_id", sa.String(), nullable=False),
        sa.Column("format_version", sa.Integer(), nullable=False),
        sa.Column("wrap_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(planned_job_id) BETWEEN 1 AND 128",
            name="ck_spool_admission_keys_job",
        ),
        sa.CheckConstraint(
            "length(intent_id) > 0", name="ck_spool_admission_keys_intent"
        ),
        sa.CheckConstraint(
            "format_version = 1", name="ck_spool_admission_keys_format_version"
        ),
        sa.CheckConstraint(
            "length(wrap_nonce) = 12", name="ck_spool_admission_keys_wrap_nonce"
        ),
        sa.CheckConstraint(
            "length(wrapped_key) = 48", name="ck_spool_admission_keys_wrapped_key"
        ),
        sa.ForeignKeyConstraint(
            ["admission_id"], ["device_work_admissions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["root_version"], ["spool_root_keys.root_version"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("admission_id"),
        sa.UniqueConstraint("planned_job_id"),
        sa.UniqueConstraint(
            "root_version", "wrap_nonce", name="uq_spool_admission_keys_root_nonce"
        ),
    )

    op.create_table(
        "spool_root_key_provisioning",
        sa.Column("root_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("protected_key_identifier", sa.String(), nullable=False),
        sa.Column("provisioned_at", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "state IN ('provisioning', 'available', 'quarantined')",
            name="ck_spool_root_key_provisioning_state",
        ),
        sa.CheckConstraint(
            "length(protected_key_identifier) BETWEEN 1 AND 512",
            name="ck_spool_root_key_provisioning_identity",
        ),
        sa.CheckConstraint(
            "(state = 'available' AND provisioned_at IS NOT NULL AND error_code IS NULL) OR (state = 'provisioning' AND provisioned_at IS NULL AND error_code IS NULL) OR (state = 'quarantined' AND provisioned_at IS NULL AND error_code IS NOT NULL)",
            name="ck_spool_root_key_provisioning_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["root_version"], ["spool_root_keys.root_version"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("root_version"),
    )

    op.create_table(
        "spool_reservations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("reservation_kind", sa.String(), nullable=False),
        sa.Column("admission_id", sa.String(), nullable=True),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("owner_generation", sa.Integer(), nullable=False),
        sa.Column("queue_slots", sa.Integer(), nullable=False),
        sa.Column("original_bytes", sa.Integer(), nullable=False),
        sa.Column("persistent_bytes", sa.Integer(), nullable=False),
        sa.Column("temporary_bytes", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("released_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "reservation_kind IN ('admission', 'execution_temp')",
            name="ck_spool_reservations_kind",
        ),
        sa.CheckConstraint(
            "length(device_id) > 0 AND length(owner_id) > 0 AND owner_generation > 0",
            name="ck_spool_reservations_identity",
        ),
        sa.CheckConstraint(
            "state IN ('held', 'committed', 'released', 'expired')",
            name="ck_spool_reservations_state",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_spool_reservations_expiry"
        ),
        sa.CheckConstraint(
            "((state IN ('held', 'committed')) AND released_at IS NULL) OR ((state IN ('released', 'expired')) AND released_at IS NOT NULL)",
            name="ck_spool_reservations_release",
        ),
        sa.CheckConstraint(
            "(reservation_kind = 'admission' AND admission_id IS NOT NULL AND queue_slots = 1 AND original_bytes > 0 AND persistent_bytes >= original_bytes + 65536 AND temporary_bytes = 0 AND ((state = 'held' AND job_id IS NULL) OR (state = 'committed' AND job_id IS NOT NULL) OR state IN ('released', 'expired'))) OR "
            "(reservation_kind = 'execution_temp' AND admission_id IS NULL AND job_id IS NOT NULL AND queue_slots = 0 AND original_bytes = 0 AND persistent_bytes = 0 AND temporary_bytes > 0 AND state IN ('held', 'released', 'expired'))",
            name="ck_spool_reservations_dimensions",
        ),
        sa.ForeignKeyConstraint(
            ["admission_id"], ["device_work_admissions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["public_print_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_spool_reservations_device_state",
        "spool_reservations",
        ["device_id", "state"],
    )
    op.create_index(
        "idx_spool_reservations_expiry", "spool_reservations", ["expires_at", "state"]
    )
    op.create_index(
        "idx_spool_reservations_admission_id", "spool_reservations", ["admission_id"]
    )
    op.create_index(
        "uq_spool_reservations_admission_active",
        "spool_reservations",
        ["admission_id"],
        unique=True,
        sqlite_where=sa.text("admission_id IS NOT NULL AND state IN ('held', 'committed')"),
    )
    op.create_index(
        "uq_spool_reservations_execution_owner_active",
        "spool_reservations",
        ["job_id", "owner_id", "owner_generation"],
        unique=True,
        sqlite_where=sa.text("reservation_kind = 'execution_temp' AND state = 'held'"),
    )

    op.create_table(
        "spool_nonce_reservations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("root_version", sa.Integer(), nullable=True),
        sa.Column("planned_job_id", sa.String(), nullable=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("admission_id", sa.String(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "(domain = 'root_wrap' AND purpose = 'data_key_wrap' AND root_version IS NOT NULL AND planned_job_id IS NULL) OR "
            "(domain = 'artifact' AND purpose IN ('original', 'derived_raster') AND root_version IS NULL AND planned_job_id IS NOT NULL)",
            name="ck_spool_nonce_reservations_domain",
        ),
        sa.CheckConstraint(
            "(planned_job_id IS NULL OR length(planned_job_id) BETWEEN 1 AND 128) AND length(nonce) = 12",
            name="ck_spool_nonce_reservations_identity",
        ),
        sa.ForeignKeyConstraint(
            ["root_version"], ["spool_root_keys.root_version"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["admission_id"], ["device_work_admissions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "domain",
            "root_version",
            "planned_job_id",
            "nonce",
            name="uq_spool_nonce_reservations_domain_nonce",
        ),
    )
    op.create_index(
        "idx_spool_nonce_reservations_admission_id",
        "spool_nonce_reservations",
        ["admission_id"],
    )
    op.create_index(
        "uq_spool_nonce_reservations_root_nonce",
        "spool_nonce_reservations",
        ["root_version", "nonce"],
        unique=True,
        sqlite_where=sa.text("domain = 'root_wrap'"),
    )
    op.create_index(
        "uq_spool_nonce_reservations_job_nonce",
        "spool_nonce_reservations",
        ["planned_job_id", "nonce"],
        unique=True,
        sqlite_where=sa.text("domain = 'artifact'"),
    )

    op.create_table(
        "spool_artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("admission_id", sa.String(), nullable=False),
        sa.Column("reservation_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("aad_job_id", sa.String(), nullable=False),
        sa.Column("intent_id", sa.String(), nullable=False),
        sa.Column("format_version", sa.Integer(), nullable=False),
        sa.Column("artifact_kind", sa.String(), nullable=False),
        sa.Column("storage_ref", sa.String(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("plaintext_size_bytes", sa.Integer(), nullable=False),
        sa.Column("ciphertext_size_bytes", sa.Integer(), nullable=False),
        sa.Column("plaintext_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("retention_policy", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("committed_at", sa.String(), nullable=True),
        sa.Column("delete_after", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.String(), nullable=True),
        sa.Column("delete_attempts", sa.Integer(), nullable=False),
        sa.Column("last_delete_error_code", sa.String(), nullable=True),
        sa.CheckConstraint(
            "length(aad_job_id) > 0 AND length(intent_id) > 0 AND length(storage_ref) > 0",
            name="ck_spool_artifacts_identity",
        ),
        sa.CheckConstraint(
            "format_version = 1", name="ck_spool_artifacts_format_version"
        ),
        sa.CheckConstraint(
            "artifact_kind IN ('original', 'derived_raster')",
            name="ck_spool_artifacts_kind",
        ),
        sa.CheckConstraint("length(nonce) = 12", name="ck_spool_artifacts_nonce"),
        sa.CheckConstraint(
            "length(plaintext_sha256) = 32", name="ck_spool_artifacts_plaintext_sha256"
        ),
        sa.CheckConstraint(
            "plaintext_size_bytes > 0", name="ck_spool_artifacts_plaintext_size"
        ),
        sa.CheckConstraint(
            "ciphertext_size_bytes >= plaintext_size_bytes + 16",
            name="ck_spool_artifacts_ciphertext_size",
        ),
        sa.CheckConstraint(
            "job_id IS NULL OR job_id = aad_job_id", name="ck_spool_artifacts_job_id"
        ),
        sa.CheckConstraint(
            "delete_attempts >= 0", name="ck_spool_artifacts_delete_attempts"
        ),
        sa.CheckConstraint(
            "last_delete_error_code IS NULL OR delete_attempts > 0",
            name="ck_spool_artifacts_delete_error",
        ),
        sa.CheckConstraint(
            "retention_policy IN ('active', 'success_immediate', 'integrity_immediate', 'failure_24h')",
            name="ck_spool_artifacts_retention_policy",
        ),
        sa.CheckConstraint(
            "(state = 'staging' AND job_id IS NULL AND committed_at IS NULL AND retention_policy = 'active' AND delete_after IS NULL AND deleted_at IS NULL AND delete_attempts = 0) OR "
            "(state = 'committed' AND job_id IS NOT NULL AND committed_at IS NOT NULL AND retention_policy = 'active' AND delete_after IS NULL AND deleted_at IS NULL) OR "
            "(state = 'delete_pending' AND job_id IS NOT NULL AND committed_at IS NOT NULL AND retention_policy IN ('success_immediate', 'integrity_immediate', 'failure_24h') AND delete_after IS NOT NULL AND deleted_at IS NULL) OR "
            "(state = 'deleted' AND job_id IS NOT NULL AND committed_at IS NOT NULL AND retention_policy IN ('success_immediate', 'integrity_immediate', 'failure_24h') AND delete_after IS NOT NULL AND deleted_at IS NOT NULL)",
            name="ck_spool_artifacts_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["admission_id"], ["device_work_admissions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["spool_reservations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["public_print_jobs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_ref"),
    )
    op.create_index(
        "idx_spool_artifacts_job_state", "spool_artifacts", ["job_id", "state"]
    )
    op.create_index(
        "idx_spool_artifacts_delete_after",
        "spool_artifacts",
        ["delete_after"],
        sqlite_where=sa.text("delete_after IS NOT NULL"),
    )
    op.create_index(
        "uq_spool_artifacts_aad_nonce",
        "spool_artifacts",
        ["aad_job_id", "nonce"],
        unique=True,
    )
    op.create_index(
        "uq_spool_artifacts_admission_kind",
        "spool_artifacts",
        ["admission_id", "artifact_kind"],
        unique=True,
    )

    op.create_table(
        "public_print_job_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "state_version > 0", name="ck_public_print_job_events_state_version"
        ),
        sa.CheckConstraint(
            "length(event_type) BETWEEN 1 AND 64",
            name="ck_public_print_job_events_event_type",
        ),
        sa.CheckConstraint(
            "json_valid(snapshot_json) AND json_type(snapshot_json) = 'object' AND length(CAST(snapshot_json AS BLOB)) <= 16384 AND json_extract(snapshot_json, '$.receipt_payload') IS NULL AND json_extract(snapshot_json, '$.raw_driver_message') IS NULL AND json_extract(snapshot_json, '$.document_bytes') IS NULL",
            name="ck_public_print_job_events_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["public_print_jobs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint(
            "job_id", "state_version", name="uq_public_print_job_events_job_version"
        ),
    )
    op.create_index(
        "idx_public_print_job_events_job_sequence",
        "public_print_job_events",
        ["job_id", sa.text("sequence DESC")],
    )
    op.create_index(
        "idx_public_print_job_events_sequence", "public_print_job_events", ["sequence"]
    )
    _create_identity_triggers()


def downgrade() -> None:
    _drop_identity_triggers()
    op.drop_index(
        "idx_public_print_job_events_sequence", table_name="public_print_job_events"
    )
    op.drop_index(
        "idx_public_print_job_events_job_sequence", table_name="public_print_job_events"
    )
    op.drop_table("public_print_job_events")
    op.drop_index("uq_spool_artifacts_aad_nonce", table_name="spool_artifacts")
    op.drop_index(
        "uq_spool_artifacts_admission_kind", table_name="spool_artifacts"
    )
    op.drop_index("idx_spool_artifacts_delete_after", table_name="spool_artifacts")
    op.drop_index("idx_spool_artifacts_job_state", table_name="spool_artifacts")
    op.drop_table("spool_artifacts")
    op.drop_index(
        "idx_spool_nonce_reservations_admission_id",
        table_name="spool_nonce_reservations",
    )
    op.drop_table("spool_nonce_reservations")
    op.drop_index(
        "idx_spool_reservations_admission_id", table_name="spool_reservations"
    )
    op.drop_index("idx_spool_reservations_expiry", table_name="spool_reservations")
    op.drop_index(
        "idx_spool_reservations_device_state", table_name="spool_reservations"
    )
    op.drop_table("spool_reservations")
    op.drop_table("spool_admission_keys")
    op.drop_table("spool_job_keys")
    op.drop_table("spool_root_key_provisioning")
    op.drop_table("spool_security_state")
    op.drop_index("uq_spool_root_keys_retiring", table_name="spool_root_keys")
    op.drop_index("uq_spool_root_keys_current", table_name="spool_root_keys")
    op.drop_table("spool_root_keys")
    op.drop_index(
        "idx_device_work_admissions_job_id", table_name="device_work_admissions"
    )
    op.drop_index(
        "uq_device_work_admissions_job_id", table_name="device_work_admissions"
    )
    op.drop_index(
        "idx_device_work_admissions_idempotency_expiry",
        table_name="device_work_admissions",
    )
    op.drop_index(
        "uq_device_work_admissions_origin_submission",
        table_name="device_work_admissions",
    )
    op.drop_index(
        "uq_device_work_admissions_manager_pos_idempotency",
        table_name="device_work_admissions",
    )
    op.drop_index(
        "uq_device_work_admissions_manager_idempotency",
        table_name="device_work_admissions",
    )
    op.drop_index(
        "uq_device_work_admissions_paired_idempotency",
        table_name="device_work_admissions",
    )
    op.drop_table("device_work_admissions")
    op.drop_index(
        "uq_public_print_jobs_managed_work_id", table_name="public_print_jobs"
    )
    op.drop_index("uq_public_print_jobs_intent_id", table_name="public_print_jobs")
    op.drop_index(
        "idx_public_print_jobs_state_expires_at", table_name="public_print_jobs"
    )
    op.drop_index(
        "idx_public_print_jobs_scope_accepted_at", table_name="public_print_jobs"
    )
    op.drop_table("public_print_jobs")


def _create_identity_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER ck_device_work_admissions_acceptance_link
        BEFORE UPDATE OF state, job_id, accepted_at ON device_work_admissions
        WHEN NEW.state = 'accepted'
         AND NOT EXISTS (
            SELECT 1 FROM public_print_jobs AS job
            WHERE job.id = NEW.job_id
              AND job.admission_id = NEW.id
              AND job.state = 'accepted'
              AND job.accepted_at = NEW.accepted_at
              AND job.expires_at = NEW.deadline_at
              AND job.contract_version = 'v' || NEW.contract_major
         )
        BEGIN
            SELECT RAISE(ABORT, 'accepted admission requires matching public job');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_work_admissions_identity_immutable
        BEFORE UPDATE OF id, planned_job_id, database, scope_kind, organization_id,
            site_id, pos_configuration_id, paired_client_id, idempotency_key,
            fingerprint, intent_id, device_id, deadline_at, original_size_bytes,
            created_at, actor_id, binding_revision_id, authorization_digest,
            operation, media_type, normalized_options_digest, grant_scope_digest,
            origin_submission_key, origin_kind, origin_json, contract_major,
            copy_ordinal ON device_work_admissions
        BEGIN
            SELECT RAISE(ABORT, 'admission identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_public_print_jobs_admission_identity_insert
        BEFORE INSERT ON public_print_jobs
        WHEN EXISTS (SELECT 1 FROM device_work_admissions WHERE id = NEW.admission_id)
         AND NOT EXISTS (
            SELECT 1 FROM device_work_admissions AS admission
            WHERE admission.id = NEW.admission_id
              AND admission.state = 'finalizing'
              AND admission.planned_job_id = NEW.id
              AND admission.intent_id = NEW.intent_id
              AND admission.device_id = NEW.device_id
              AND admission.scope_kind = NEW.scope_kind
              AND admission.organization_id = NEW.organization_id
              AND admission.site_id = NEW.site_id
              AND admission.pos_configuration_id IS NEW.pos_configuration_id
              AND admission.paired_client_id IS NEW.paired_client_id
              AND admission.origin_kind = NEW.origin_kind
              AND admission.origin_json = NEW.origin_json
         )
        BEGIN
            SELECT RAISE(ABORT, 'public print job identity does not match admission');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_public_print_jobs_admission_identity_update
        BEFORE UPDATE OF admission_id, id, intent_id, device_id, scope_kind,
            organization_id, site_id, pos_configuration_id, paired_client_id,
            origin_kind, origin_json ON public_print_jobs
        WHEN EXISTS (SELECT 1 FROM device_work_admissions WHERE id = NEW.admission_id)
         AND NOT EXISTS (
            SELECT 1 FROM device_work_admissions AS admission
            WHERE admission.id = NEW.admission_id
              AND admission.planned_job_id = NEW.id
              AND admission.intent_id = NEW.intent_id
              AND admission.device_id = NEW.device_id
              AND admission.scope_kind = NEW.scope_kind
              AND admission.organization_id = NEW.organization_id
              AND admission.site_id = NEW.site_id
              AND admission.pos_configuration_id IS NEW.pos_configuration_id
              AND admission.paired_client_id IS NEW.paired_client_id
              AND admission.origin_kind = NEW.origin_kind
              AND admission.origin_json = NEW.origin_json
         )
        BEGIN
            SELECT RAISE(ABORT, 'public print job identity does not match admission');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_admission_keys_identity
        BEFORE INSERT ON spool_admission_keys
        WHEN EXISTS (SELECT 1 FROM device_work_admissions WHERE id = NEW.admission_id)
         AND NOT EXISTS (
            SELECT 1 FROM device_work_admissions AS admission
            WHERE admission.id = NEW.admission_id
              AND admission.planned_job_id = NEW.planned_job_id
              AND admission.intent_id = NEW.intent_id
         )
        BEGIN
            SELECT RAISE(ABORT, 'staging key identity does not match admission');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_admission_keys_immutable
        BEFORE UPDATE ON spool_admission_keys
        BEGIN
            SELECT RAISE(ABORT, 'staging key is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_job_keys_identity
        BEFORE INSERT ON spool_job_keys
        WHEN EXISTS (SELECT 1 FROM public_print_jobs WHERE id = NEW.job_id)
         AND NOT EXISTS (
            SELECT 1 FROM public_print_jobs AS job
            WHERE job.id = NEW.job_id AND job.intent_id = NEW.intent_id
         )
        BEGIN
            SELECT RAISE(ABORT, 'job key identity does not match public job');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_job_keys_lifecycle
        BEFORE UPDATE ON spool_job_keys
        WHEN NOT (
            NEW.job_id = OLD.job_id
            AND NEW.intent_id = OLD.intent_id
            AND NEW.format_version = OLD.format_version
            AND NEW.created_at = OLD.created_at
            AND (
                (OLD.key_deleted_at IS NULL
                 AND NEW.key_deleted_at IS NOT NULL
                 AND NEW.root_version = OLD.root_version
                 AND NEW.wrap_nonce = OLD.wrap_nonce
                 AND length(NEW.wrapped_key) = 0)
                OR
                (OLD.key_deleted_at IS NULL
                 AND NEW.key_deleted_at IS NULL
                 AND NEW.root_version != OLD.root_version
                 AND NEW.wrap_nonce != OLD.wrap_nonce
                 AND length(NEW.wrapped_key) = 48)
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'job key transition is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_nonce_reservations_identity
        BEFORE INSERT ON spool_nonce_reservations
        WHEN NEW.domain = 'artifact'
         AND EXISTS (SELECT 1 FROM device_work_admissions WHERE id = NEW.admission_id)
         AND NOT EXISTS (
            SELECT 1 FROM device_work_admissions AS admission
            WHERE admission.id = NEW.admission_id
              AND admission.planned_job_id = NEW.planned_job_id
         )
        BEGIN
            SELECT RAISE(ABORT, 'artifact nonce identity does not match admission');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_nonce_reservations_immutable
        BEFORE UPDATE ON spool_nonce_reservations
        BEGIN
            SELECT RAISE(ABORT, 'nonce reservation is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_nonce_reservations_no_delete
        BEFORE DELETE ON spool_nonce_reservations
        BEGIN
            SELECT RAISE(ABORT, 'nonce reservation cannot be deleted');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_artifacts_identity
        BEFORE INSERT ON spool_artifacts
        WHEN EXISTS (SELECT 1 FROM device_work_admissions WHERE id = NEW.admission_id)
         AND NOT EXISTS (
            SELECT 1 FROM device_work_admissions AS admission
            JOIN spool_reservations AS reservation
              ON reservation.id = NEW.reservation_id
            WHERE admission.id = NEW.admission_id
              AND admission.planned_job_id = NEW.aad_job_id
              AND admission.intent_id = NEW.intent_id
              AND reservation.admission_id = NEW.admission_id
              AND reservation.device_id = admission.device_id
              AND EXISTS (
                  SELECT 1 FROM spool_nonce_reservations AS nonce
                  WHERE nonce.domain = 'artifact'
                    AND nonce.admission_id = NEW.admission_id
                    AND nonce.planned_job_id = NEW.aad_job_id
                    AND nonce.nonce = NEW.nonce
              )
         )
        BEGIN
            SELECT RAISE(ABORT, 'artifact identity does not match admission');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_artifacts_identity_immutable
        BEFORE UPDATE OF id, admission_id, reservation_id, aad_job_id, intent_id,
            format_version, artifact_kind, storage_ref, nonce,
            plaintext_size_bytes, ciphertext_size_bytes, plaintext_sha256,
            created_at ON spool_artifacts
        BEGIN
            SELECT RAISE(ABORT, 'artifact identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_artifacts_job_link
        BEFORE UPDATE OF job_id ON spool_artifacts
        WHEN NEW.job_id IS NOT NULL
         AND NOT EXISTS (
            SELECT 1 FROM public_print_jobs AS job
            JOIN spool_reservations AS reservation
              ON reservation.id = NEW.reservation_id
            WHERE job.id = NEW.job_id
              AND job.admission_id = NEW.admission_id
              AND job.id = NEW.aad_job_id
              AND job.intent_id = NEW.intent_id
              AND reservation.admission_id = NEW.admission_id
              AND reservation.job_id = NEW.job_id
         )
        BEGIN
            SELECT RAISE(ABORT, 'artifact job link is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_reservations_identity_immutable
        BEFORE UPDATE OF id, reservation_kind, admission_id, device_id, owner_id,
            owner_generation, queue_slots, original_bytes, persistent_bytes,
            temporary_bytes, created_at ON spool_reservations
        BEGIN
            SELECT RAISE(ABORT, 'reservation identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_reservations_admission_identity
        BEFORE INSERT ON spool_reservations
        WHEN NEW.reservation_kind = 'admission'
         AND NOT EXISTS (
            SELECT 1 FROM device_work_admissions AS admission
            WHERE admission.id = NEW.admission_id
              AND admission.device_id = NEW.device_id
         )
        BEGIN
            SELECT RAISE(ABORT, 'reservation identity does not match admission');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_reservations_job_link
        BEFORE UPDATE OF job_id ON spool_reservations
        WHEN NEW.job_id IS NOT NULL
         AND NOT EXISTS (
            SELECT 1 FROM public_print_jobs AS job
            WHERE job.id = NEW.job_id
              AND job.admission_id IS NEW.admission_id
              AND job.device_id = NEW.device_id
         )
        BEGIN
            SELECT RAISE(ABORT, 'reservation job link is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_security_state_generation
        BEFORE UPDATE OF committed_generation ON spool_security_state
        WHEN NEW.committed_generation < OLD.committed_generation
        BEGIN
            SELECT RAISE(ABORT, 'spool security generation cannot move backward');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_security_state_identity_immutable
        BEFORE UPDATE OF singleton_id, anchor_kind, anchor_identifier,
            anchor_evidence_digest, created_at ON spool_security_state
        BEGIN
            SELECT RAISE(ABORT, 'spool security identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_spool_security_state_quarantine_terminal
        BEFORE UPDATE OF status ON spool_security_state
        WHEN OLD.status = 'quarantined' AND NEW.status != 'quarantined'
        BEGIN
            SELECT RAISE(ABORT, 'spool security quarantine is terminal');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_public_print_jobs_lifecycle_update
        BEFORE UPDATE OF state, state_version ON public_print_jobs
        WHEN NOT (
            NEW.state_version = OLD.state_version + 1
            AND (
                (OLD.state = 'accepted' AND NEW.state IN ('in_progress', 'failed', 'expired', 'canceled'))
                OR (OLD.state = 'in_progress' AND NEW.state IN ('output_confirmed', 'failed', 'outcome_unknown'))
                OR (OLD.state = 'outcome_unknown' AND NEW.state IN ('output_confirmed', 'failed'))
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'public print job transition is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_public_print_job_events_job_state
        BEFORE INSERT ON public_print_job_events
        WHEN NOT EXISTS (
            SELECT 1 FROM public_print_jobs AS job
            WHERE job.id = NEW.job_id
              AND job.state_version = NEW.state_version
              AND job.state = NEW.event_type
              AND json_extract(NEW.snapshot_json, '$.job_id') = NEW.job_id
              AND json_extract(NEW.snapshot_json, '$.state') = job.state
              AND json_extract(NEW.snapshot_json, '$.state_version') = NEW.state_version
              AND NEW.snapshot_json = json(NEW.snapshot_json)
        )
        BEGIN
            SELECT RAISE(ABORT, 'public print job event does not match job state');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_work_admissions_nested_content_insert
        BEFORE INSERT ON device_work_admissions
        WHEN EXISTS (
            SELECT 1 FROM json_tree(NEW.origin_json)
            WHERE key IN ('receipt_payload', 'raw_driver_message', 'document_bytes')
        )
        BEGIN
            SELECT RAISE(ABORT, 'admission origin contains forbidden content');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_public_print_job_events_nested_content_insert
        BEFORE INSERT ON public_print_job_events
        WHEN EXISTS (
            SELECT 1 FROM json_tree(NEW.snapshot_json)
            WHERE key IN ('receipt_payload', 'raw_driver_message', 'document_bytes')
        )
        BEGIN
            SELECT RAISE(ABORT, 'public print job event contains forbidden content');
        END
        """
    )


def _drop_identity_triggers() -> None:
    for name in (
        "ck_public_print_job_events_nested_content_insert",
        "ck_device_work_admissions_nested_content_insert",
        "ck_public_print_job_events_job_state",
        "ck_public_print_jobs_lifecycle_update",
        "ck_spool_security_state_quarantine_terminal",
        "ck_spool_security_state_identity_immutable",
        "ck_spool_security_state_generation",
        "ck_spool_reservations_job_link",
        "ck_spool_reservations_admission_identity",
        "ck_spool_reservations_identity_immutable",
        "ck_spool_artifacts_job_link",
        "ck_spool_artifacts_identity_immutable",
        "ck_spool_artifacts_identity",
        "ck_spool_nonce_reservations_no_delete",
        "ck_spool_nonce_reservations_immutable",
        "ck_spool_nonce_reservations_identity",
        "ck_spool_job_keys_lifecycle",
        "ck_spool_job_keys_identity",
        "ck_spool_admission_keys_immutable",
        "ck_spool_admission_keys_identity",
        "ck_public_print_jobs_admission_identity_update",
        "ck_public_print_jobs_admission_identity_insert",
        "ck_device_work_admissions_identity_immutable",
        "ck_device_work_admissions_acceptance_link",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
