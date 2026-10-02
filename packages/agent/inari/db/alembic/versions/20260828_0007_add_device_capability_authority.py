"""Create the signed Device Capability authority schema."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260828_0007"
down_revision = "20260828_0006"
branch_labels = None
depends_on = None


_IMMUTABLE_TABLES = (
    ("device_authority_revisions", "authority revision"),
    ("device_driver_profiles", "Driver Profile"),
    ("hardware_certification_matrix_rows", "certification matrix row"),
    ("device_binding_revisions", "Binding Revision"),
    ("device_test_evidence", "Device Test evidence"),
    ("device_observations", "Device observation"),
    ("device_authority_revocations", "authority revocation"),
    ("device_work_authority_proofs", "authority proof"),
    ("device_work_authority_proof_subjects", "authority proof subject"),
)


def upgrade() -> None:
    _expand_admission_failure_codes()
    op.create_table(
        "device_authority_signer_keys",
        sa.Column("key_id", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("not_before", sa.String(), nullable=False),
        sa.Column("not_after", sa.String(), nullable=True),
        sa.Column("retired_at", sa.String(), nullable=True),
        sa.CheckConstraint(
            "length(key_id) BETWEEN 1 AND 256",
            name="ck_device_authority_signer_keys_identity",
        ),
        sa.CheckConstraint(
            "purpose IN ('authority_revision', 'driver_profile', 'certification_matrix', 'binding_revision', 'device_test_evidence', 'device_observation', 'authority_revocation')",
            name="ck_device_authority_signer_keys_purpose",
        ),
        sa.CheckConstraint(
            "length(public_key) = 32",
            name="ck_device_authority_signer_keys_public_key",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'retired') AND ((state = 'active' AND retired_at IS NULL) OR (state = 'retired' AND retired_at IS NOT NULL))",
            name="ck_device_authority_signer_keys_state",
        ),
        sa.CheckConstraint(
            "(not_after IS NULL OR not_after > not_before) AND (retired_at IS NULL OR retired_at >= not_before)",
            name="ck_device_authority_signer_keys_validity",
        ),
        sa.UniqueConstraint(
            "public_key", name="uq_device_authority_signer_keys_public_key"
        ),
        sa.PrimaryKeyConstraint("key_id"),
    )
    op.create_index(
        "idx_device_authority_signer_keys_purpose_state",
        "device_authority_signer_keys",
        ["purpose", "state"],
    )

    op.create_table(
        "device_authority_revisions",
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("manifest_digest", sa.LargeBinary(), nullable=False),
        sa.Column("effective_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("revision_digest", sa.LargeBinary(), nullable=False),
        sa.Column("signer_key_id", sa.String(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "length(revision_id) BETWEEN 1 AND 256 AND revision_number > 0",
            name="ck_device_authority_revisions_identity",
        ),
        sa.CheckConstraint(
            "length(manifest_digest) = 32 AND length(revision_digest) = 32 AND length(signature) = 64",
            name="ck_device_authority_revisions_cryptography",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_at",
            name="ck_device_authority_revisions_validity",
        ),
        sa.ForeignKeyConstraint(
            ["signer_key_id"],
            ["device_authority_signer_keys.key_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("revision_id"),
        sa.UniqueConstraint(
            "revision_number", name="uq_device_authority_revisions_number"
        ),
    )
    op.create_index(
        "idx_device_authority_revisions_signer_key",
        "device_authority_revisions",
        ["signer_key_id"],
    )
    op.create_index(
        "uq_device_authority_revisions_digest",
        "device_authority_revisions",
        ["revision_digest"],
        unique=True,
    )

    op.create_table(
        "device_authority_state",
        sa.Column("singleton_id", sa.Integer(), nullable=False),
        sa.Column("current_revision_id", sa.String(), nullable=True),
        sa.Column("current_revision_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("quarantined_at", sa.String(), nullable=True),
        sa.Column("quarantine_reason_code", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "singleton_id = 1", name="ck_device_authority_state_singleton"
        ),
        sa.CheckConstraint(
            "current_revision_number >= 0",
            name="ck_device_authority_state_revision",
        ),
        sa.CheckConstraint(
            "(current_revision_number = 0 AND current_revision_id IS NULL) OR (current_revision_number > 0 AND current_revision_id IS NOT NULL)",
            name="ck_device_authority_state_current_revision",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'quarantined')",
            name="ck_device_authority_state_status",
        ),
        sa.CheckConstraint(
            "(status = 'ready' AND quarantined_at IS NULL AND quarantine_reason_code IS NULL) OR (status = 'quarantined' AND quarantined_at IS NOT NULL AND quarantine_reason_code IS NOT NULL)",
            name="ck_device_authority_state_quarantine",
        ),
        sa.ForeignKeyConstraint(
            ["current_revision_id"],
            ["device_authority_revisions.revision_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("singleton_id"),
    )

    op.create_table(
        "device_driver_profiles",
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("driver_id", sa.String(), nullable=False),
        sa.Column("min_agent_version", sa.String(), nullable=False),
        sa.Column("capabilities", sa.Text(), nullable=False),
        sa.Column("profile_digest", sa.LargeBinary(), nullable=False),
        sa.Column("signer_key_id", sa.String(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column("effective_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("authority_revision_id", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(profile_id) BETWEEN 1 AND 256 AND length(version) BETWEEN 1 AND 256 AND length(driver_id) BETWEEN 1 AND 256 AND length(min_agent_version) BETWEEN 1 AND 256",
            name="ck_device_driver_profiles_identity",
        ),
        sa.CheckConstraint(
            "json_valid(capabilities) AND json_type(capabilities) = 'array' AND capabilities = json(capabilities) AND length(CAST(capabilities AS BLOB)) <= 16384",
            name="ck_device_driver_profiles_capabilities",
        ),
        sa.CheckConstraint(
            "length(profile_digest) = 32 AND length(signature) = 64",
            name="ck_device_driver_profiles_cryptography",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_at",
            name="ck_device_driver_profiles_validity",
        ),
        sa.ForeignKeyConstraint(
            ["authority_revision_id"],
            ["device_authority_revisions.revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signer_key_id"],
            ["device_authority_signer_keys.key_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("profile_id"),
        sa.UniqueConstraint("profile_digest", name="uq_device_driver_profiles_digest"),
    )
    op.create_index(
        "idx_device_driver_profiles_driver_version",
        "device_driver_profiles",
        ["driver_id", "version"],
    )

    op.create_table(
        "hardware_certification_matrix_rows",
        sa.Column("row_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "device_id",
            sa.String(),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("device_identity_digest", sa.LargeBinary(), nullable=False),
        sa.Column("manufacturer", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("firmware_version", sa.String(), nullable=False),
        sa.Column("firmware_build", sa.String(), nullable=False),
        sa.Column("driver_id", sa.String(), nullable=False),
        sa.Column("driver_profile_digest", sa.LargeBinary(), nullable=False),
        sa.Column("capability_id", sa.String(), nullable=False),
        sa.Column("platform_backend_id", sa.String(), nullable=False),
        sa.Column("connection", sa.String(), nullable=False),
        sa.Column("media_profile", sa.String(), nullable=False),
        sa.Column("operating_system", sa.String(), nullable=False),
        sa.Column("release_set_id", sa.String(), nullable=False),
        sa.Column("effective_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("matrix_row_digest", sa.LargeBinary(), nullable=False),
        sa.Column("signer_key_id", sa.String(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column("authority_revision_id", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(row_id) BETWEEN 1 AND 256 AND version > 0 AND length(device_identity_digest) = 32 AND length(driver_id) BETWEEN 1 AND 256 AND length(driver_profile_digest) = 32 AND length(capability_id) BETWEEN 1 AND 256",
            name="ck_hardware_certification_matrix_rows_identity",
        ),
        sa.CheckConstraint(
            "length(manufacturer) BETWEEN 1 AND 256 AND length(model) BETWEEN 1 AND 256 AND length(firmware_version) BETWEEN 1 AND 256 AND length(firmware_build) BETWEEN 1 AND 256 AND length(platform_backend_id) BETWEEN 1 AND 256 AND length(connection) BETWEEN 1 AND 256 AND length(media_profile) BETWEEN 1 AND 256 AND length(operating_system) BETWEEN 1 AND 256 AND length(release_set_id) BETWEEN 1 AND 256",
            name="ck_hardware_certification_matrix_rows_fields",
        ),
        sa.CheckConstraint(
            "length(matrix_row_digest) = 32 AND length(signature) = 64",
            name="ck_hardware_certification_matrix_rows_cryptography",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_at",
            name="ck_hardware_certification_matrix_rows_validity",
        ),
        sa.ForeignKeyConstraint(
            ["authority_revision_id"],
            ["device_authority_revisions.revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signer_key_id"],
            ["device_authority_signer_keys.key_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint(
            "device_id",
            "version",
            "driver_id",
            "capability_id",
            name="uq_hardware_certification_matrix_rows_graph",
        ),
    )
    op.create_index(
        "idx_hardware_certification_matrix_rows_profile_digest",
        "hardware_certification_matrix_rows",
        ["driver_profile_digest"],
    )

    op.create_table(
        "device_binding_revisions",
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("binding_id", sa.String(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("database", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("site_id", sa.String(), nullable=False),
        sa.Column("scope_kind", sa.String(), nullable=False),
        sa.Column("pos_configuration_id", sa.String(), nullable=True),
        sa.Column("device_purpose", sa.String(), nullable=False),
        sa.Column(
            "device_id",
            sa.String(),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("device_identity_digest", sa.LargeBinary(), nullable=False),
        sa.Column("capability_id", sa.String(), nullable=False),
        sa.Column("driver_profile_digest", sa.LargeBinary(), nullable=False),
        sa.Column(
            "matrix_row_id",
            sa.String(),
            sa.ForeignKey(
                "hardware_certification_matrix_rows.row_id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("options_digest", sa.LargeBinary(), nullable=False),
        sa.Column("binding_digest", sa.LargeBinary(), nullable=False),
        sa.Column("signer_key_id", sa.String(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column("effective_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("authority_revision_id", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(revision_id) BETWEEN 1 AND 256 AND length(binding_id) BETWEEN 1 AND 256 AND revision_number > 0 AND length(database) BETWEEN 1 AND 256 AND length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND length(device_purpose) BETWEEN 1 AND 256 AND length(device_identity_digest) = 32 AND length(capability_id) BETWEEN 1 AND 256 AND length(driver_profile_digest) = 32 AND length(options_digest) = 32",
            name="ck_device_binding_revisions_identity",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('site', 'pos_configuration') AND ((scope_kind = 'site' AND pos_configuration_id IS NULL) OR (scope_kind = 'pos_configuration' AND length(pos_configuration_id) BETWEEN 1 AND 256))",
            name="ck_device_binding_revisions_scope",
        ),
        sa.CheckConstraint(
            "length(binding_digest) = 32 AND length(signature) = 64",
            name="ck_device_binding_revisions_cryptography",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_at",
            name="ck_device_binding_revisions_validity",
        ),
        sa.ForeignKeyConstraint(
            ["authority_revision_id"],
            ["device_authority_revisions.revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signer_key_id"],
            ["device_authority_signer_keys.key_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("revision_id"),
        sa.UniqueConstraint(
            "binding_id",
            "revision_number",
            name="uq_device_binding_revisions_number",
        ),
        sa.UniqueConstraint(
            "binding_digest", name="uq_device_binding_revisions_digest"
        ),
    )
    op.create_index(
        "idx_device_binding_revisions_scope",
        "device_binding_revisions",
        [
            "database",
            "organization_id",
            "site_id",
            "scope_kind",
            "pos_configuration_id",
            "device_purpose",
        ],
    )

    op.create_table(
        "device_test_evidence",
        sa.Column("evidence_id", sa.String(), nullable=False),
        sa.Column(
            "revision_id",
            sa.String(),
            sa.ForeignKey("device_binding_revisions.revision_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.String(),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("device_identity_digest", sa.LargeBinary(), nullable=False),
        sa.Column("capability_id", sa.String(), nullable=False),
        sa.Column("driver_profile_digest", sa.LargeBinary(), nullable=False),
        sa.Column(
            "matrix_row_id",
            sa.String(),
            sa.ForeignKey(
                "hardware_certification_matrix_rows.row_id", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("output_evidence", sa.String(), nullable=False),
        sa.Column("result", sa.String(), nullable=False),
        sa.Column("test_pattern_digest", sa.LargeBinary(), nullable=False),
        sa.Column("tested_at", sa.String(), nullable=False),
        sa.Column("valid_until", sa.String(), nullable=True),
        sa.Column("evidence_digest", sa.LargeBinary(), nullable=False),
        sa.Column("signer_key_id", sa.String(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column("authority_revision_id", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(evidence_id) BETWEEN 1 AND 256 AND length(device_identity_digest) = 32 AND length(capability_id) BETWEEN 1 AND 256 AND length(driver_profile_digest) = 32 AND length(matrix_row_id) BETWEEN 1 AND 256 AND length(test_pattern_digest) = 32",
            name="ck_device_test_evidence_identity",
        ),
        sa.CheckConstraint(
            "output_evidence IN ('transport', 'spooler', 'device')",
            name="ck_device_test_evidence_output",
        ),
        sa.CheckConstraint(
            "result IN ('passed', 'failed_environment', 'failed_contract')",
            name="ck_device_test_evidence_result",
        ),
        sa.CheckConstraint(
            "length(evidence_digest) = 32 AND length(signature) = 64",
            name="ck_device_test_evidence_cryptography",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > tested_at",
            name="ck_device_test_evidence_validity",
        ),
        sa.ForeignKeyConstraint(
            ["authority_revision_id"],
            ["device_authority_revisions.revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signer_key_id"],
            ["device_authority_signer_keys.key_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("evidence_id"),
        sa.UniqueConstraint("evidence_digest", name="uq_device_test_evidence_digest"),
    )
    op.create_index(
        "idx_device_test_evidence_revision",
        "device_test_evidence",
        ["revision_id", "result"],
    )

    op.create_table(
        "device_observations",
        sa.Column("observation_id", sa.String(), nullable=False),
        sa.Column(
            "device_id",
            sa.String(),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("device_identity_digest", sa.LargeBinary(), nullable=False),
        sa.Column("driver_id", sa.String(), nullable=False),
        sa.Column("driver_profile_digest", sa.LargeBinary(), nullable=False),
        sa.Column("platform_backend_id", sa.String(), nullable=False),
        sa.Column("connection", sa.String(), nullable=False),
        sa.Column("media_profile", sa.String(), nullable=False),
        sa.Column("firmware_version", sa.String(), nullable=False),
        sa.Column("firmware_build", sa.String(), nullable=False),
        sa.Column("operating_system", sa.String(), nullable=False),
        sa.Column("ready", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("observed_at", sa.String(), nullable=False),
        sa.Column("observation_digest", sa.LargeBinary(), nullable=False),
        sa.Column("signer_key_id", sa.String(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "length(observation_id) BETWEEN 1 AND 256 AND length(device_identity_digest) = 32 AND length(driver_id) BETWEEN 1 AND 256 AND length(driver_profile_digest) = 32 AND length(platform_backend_id) BETWEEN 1 AND 256 AND length(connection) BETWEEN 1 AND 256 AND length(media_profile) BETWEEN 1 AND 256 AND length(firmware_version) BETWEEN 1 AND 256 AND length(firmware_build) BETWEEN 1 AND 256 AND length(operating_system) BETWEEN 1 AND 256 AND length(state) BETWEEN 1 AND 256",
            name="ck_device_observations_identity",
        ),
        sa.CheckConstraint(
            "length(observation_digest) = 32 AND length(signature) = 64",
            name="ck_device_observations_cryptography",
        ),
        sa.ForeignKeyConstraint(
            ["signer_key_id"],
            ["device_authority_signer_keys.key_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("observation_id"),
    )
    op.create_index(
        "idx_device_observations_device_observed_at",
        "device_observations",
        ["device_id", "observed_at"],
    )

    op.create_table(
        "device_binding_authority_state",
        sa.Column("state_id", sa.String(), nullable=False),
        sa.Column(
            "active_revision_id",
            sa.String(),
            sa.ForeignKey("device_binding_revisions.revision_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "active_test_evidence_id",
            sa.String(),
            sa.ForeignKey("device_test_evidence.evidence_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("database", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("site_id", sa.String(), nullable=False),
        sa.Column("scope_kind", sa.String(), nullable=False),
        sa.Column("pos_configuration_id", sa.String(), nullable=True),
        sa.Column("device_purpose", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(state_id) BETWEEN 1 AND 256 AND length(database) BETWEEN 1 AND 256 AND length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND length(device_purpose) BETWEEN 1 AND 256",
            name="ck_device_binding_authority_state_identity",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('site', 'pos_configuration') AND ((scope_kind = 'site' AND pos_configuration_id IS NULL) OR (scope_kind = 'pos_configuration' AND length(pos_configuration_id) BETWEEN 1 AND 256))",
            name="ck_device_binding_authority_state_scope",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_device_binding_authority_state_status",
        ),
        sa.PrimaryKeyConstraint("state_id"),
    )
    op.create_index(
        "uq_device_binding_authority_state_active_scope_purpose",
        "device_binding_authority_state",
        [
            "database",
            "organization_id",
            "site_id",
            "scope_kind",
            sa.text("coalesce(pos_configuration_id, '')"),
            "device_purpose",
        ],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "idx_device_binding_authority_state_binding",
        "device_binding_authority_state",
        ["active_revision_id", "status"],
    )

    op.create_table(
        "device_authority_revocations",
        sa.Column("revocation_id", sa.String(), nullable=False),
        sa.Column("authority_revision_id", sa.String(), nullable=False),
        sa.Column("signer_key_id", sa.String(), nullable=False),
        sa.Column("subject_kind", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("subject_digest", sa.LargeBinary(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("revoked_at", sa.String(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "length(revocation_id) BETWEEN 1 AND 256 AND length(subject_id) BETWEEN 1 AND 256 AND length(reason_code) BETWEEN 1 AND 256 AND length(subject_digest) = 32",
            name="ck_device_authority_revocations_identity",
        ),
        sa.CheckConstraint(
            "subject_kind IN ('authority_revision', 'driver_profile', 'certification_matrix_row', 'binding_revision', 'device_test_evidence')",
            name="ck_device_authority_revocations_subject",
        ),
        sa.CheckConstraint(
            "length(signature) = 64",
            name="ck_device_authority_revocations_cryptography",
        ),
        sa.ForeignKeyConstraint(
            ["authority_revision_id"],
            ["device_authority_revisions.revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signer_key_id"],
            ["device_authority_signer_keys.key_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("revocation_id"),
        sa.UniqueConstraint(
            "subject_kind",
            "subject_id",
            "subject_digest",
            name="uq_device_authority_revocations_subject",
        ),
    )
    op.create_index(
        "idx_device_authority_revocations_revision",
        "device_authority_revocations",
        ["authority_revision_id", "revoked_at"],
    )

    op.create_table(
        "device_work_authority_proofs",
        sa.Column("proof_id", sa.String(), nullable=False),
        sa.Column(
            "admission_id",
            sa.String(),
            sa.ForeignKey("device_work_admissions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("authority_revision_id", sa.String(), nullable=False),
        sa.Column("authority_revision_number", sa.Integer(), nullable=False),
        sa.Column("authority_revision_digest", sa.LargeBinary(), nullable=False),
        sa.Column("snapshot_digest", sa.LargeBinary(), nullable=False),
        sa.Column("graph_digest", sa.LargeBinary(), nullable=False),
        sa.Column("scope_digest", sa.LargeBinary(), nullable=False),
        sa.Column("observation_digest", sa.LargeBinary(), nullable=False),
        sa.Column("binding_revision_id", sa.String(), nullable=False),
        sa.Column("binding_revision_digest", sa.LargeBinary(), nullable=False),
        sa.Column("driver_profile_id", sa.String(), nullable=False),
        sa.Column("driver_profile_digest", sa.LargeBinary(), nullable=False),
        sa.Column("matrix_row_id", sa.String(), nullable=False),
        sa.Column("matrix_row_digest", sa.LargeBinary(), nullable=False),
        sa.Column("test_evidence_id", sa.String(), nullable=False),
        sa.Column("test_evidence_digest", sa.LargeBinary(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("device_identity_digest", sa.LargeBinary(), nullable=False),
        sa.Column("capability_id", sa.String(), nullable=False),
        sa.Column("device_purpose", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("contract_major", sa.Integer(), nullable=False),
        sa.Column("options_digest", sa.LargeBinary(), nullable=False),
        sa.Column("issued_at", sa.String(), nullable=False),
        sa.Column("valid_until", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(proof_id) BETWEEN 1 AND 256 AND authority_revision_number > 0 AND length(authority_revision_digest) = 32 AND length(snapshot_digest) = 32 AND length(graph_digest) = 32 AND length(scope_digest) = 32 AND length(observation_digest) = 32 AND length(binding_revision_digest) = 32 AND length(driver_profile_digest) = 32 AND length(matrix_row_digest) = 32 AND length(test_evidence_digest) = 32 AND length(device_identity_digest) = 32 AND length(options_digest) = 32",
            name="ck_device_work_authority_proofs_digests",
        ),
        sa.CheckConstraint(
            "length(capability_id) BETWEEN 1 AND 256 AND length(device_purpose) BETWEEN 1 AND 256 AND length(operation) BETWEEN 1 AND 256 AND length(media_type) BETWEEN 1 AND 256 AND contract_major > 0",
            name="ck_device_work_authority_proofs_identity",
        ),
        sa.CheckConstraint(
            "valid_until > issued_at",
            name="ck_device_work_authority_proofs_validity",
        ),
        sa.ForeignKeyConstraint(
            ["authority_revision_id"],
            ["device_authority_revisions.revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["binding_revision_id"],
            ["device_binding_revisions.revision_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["driver_profile_id"],
            ["device_driver_profiles.profile_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["matrix_row_id"],
            ["hardware_certification_matrix_rows.row_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_evidence_id"],
            ["device_test_evidence.evidence_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("proof_id"),
        sa.UniqueConstraint(
            "admission_id", name="uq_device_work_authority_proofs_admission"
        ),
    )
    op.create_index(
        "idx_device_work_authority_proofs_graph",
        "device_work_authority_proofs",
        ["binding_revision_id", "test_evidence_id", "issued_at"],
    )

    op.create_table(
        "device_work_authority_proof_subjects",
        sa.Column("proof_id", sa.String(), nullable=False),
        sa.Column("subject_kind", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("subject_digest", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "subject_kind IN ('authority_revision', 'driver_profile', 'certification_matrix_row', 'binding_revision', 'device_test_evidence', 'device_observation') AND length(subject_id) BETWEEN 1 AND 256 AND length(subject_digest) = 32",
            name="ck_device_work_authority_proof_subjects_identity",
        ),
        sa.ForeignKeyConstraint(
            ["proof_id"],
            ["device_work_authority_proofs.proof_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("proof_id", "subject_kind", "subject_id"),
    )
    op.create_index(
        "idx_device_work_authority_proof_subjects_subject",
        "device_work_authority_proof_subjects",
        ["subject_kind", "subject_id", "subject_digest"],
    )

    _add_public_authority_proof_column()
    _create_authority_triggers()


def downgrade() -> None:
    _drop_authority_triggers()
    _drop_public_authority_proof_column()
    op.drop_index(
        "idx_device_work_authority_proof_subjects_subject",
        table_name="device_work_authority_proof_subjects",
    )
    op.drop_table("device_work_authority_proof_subjects")
    op.drop_index(
        "idx_device_work_authority_proofs_graph",
        table_name="device_work_authority_proofs",
    )
    op.drop_table("device_work_authority_proofs")
    op.drop_index(
        "idx_device_authority_revocations_revision",
        table_name="device_authority_revocations",
    )
    op.drop_table("device_authority_revocations")
    op.drop_index(
        "idx_device_binding_authority_state_binding",
        table_name="device_binding_authority_state",
    )
    op.drop_index(
        "uq_device_binding_authority_state_active_scope_purpose",
        table_name="device_binding_authority_state",
    )
    op.drop_table("device_binding_authority_state")
    op.drop_index(
        "idx_device_observations_device_observed_at",
        table_name="device_observations",
    )
    op.drop_table("device_observations")
    op.drop_index(
        "idx_device_test_evidence_revision", table_name="device_test_evidence"
    )
    op.drop_table("device_test_evidence")
    op.drop_index(
        "idx_device_binding_revisions_scope", table_name="device_binding_revisions"
    )
    op.drop_table("device_binding_revisions")
    op.drop_index(
        "idx_hardware_certification_matrix_rows_profile_digest",
        table_name="hardware_certification_matrix_rows",
    )
    op.drop_table("hardware_certification_matrix_rows")
    op.drop_index(
        "idx_device_driver_profiles_driver_version",
        table_name="device_driver_profiles",
    )
    op.drop_table("device_driver_profiles")
    op.drop_table("device_authority_state")
    op.drop_index(
        "uq_device_authority_revisions_digest",
        table_name="device_authority_revisions",
    )
    op.drop_index(
        "idx_device_authority_revisions_signer_key",
        table_name="device_authority_revisions",
    )
    op.drop_table("device_authority_revisions")
    op.drop_index(
        "idx_device_authority_signer_keys_purpose_state",
        table_name="device_authority_signer_keys",
    )
    op.drop_table("device_authority_signer_keys")
    _restore_admission_failure_codes()


def _expand_admission_failure_codes() -> None:
    _rewrite_admission_lifecycle(
        "(state IN ('staging', 'finalizing') AND job_id IS NULL AND accepted_at IS NULL AND failed_at IS NULL AND failure_code IS NULL AND idempotency_expires_at > created_at) OR "
        "(state = 'accepted' AND job_id IS NOT NULL AND job_id = planned_job_id AND accepted_at IS NOT NULL AND failed_at IS NULL AND failure_code IS NULL AND idempotency_expires_at > accepted_at AND content_expires_at > accepted_at) OR "
        "(state = 'aborted' AND job_id IS NULL AND accepted_at IS NULL AND failed_at IS NOT NULL AND failure_code IN ('expired', 'recovery_uncertain', 'service_unavailable', 'capability_changed', 'certification_required') AND idempotency_expires_at > failed_at)"
    )


def _restore_admission_failure_codes() -> None:
    _rewrite_admission_lifecycle(
        "(state IN ('staging', 'finalizing') AND job_id IS NULL AND accepted_at IS NULL AND failed_at IS NULL AND failure_code IS NULL AND idempotency_expires_at > created_at) OR "
        "(state = 'accepted' AND job_id IS NOT NULL AND job_id = planned_job_id AND accepted_at IS NOT NULL AND failed_at IS NULL AND failure_code IS NULL AND idempotency_expires_at > accepted_at AND content_expires_at > accepted_at) OR "
        "(state = 'aborted' AND job_id IS NULL AND accepted_at IS NULL AND failed_at IS NOT NULL AND failure_code IN ('expired', 'recovery_uncertain', 'service_unavailable') AND idempotency_expires_at > failed_at)"
    )


def _rewrite_admission_lifecycle(lifecycle_sql: str) -> None:
    connection = op.get_bind()
    trigger_rows = connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'trigger' AND sql LIKE '%device_work_admissions%'"
    ).fetchall()
    for name, _sql in trigger_rows:
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    try:
        with op.batch_alter_table("device_work_admissions", recreate="always") as batch:
            batch.drop_constraint("ck_device_work_admissions_lifecycle", type_="check")
            batch.create_check_constraint(
                "ck_device_work_admissions_lifecycle", lifecycle_sql
            )
    finally:
        for _name, sql in trigger_rows:
            connection.exec_driver_sql(sql)


def _add_public_authority_proof_column() -> None:
    _rewrite_public_print_jobs(
        lambda batch: batch.add_column(
            sa.Column(
                "authority_proof_id",
                sa.String(),
                sa.ForeignKey(
                    "device_work_authority_proofs.proof_id",
                    ondelete="RESTRICT",
                    name="fk_public_print_jobs_authority_proof_id",
                ),
                nullable=True,
            )
        )
    )


def _drop_public_authority_proof_column() -> None:
    _rewrite_public_print_jobs(lambda batch: batch.drop_column("authority_proof_id"))


def _rewrite_public_print_jobs(operation) -> None:  # type: ignore[no-untyped-def]
    connection = op.get_bind()
    trigger_rows = connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'trigger' AND sql LIKE '%public_print_jobs%'"
    ).fetchall()
    for name, _sql in trigger_rows:
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{name}"')
    try:
        with op.batch_alter_table("public_print_jobs", recreate="always") as batch:
            operation(batch)
    finally:
        for _name, sql in trigger_rows:
            connection.exec_driver_sql(sql)


def _create_authority_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER ck_device_authority_signer_keys_identity_immutable
        BEFORE UPDATE OF key_id, purpose, public_key, not_before, not_after
        ON device_authority_signer_keys
        BEGIN
            SELECT RAISE(ABORT, 'authority signer key identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_authority_signer_keys_lifecycle
        BEFORE UPDATE OF state, retired_at ON device_authority_signer_keys
        WHEN (NEW.state = 'active' AND NEW.retired_at IS NOT NULL)
          OR (NEW.state = 'retired' AND NEW.retired_at IS NULL)
          OR (OLD.state = 'retired' AND NEW.state = 'active')
        BEGIN
            SELECT RAISE(ABORT, 'authority signer key lifecycle is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_authority_revisions_signer_purpose
        BEFORE INSERT ON device_authority_revisions
        WHEN NOT EXISTS (
            SELECT 1 FROM device_authority_signer_keys AS signer
            WHERE signer.key_id = NEW.signer_key_id
              AND signer.purpose = 'authority_revision'
        )
        BEGIN
            SELECT RAISE(ABORT, 'authority revision signer purpose is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_authority_state_current_revision
        BEFORE INSERT ON device_authority_state
        WHEN NEW.current_revision_id IS NOT NULL
         AND NOT EXISTS (
            SELECT 1 FROM device_authority_revisions AS revision
            WHERE revision.revision_id = NEW.current_revision_id
              AND revision.revision_number = NEW.current_revision_number
        )
        BEGIN
            SELECT RAISE(ABORT, 'authority state revision link is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_authority_state_revision_update
        BEFORE UPDATE OF current_revision_id, current_revision_number
        ON device_authority_state
        WHEN NEW.current_revision_number < OLD.current_revision_number
          OR (NEW.current_revision_number = OLD.current_revision_number
              AND NEW.current_revision_id IS NOT OLD.current_revision_id)
          OR (NEW.current_revision_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM device_authority_revisions AS revision
              WHERE revision.revision_id = NEW.current_revision_id
                AND revision.revision_number = NEW.current_revision_number
          ))
        BEGIN
            SELECT RAISE(ABORT, 'authority state revision update is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_authority_state_quarantine_terminal
        BEFORE UPDATE OF status ON device_authority_state
        WHEN OLD.status = 'quarantined' AND NEW.status != 'quarantined'
        BEGIN
            SELECT RAISE(ABORT, 'authority quarantine is terminal');
        END
        """
    )

    for table, message in _IMMUTABLE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER ck_{table}_immutable
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{message} is immutable');
            END
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER ck_{table}_no_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{message} cannot be deleted');
            END
            """
        )

    for table, purpose in (
        ("device_driver_profiles", "driver_profile"),
        ("hardware_certification_matrix_rows", "certification_matrix"),
        ("device_binding_revisions", "binding_revision"),
        ("device_test_evidence", "device_test_evidence"),
        ("device_observations", "device_observation"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER ck_{table}_signer_purpose
            BEFORE INSERT ON {table}
            WHEN NOT EXISTS (
                SELECT 1 FROM device_authority_signer_keys AS signer
                WHERE signer.key_id = NEW.signer_key_id
                  AND signer.purpose = '{purpose}'
            )
            BEGIN
                SELECT RAISE(ABORT, '{table} signer purpose is invalid');
            END
            """
        )

    op.execute(
        """
        CREATE TRIGGER ck_device_driver_profiles_content_free
        BEFORE INSERT ON device_driver_profiles
        WHEN EXISTS (
            SELECT 1 FROM json_tree(NEW.capabilities)
            WHERE key IN ('receipt_payload', 'raw_driver_message', 'document_bytes')
        )
        BEGIN
            SELECT RAISE(ABORT, 'Driver Profile contains forbidden content');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_hardware_certification_matrix_rows_links
        BEFORE INSERT ON hardware_certification_matrix_rows
        WHEN NOT EXISTS (
            SELECT 1
            FROM devices AS device
            JOIN device_driver_profiles AS profile
              ON profile.profile_digest = NEW.driver_profile_digest
             AND profile.driver_id = NEW.driver_id
             AND profile.authority_revision_id = NEW.authority_revision_id
             AND EXISTS (
                 SELECT 1
                 FROM json_each(profile.capabilities) AS capability
                 WHERE json_extract(capability.value, '$.capability_id') = NEW.capability_id
             )
            WHERE device.id = NEW.device_id
              AND device.driver_key = NEW.driver_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'certification matrix graph is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_binding_revisions_links
        BEFORE INSERT ON device_binding_revisions
        WHEN NOT EXISTS (
            SELECT 1
            FROM hardware_certification_matrix_rows AS matrix
            JOIN device_driver_profiles AS profile
              ON profile.profile_digest = NEW.driver_profile_digest
             AND profile.authority_revision_id = NEW.authority_revision_id
            WHERE matrix.row_id = NEW.matrix_row_id
              AND matrix.authority_revision_id = NEW.authority_revision_id
              AND matrix.device_id = NEW.device_id
              AND matrix.device_identity_digest = NEW.device_identity_digest
              AND matrix.driver_profile_digest = NEW.driver_profile_digest
              AND matrix.capability_id = NEW.capability_id
              AND EXISTS (
                  SELECT 1
                  FROM json_each(profile.capabilities) AS capability
                  WHERE json_extract(capability.value, '$.capability_id') = NEW.capability_id
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'Binding Revision graph is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_test_evidence_links
        BEFORE INSERT ON device_test_evidence
        WHEN NOT EXISTS (
            SELECT 1
            FROM device_binding_revisions AS binding
            JOIN hardware_certification_matrix_rows AS matrix
              ON matrix.row_id = NEW.matrix_row_id
             AND matrix.authority_revision_id = NEW.authority_revision_id
            WHERE binding.revision_id = NEW.revision_id
              AND binding.authority_revision_id = NEW.authority_revision_id
              AND binding.device_id = NEW.device_id
              AND binding.device_identity_digest = NEW.device_identity_digest
              AND binding.capability_id = NEW.capability_id
              AND binding.driver_profile_digest = NEW.driver_profile_digest
              AND binding.matrix_row_id = NEW.matrix_row_id
              AND matrix.device_id = NEW.device_id
              AND matrix.device_identity_digest = NEW.device_identity_digest
              AND matrix.driver_profile_digest = NEW.driver_profile_digest
              AND matrix.capability_id = NEW.capability_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'Device Test graph is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_binding_authority_state_graph
        BEFORE INSERT ON device_binding_authority_state
        WHEN NEW.status = 'active' AND NOT EXISTS (
            SELECT 1
            FROM device_binding_revisions AS binding
            JOIN device_test_evidence AS evidence
              ON evidence.evidence_id = NEW.active_test_evidence_id
             AND evidence.revision_id = binding.revision_id
             AND evidence.authority_revision_id = binding.authority_revision_id
             AND evidence.device_id = binding.device_id
             AND evidence.device_identity_digest = binding.device_identity_digest
             AND evidence.capability_id = binding.capability_id
             AND evidence.driver_profile_digest = binding.driver_profile_digest
             AND evidence.matrix_row_id = binding.matrix_row_id
             AND evidence.result = 'passed'
            WHERE binding.revision_id = NEW.active_revision_id
              AND binding.database = NEW.database
              AND binding.organization_id = NEW.organization_id
              AND binding.site_id = NEW.site_id
              AND binding.scope_kind = NEW.scope_kind
              AND coalesce(binding.pos_configuration_id, '') = coalesce(NEW.pos_configuration_id, '')
              AND binding.device_purpose = NEW.device_purpose
        )
        BEGIN
            SELECT RAISE(ABORT, 'active Binding Revision graph is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_binding_authority_state_graph_update
        BEFORE UPDATE ON device_binding_authority_state
        WHEN NEW.status = 'active' AND NOT EXISTS (
            SELECT 1
            FROM device_binding_revisions AS binding
            JOIN device_test_evidence AS evidence
              ON evidence.evidence_id = NEW.active_test_evidence_id
             AND evidence.revision_id = binding.revision_id
             AND evidence.authority_revision_id = binding.authority_revision_id
             AND evidence.device_id = binding.device_id
             AND evidence.device_identity_digest = binding.device_identity_digest
             AND evidence.capability_id = binding.capability_id
             AND evidence.driver_profile_digest = binding.driver_profile_digest
             AND evidence.matrix_row_id = binding.matrix_row_id
             AND evidence.result = 'passed'
            WHERE binding.revision_id = NEW.active_revision_id
              AND binding.database = NEW.database
              AND binding.organization_id = NEW.organization_id
              AND binding.site_id = NEW.site_id
              AND binding.scope_kind = NEW.scope_kind
              AND coalesce(binding.pos_configuration_id, '') = coalesce(NEW.pos_configuration_id, '')
              AND binding.device_purpose = NEW.device_purpose
        )
        BEGIN
            SELECT RAISE(ABORT, 'active Binding Revision graph is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_authority_revocations_signer_purpose
        BEFORE INSERT ON device_authority_revocations
        WHEN NOT EXISTS (
            SELECT 1 FROM device_authority_signer_keys AS signer
            WHERE signer.key_id = NEW.signer_key_id
              AND signer.purpose = 'authority_revocation'
        )
        BEGIN
            SELECT RAISE(ABORT, 'authority revocation signer purpose is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_authority_revocations_subject
        BEFORE INSERT ON device_authority_revocations
        WHEN NOT (
            (NEW.subject_kind = 'authority_revision' AND EXISTS (
                SELECT 1 FROM device_authority_revisions AS subject
                WHERE subject.revision_id = NEW.subject_id
                  AND subject.revision_digest = NEW.subject_digest
                  AND NEW.authority_revision_id = subject.revision_id
            ))
            OR (NEW.subject_kind = 'driver_profile' AND EXISTS (
                SELECT 1 FROM device_driver_profiles AS subject
                WHERE subject.profile_id = NEW.subject_id
                  AND subject.profile_digest = NEW.subject_digest
                  AND subject.authority_revision_id = NEW.authority_revision_id
            ))
            OR (NEW.subject_kind = 'certification_matrix_row' AND EXISTS (
                SELECT 1 FROM hardware_certification_matrix_rows AS subject
                WHERE subject.row_id = NEW.subject_id
                  AND subject.matrix_row_digest = NEW.subject_digest
                  AND subject.authority_revision_id = NEW.authority_revision_id
            ))
            OR (NEW.subject_kind = 'binding_revision' AND EXISTS (
                SELECT 1 FROM device_binding_revisions AS subject
                WHERE subject.revision_id = NEW.subject_id
                  AND subject.binding_digest = NEW.subject_digest
                  AND subject.authority_revision_id = NEW.authority_revision_id
            ))
            OR (NEW.subject_kind = 'device_test_evidence' AND EXISTS (
                SELECT 1 FROM device_test_evidence AS subject
                WHERE subject.evidence_id = NEW.subject_id
                  AND subject.evidence_digest = NEW.subject_digest
                  AND subject.authority_revision_id = NEW.authority_revision_id
            ))
        )
        BEGIN
            SELECT RAISE(ABORT, 'authority revocation subject link is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_work_authority_proofs_graph
        BEFORE INSERT ON device_work_authority_proofs
        WHEN NOT EXISTS (
            SELECT 1
            FROM device_work_admissions AS admission
            JOIN device_authority_revisions AS authority
              ON authority.revision_id = NEW.authority_revision_id
             AND authority.revision_number = NEW.authority_revision_number
             AND authority.revision_digest = NEW.authority_revision_digest
            JOIN device_binding_revisions AS binding
              ON binding.revision_id = NEW.binding_revision_id
             AND binding.binding_digest = NEW.binding_revision_digest
            JOIN device_driver_profiles AS profile
              ON profile.profile_id = NEW.driver_profile_id
             AND profile.profile_digest = NEW.driver_profile_digest
            JOIN hardware_certification_matrix_rows AS matrix
              ON matrix.row_id = NEW.matrix_row_id
             AND matrix.matrix_row_digest = NEW.matrix_row_digest
            JOIN device_test_evidence AS evidence
              ON evidence.evidence_id = NEW.test_evidence_id
             AND evidence.evidence_digest = NEW.test_evidence_digest
            WHERE admission.id = NEW.admission_id
              AND admission.binding_revision_id = NEW.binding_revision_id
              AND admission.device_id = NEW.device_id
              AND admission.operation = NEW.operation
              AND admission.media_type = NEW.media_type
              AND admission.contract_major = NEW.contract_major
              AND admission.normalized_options_digest = NEW.options_digest
              AND binding.authority_revision_id = NEW.authority_revision_id
              AND binding.driver_profile_digest = NEW.driver_profile_digest
              AND binding.matrix_row_id = NEW.matrix_row_id
              AND binding.device_id = NEW.device_id
              AND binding.device_identity_digest = NEW.device_identity_digest
              AND binding.capability_id = NEW.capability_id
              AND binding.device_purpose = NEW.device_purpose
              AND matrix.authority_revision_id = NEW.authority_revision_id
              AND matrix.device_id = NEW.device_id
              AND matrix.device_identity_digest = NEW.device_identity_digest
              AND matrix.driver_profile_digest = NEW.driver_profile_digest
              AND matrix.capability_id = NEW.capability_id
              AND evidence.revision_id = NEW.binding_revision_id
              AND evidence.device_id = NEW.device_id
              AND evidence.device_identity_digest = NEW.device_identity_digest
              AND evidence.capability_id = NEW.capability_id
              AND evidence.driver_profile_digest = NEW.driver_profile_digest
              AND evidence.matrix_row_id = NEW.matrix_row_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'authority proof graph is invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_work_admissions_authority_proof
        BEFORE INSERT ON device_work_admissions
        WHEN NEW.state = 'accepted' AND NOT EXISTS (
            SELECT 1 FROM device_work_authority_proofs AS proof
            WHERE proof.admission_id = NEW.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'accepted admission requires an authority proof');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_device_work_admissions_authority_proof_update
        BEFORE UPDATE OF state, id ON device_work_admissions
        WHEN NEW.state = 'accepted' AND NOT EXISTS (
            SELECT 1 FROM device_work_authority_proofs AS proof
            WHERE proof.admission_id = NEW.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'accepted admission requires an authority proof');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_public_print_jobs_authority_proof
        BEFORE INSERT ON public_print_jobs
        WHEN NEW.state = 'accepted' AND NOT EXISTS (
            SELECT 1
            FROM device_work_authority_proofs AS proof
            WHERE proof.proof_id = NEW.authority_proof_id
              AND proof.admission_id = NEW.admission_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'accepted public job requires an exact authority proof');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER ck_public_print_jobs_authority_proof_update
        BEFORE UPDATE OF state, admission_id, authority_proof_id ON public_print_jobs
        WHEN NEW.state = 'accepted' AND NOT EXISTS (
            SELECT 1
            FROM device_work_authority_proofs AS proof
            WHERE proof.proof_id = NEW.authority_proof_id
              AND proof.admission_id = NEW.admission_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'accepted public job requires an exact authority proof');
        END
        """
    )


def _drop_authority_triggers() -> None:
    names = [
        "ck_public_print_jobs_authority_proof_update",
        "ck_public_print_jobs_authority_proof",
        "ck_device_work_admissions_authority_proof_update",
        "ck_device_work_admissions_authority_proof",
        "ck_device_work_authority_proofs_graph",
        "ck_device_authority_revocations_subject",
        "ck_device_authority_revocations_signer_purpose",
        "ck_device_binding_authority_state_graph_update",
        "ck_device_binding_authority_state_graph",
        "ck_device_test_evidence_links",
        "ck_device_binding_revisions_links",
        "ck_hardware_certification_matrix_rows_links",
        "ck_device_driver_profiles_content_free",
    ]
    names.extend(
        f"ck_{table}_signer_purpose"
        for table, _purpose in (
            ("device_driver_profiles", "driver_profile"),
            ("hardware_certification_matrix_rows", "certification_matrix"),
            ("device_binding_revisions", "binding_revision"),
            ("device_test_evidence", "device_test_evidence"),
            ("device_observations", "device_observation"),
        )
    )
    for table, _message in reversed(_IMMUTABLE_TABLES):
        names.extend((f"ck_{table}_no_delete", f"ck_{table}_immutable"))
    names.extend(
        (
            "ck_device_authority_state_quarantine_terminal",
            "ck_device_authority_state_revision_update",
            "ck_device_authority_state_current_revision",
            "ck_device_authority_revisions_signer_purpose",
            "ck_device_authority_signer_keys_lifecycle",
            "ck_device_authority_signer_keys_identity_immutable",
        )
    )
    for name in names:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
