from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
)
from sqlalchemy.engine import Engine, URL
from sqlalchemy.pool import NullPool

metadata = MetaData()

devices_table = Table(
    "devices",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("driver_key", String, nullable=False),
    Column("identity_transport", String, nullable=False),
    Column("identity_serial_number", String),
    Column("identity_vendor_id", Integer),
    Column("identity_product_id", Integer),
    Column("identity_os_instance_id", String),
    Column("identity_port_id", String),
    Column("name", String, nullable=False),
    Column("connection_state", String, nullable=False),
    Column("first_seen_at", String, nullable=False),
    Column("last_seen_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("is_default", Boolean, nullable=False),
    Column("preferred_transport", String),
    Column("capabilities_json", Text, nullable=False),
    Column("metadata_json", Text, nullable=False),
)

device_events_table = Table(
    "device_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("device_id", String, ForeignKey("devices.id"), nullable=False),
    Column("event_type", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("occurred_at", String, nullable=False),
)
Index(
    "idx_device_events_device_id",
    device_events_table.c.device_id,
    device_events_table.c.sequence.desc(),
)

jobs_table = Table(
    "jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("operation", String, nullable=False),
    Column(
        "device_id",
        String,
        nullable=False,
    ),
    Column("device_kind", String, nullable=False),
    Column("device_name", String, nullable=False),
    Column("state", String, nullable=False),
    Column("request_json", Text, nullable=False),
    Column("request_metadata_json", Text, nullable=False),
    Column("content_kind", String),
    Column("command_kind", String),
    Column("attempt_count", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("queued_at", String, nullable=False),
    Column("next_run_at", String, nullable=False),
    Column("started_at", String),
    Column("finished_at", String),
    Column("lease_expires_at", String),
    Column("result_json", Text),
    Column("last_error_code", String),
    Column("last_error_detail", Text),
)
Index(
    "idx_jobs_state_next_run_at",
    jobs_table.c.state,
    jobs_table.c.next_run_at,
    jobs_table.c.created_at,
)
Index("idx_jobs_device_id", jobs_table.c.device_id, jobs_table.c.created_at)

job_attempts_table = Table(
    "job_attempts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", String, ForeignKey("jobs.id"), nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("state", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("finished_at", String),
    Column("error_code", String),
    Column("error_detail", Text),
    Column("result_json", Text),
)
Index(
    "idx_job_attempts_job_id",
    job_attempts_table.c.job_id,
    job_attempts_table.c.attempt_number.desc(),
)

job_events_table = Table(
    "job_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("job_id", String, ForeignKey("jobs.id"), nullable=False),
    Column("event_type", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("occurred_at", String, nullable=False),
)
Index(
    "idx_job_events_job_id",
    job_events_table.c.job_id,
    job_events_table.c.sequence.desc(),
)

gateway_inbound_commands_table = Table(
    "gateway_inbound_commands",
    metadata,
    Column("command_id", String, primary_key=True),
    Column("message_id", String, nullable=False),
    Column("sequence", Integer),
    Column("dispatch_epoch", Integer),
    Column("message_type", String, nullable=False),
    Column("state", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("response_json", Text),
    Column("error_code", String),
    Column("error_detail", Text),
    Column("job_id", String),
    Column("received_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
Index(
    "idx_gateway_inbound_job_id",
    gateway_inbound_commands_table.c.job_id,
    gateway_inbound_commands_table.c.updated_at.desc(),
)
Index(
    "idx_gateway_inbound_sequence",
    gateway_inbound_commands_table.c.sequence,
    unique=True,
    sqlite_where=gateway_inbound_commands_table.c.sequence.is_not(None),
)
Index(
    "uq_gateway_inbound_dispatch_epoch_sequence",
    gateway_inbound_commands_table.c.dispatch_epoch,
    gateway_inbound_commands_table.c.sequence,
    unique=True,
    sqlite_where=gateway_inbound_commands_table.c.dispatch_epoch.is_not(None),
)
Index(
    "idx_gateway_inbound_managed_work",
    func.json_extract(
        gateway_inbound_commands_table.c.payload_json, "$.payload.managed_work_id"
    ),
    sqlite_where=gateway_inbound_commands_table.c.message_type
    == "controller.command.dispatch_device_work",
)

gateway_managed_dispatch_state_table = Table(
    "gateway_managed_dispatch_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("dispatch_epoch", Integer, nullable=False),
    Column("last_sequence", Integer, nullable=False),
    Column("updated_at", String, nullable=False),
    CheckConstraint("id = 1", name="ck_gateway_managed_dispatch_state_singleton"),
    CheckConstraint(
        "dispatch_epoch > 0 AND last_sequence >= 0",
        name="ck_gateway_managed_dispatch_state_position",
    ),
)

gateway_outbox_table = Table(
    "gateway_outbox",
    metadata,
    Column("message_id", String, primary_key=True),
    Column("message_type", String, nullable=False),
    Column("state", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("correlation_id", String),
    Column("dedupe_key", String),
    Column("recipient_scope", Text),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("sent_at", String),
    Column("last_error", Text),
)
Index(
    "idx_gateway_outbox_dedupe_key",
    gateway_outbox_table.c.dedupe_key,
    unique=True,
    sqlite_where=gateway_outbox_table.c.dedupe_key.is_not(None),
)
Index(
    "idx_gateway_outbox_state_created_at",
    gateway_outbox_table.c.state,
    gateway_outbox_table.c.created_at,
)

gateway_print_job_cursors_table = Table(
    "gateway_print_job_cursors",
    metadata,
    Column("recipient_scope", Text, primary_key=True),
    Column("last_sequence", Integer, nullable=False),
    CheckConstraint(
        "last_sequence BETWEEN 0 AND 9007199254740991",
        name="ck_gateway_print_job_cursor_sequence",
    ),
)

public_print_jobs_table = Table(
    "public_print_jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "admission_id",
        String,
        ForeignKey("device_work_admissions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "authority_proof_id",
        String,
        ForeignKey("device_work_authority_proofs.proof_id", ondelete="RESTRICT"),
    ),
    Column("intent_id", String, nullable=False),
    Column(
        "device_id",
        String,
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("scope_kind", String, nullable=False),
    Column("organization_id", String, nullable=False),
    Column("site_id", String, nullable=False),
    Column("pos_configuration_id", String),
    Column("paired_client_id", String),
    Column("origin_kind", String, nullable=False),
    Column("origin_json", Text, nullable=False),
    Column("managed_work_id", String),
    Column("state", String, nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("accepted_at", String, nullable=False),
    Column("started_at", String),
    Column("terminal_at", String),
    Column("expires_at", String, nullable=False),
    Column("retryable", Boolean, nullable=False),
    Column("error_code", String),
    Column("message_key", String),
    Column("confirmation_evidence", String),
    Column("contract_version", String, nullable=False),
    CheckConstraint("state_version > 0", name="ck_public_print_jobs_state_version"),
    CheckConstraint(
        "scope_kind IN ('paired_client', 'device_manager')",
        name="ck_public_print_jobs_scope_kind",
    ),
    CheckConstraint(
        "length(organization_id) > 0 AND length(site_id) > 0",
        name="ck_public_print_jobs_scope_identity",
    ),
    CheckConstraint(
        "(scope_kind = 'paired_client' AND pos_configuration_id IS NOT NULL AND paired_client_id IS NOT NULL) OR "
        "(scope_kind = 'device_manager' AND ((pos_configuration_id IS NULL AND paired_client_id IS NULL) OR (pos_configuration_id IS NOT NULL AND paired_client_id IS NOT NULL)))",
        name="ck_public_print_jobs_scope_shape",
    ),
    CheckConstraint(
        "origin_kind IN ('pos', 'preparation', 'report')",
        name="ck_public_print_jobs_origin_kind",
    ),
    CheckConstraint(
        "json_valid(origin_json) AND json_type(origin_json) = 'object' AND "
        "origin_json = json(origin_json) AND "
        "json_extract(origin_json, '$.receipt_payload') IS NULL AND "
        "json_extract(origin_json, '$.raw_driver_message') IS NULL AND "
        "json_extract(origin_json, '$.document_bytes') IS NULL",
        name="ck_public_print_jobs_origin_json",
    ),
    CheckConstraint(
        "length(CAST(origin_json AS BLOB)) <= 16384",
        name="ck_public_print_jobs_origin_json_size",
    ),
    CheckConstraint("expires_at > accepted_at", name="ck_public_print_jobs_expires_at"),
    CheckConstraint(
        "started_at IS NULL OR started_at >= accepted_at",
        name="ck_public_print_jobs_started_at",
    ),
    CheckConstraint(
        "terminal_at IS NULL OR terminal_at >= accepted_at",
        name="ck_public_print_jobs_terminal_at",
    ),
    CheckConstraint(
        "started_at IS NULL OR terminal_at IS NULL OR terminal_at >= started_at",
        name="ck_public_print_jobs_lifecycle_order",
    ),
    CheckConstraint(
        "confirmation_evidence IS NULL OR confirmation_evidence IN ('device', 'spooler', 'transport')",
        name="ck_public_print_jobs_evidence",
    ),
    CheckConstraint(
        "(state = 'accepted' AND started_at IS NULL AND terminal_at IS NULL AND confirmation_evidence IS NULL AND error_code IS NULL) OR "
        "(state = 'in_progress' AND started_at IS NOT NULL AND terminal_at IS NULL AND confirmation_evidence IS NULL AND error_code IS NULL) OR "
        "(state = 'output_confirmed' AND started_at IS NOT NULL AND terminal_at IS NOT NULL AND confirmation_evidence IS NOT NULL AND error_code IS NULL) OR "
        "(state = 'failed' AND terminal_at IS NOT NULL AND confirmation_evidence IS NULL AND error_code IS NOT NULL) OR "
        "(state = 'outcome_unknown' AND started_at IS NOT NULL AND terminal_at IS NOT NULL AND confirmation_evidence IS NULL AND error_code IS NOT NULL) OR "
        "(state IN ('expired', 'canceled') AND started_at IS NULL AND terminal_at IS NOT NULL AND confirmation_evidence IS NULL AND error_code IS NULL)",
        name="ck_public_print_jobs_lifecycle",
    ),
)
Index(
    "idx_public_print_jobs_scope_accepted_at",
    public_print_jobs_table.c.scope_kind,
    public_print_jobs_table.c.organization_id,
    public_print_jobs_table.c.site_id,
    public_print_jobs_table.c.pos_configuration_id,
    public_print_jobs_table.c.paired_client_id,
    public_print_jobs_table.c.accepted_at,
)
Index(
    "idx_public_print_jobs_state_expires_at",
    public_print_jobs_table.c.state,
    public_print_jobs_table.c.expires_at,
)
Index(
    "uq_public_print_jobs_intent_id", public_print_jobs_table.c.intent_id, unique=True
)
Index(
    "uq_public_print_jobs_admission_id",
    public_print_jobs_table.c.admission_id,
    unique=True,
)
Index(
    "uq_public_print_jobs_managed_work_id",
    public_print_jobs_table.c.managed_work_id,
    unique=True,
    sqlite_where=public_print_jobs_table.c.managed_work_id.is_not(None),
)

device_work_admissions_table = Table(
    "device_work_admissions",
    metadata,
    Column("id", String, primary_key=True),
    Column("planned_job_id", String, nullable=False, unique=True),
    Column("database", String, nullable=False),
    Column("scope_kind", String, nullable=False),
    Column("managed_work_id", String),
    Column("organization_id", String, nullable=False),
    Column("site_id", String, nullable=False),
    Column("pos_configuration_id", String),
    Column("paired_client_id", String),
    Column("idempotency_key", String, nullable=False),
    Column("fingerprint", LargeBinary, nullable=False),
    Column("state", String, nullable=False),
    # The public job owns the foreign key. This reverse identifier avoids an
    # unresolvable cycle while keeping reconciliation lookups indexed.
    Column("job_id", String),
    Column("intent_id", String, nullable=False),
    Column(
        "device_id",
        String,
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("deadline_at", String, nullable=False),
    Column("original_size_bytes", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("accepted_at", String),
    Column("failed_at", String),
    Column("failure_code", String),
    Column("idempotency_expires_at", String, nullable=False),
    Column("content_expires_at", String, nullable=False),
    Column("actor_id", String, nullable=False),
    Column("binding_revision_id", String, nullable=False),
    Column("authorization_digest", LargeBinary, nullable=False),
    Column("operation", String, nullable=False),
    Column("media_type", String, nullable=False),
    Column("normalized_options_digest", LargeBinary, nullable=False),
    Column("normalized_options", LargeBinary),
    Column("grant_scope_digest", LargeBinary, nullable=False),
    # The exact Client Grant used for admission. These fields let execution
    # recheck the same grant after queueing without storing bearer material.
    Column("grant_id", String),
    Column("grant_pairing_id", String),
    Column("grant_generation", Integer),
    Column("grant_authorization_digest", LargeBinary),
    Column("origin_submission_key", String, nullable=False),
    Column("origin_kind", String, nullable=False),
    Column("origin_json", Text, nullable=False),
    Column("contract_major", Integer, nullable=False),
    Column("copy_ordinal", Integer, nullable=False),
    CheckConstraint(
        "scope_kind IN ('paired_client', 'device_manager') AND "
        "length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND "
        "length(idempotency_key) BETWEEN 1 AND 512 AND length(planned_job_id) BETWEEN 1 AND 128 AND "
        "length(database) BETWEEN 1 AND 256 AND length(actor_id) BETWEEN 1 AND 256 AND "
        "length(binding_revision_id) BETWEEN 1 AND 256 AND length(origin_submission_key) BETWEEN 1 AND 512",
        name="ck_device_work_admissions_identity",
    ),
    CheckConstraint(
        "(scope_kind = 'paired_client' AND pos_configuration_id IS NOT NULL AND paired_client_id IS NOT NULL) OR "
        "(scope_kind = 'device_manager' AND ((pos_configuration_id IS NULL AND paired_client_id IS NULL) OR (pos_configuration_id IS NOT NULL AND paired_client_id IS NOT NULL)))",
        name="ck_device_work_admissions_scope_shape",
    ),
    CheckConstraint(
        "length(fingerprint) = 32", name="ck_device_work_admissions_fingerprint"
    ),
    CheckConstraint(
        "length(authorization_digest) = 32 AND length(normalized_options_digest) = 32 AND length(grant_scope_digest) = 32",
        name="ck_device_work_admissions_manifest_digests",
    ),
    CheckConstraint(
        "length(operation) BETWEEN 1 AND 64 AND length(media_type) BETWEEN 1 AND 128",
        name="ck_device_work_admissions_manifest_operation",
    ),
    CheckConstraint(
        "length(device_id) > 0 AND length(intent_id) > 0",
        name="ck_device_work_admissions_work_identity",
    ),
    CheckConstraint(
        "origin_kind IN ('pos', 'preparation', 'report') AND json_valid(origin_json) AND "
        "json_type(origin_json) = 'object' AND origin_json = json(origin_json) AND "
        "json_extract(origin_json, '$.receipt_payload') IS NULL AND "
        "json_extract(origin_json, '$.raw_driver_message') IS NULL AND "
        "json_extract(origin_json, '$.document_bytes') IS NULL AND "
        "length(CAST(origin_json AS BLOB)) <= 16384",
        name="ck_device_work_admissions_manifest_origin",
    ),
    CheckConstraint(
        "contract_major > 0 AND copy_ordinal >= 0",
        name="ck_device_work_admissions_manifest_version",
    ),
    CheckConstraint(
        "original_size_bytes > 0", name="ck_device_work_admissions_original_size"
    ),
    CheckConstraint(
        "deadline_at > created_at AND content_expires_at > created_at",
        name="ck_device_work_admissions_retention",
    ),
    CheckConstraint(
        "(state IN ('staging', 'finalizing') AND job_id IS NULL AND accepted_at IS NULL AND failed_at IS NULL AND failure_code IS NULL AND idempotency_expires_at > created_at) OR "
        "(state = 'accepted' AND job_id IS NOT NULL AND job_id = planned_job_id AND accepted_at IS NOT NULL AND failed_at IS NULL AND failure_code IS NULL AND idempotency_expires_at > accepted_at AND content_expires_at > accepted_at) OR "
        "(state = 'aborted' AND job_id IS NULL AND accepted_at IS NULL AND failed_at IS NOT NULL AND failure_code IN ('expired', 'recovery_uncertain', 'service_unavailable', 'capability_changed', 'certification_required') AND idempotency_expires_at > failed_at)",
        name="ck_device_work_admissions_lifecycle",
    ),
)
Index(
    "uq_device_work_admissions_paired_idempotency",
    device_work_admissions_table.c.scope_kind,
    device_work_admissions_table.c.organization_id,
    device_work_admissions_table.c.site_id,
    device_work_admissions_table.c.pos_configuration_id,
    device_work_admissions_table.c.paired_client_id,
    device_work_admissions_table.c.idempotency_key,
    unique=True,
    sqlite_where=device_work_admissions_table.c.scope_kind == "paired_client",
)
Index(
    "uq_device_work_admissions_manager_idempotency",
    device_work_admissions_table.c.scope_kind,
    device_work_admissions_table.c.organization_id,
    device_work_admissions_table.c.site_id,
    device_work_admissions_table.c.idempotency_key,
    unique=True,
    sqlite_where=(device_work_admissions_table.c.scope_kind == "device_manager")
    & device_work_admissions_table.c.pos_configuration_id.is_(None),
)
Index(
    "uq_device_work_admissions_managed_work_id",
    device_work_admissions_table.c.managed_work_id,
    unique=True,
    sqlite_where=device_work_admissions_table.c.managed_work_id.is_not(None),
)
Index(
    "uq_device_work_admissions_manager_pos_idempotency",
    device_work_admissions_table.c.scope_kind,
    device_work_admissions_table.c.organization_id,
    device_work_admissions_table.c.site_id,
    device_work_admissions_table.c.pos_configuration_id,
    device_work_admissions_table.c.paired_client_id,
    device_work_admissions_table.c.idempotency_key,
    unique=True,
    sqlite_where=(device_work_admissions_table.c.scope_kind == "device_manager")
    & device_work_admissions_table.c.pos_configuration_id.is_not(None),
)
Index(
    "uq_device_work_admissions_origin_submission",
    device_work_admissions_table.c.scope_kind,
    device_work_admissions_table.c.organization_id,
    device_work_admissions_table.c.site_id,
    device_work_admissions_table.c.pos_configuration_id,
    device_work_admissions_table.c.paired_client_id,
    device_work_admissions_table.c.origin_submission_key,
    unique=True,
    sqlite_where=device_work_admissions_table.c.pos_configuration_id.is_not(None),
)
Index(
    "idx_device_work_admissions_idempotency_expiry",
    device_work_admissions_table.c.idempotency_expires_at,
    device_work_admissions_table.c.state,
)
Index(
    "idx_device_work_admissions_job_id",
    device_work_admissions_table.c.job_id,
)
Index(
    "uq_device_work_admissions_job_id",
    device_work_admissions_table.c.job_id,
    unique=True,
    sqlite_where=device_work_admissions_table.c.job_id.is_not(None),
)

spool_root_keys_table = Table(
    "spool_root_keys",
    metadata,
    Column("root_version", Integer, primary_key=True),
    Column("key_identifier", String, nullable=False, unique=True),
    Column("state", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("rotate_after", String, nullable=False),
    Column("retiring_at", String),
    Column("retired_at", String),
    CheckConstraint(
        "root_version BETWEEN 1 AND 4294967295",
        name="ck_spool_root_keys_version",
    ),
    CheckConstraint("length(key_identifier) > 0", name="ck_spool_root_keys_identity"),
    CheckConstraint(
        "state IN ('provisioning', 'current', 'retiring', 'retired')",
        name="ck_spool_root_keys_state",
    ),
    CheckConstraint(
        "(state = 'provisioning' AND retiring_at IS NULL AND retired_at IS NULL) OR "
        "(state = 'current' AND retiring_at IS NULL AND retired_at IS NULL) OR "
        "(state = 'retiring' AND retiring_at IS NOT NULL AND retired_at IS NULL) OR "
        "(state = 'retired' AND retiring_at IS NOT NULL AND retired_at IS NOT NULL)",
        name="ck_spool_root_keys_lifecycle",
    ),
    CheckConstraint("rotate_after > created_at", name="ck_spool_root_keys_rotation"),
    CheckConstraint(
        "retiring_at IS NULL OR retiring_at >= created_at",
        name="ck_spool_root_keys_retiring_at",
    ),
    CheckConstraint(
        "retired_at IS NULL OR (retiring_at IS NOT NULL AND retired_at >= retiring_at)",
        name="ck_spool_root_keys_retired_at",
    ),
)
Index(
    "uq_spool_root_keys_current",
    spool_root_keys_table.c.state,
    unique=True,
    sqlite_where=spool_root_keys_table.c.state == "current",
)
Index(
    "uq_spool_root_keys_retiring",
    spool_root_keys_table.c.state,
    unique=True,
    sqlite_where=spool_root_keys_table.c.state == "retiring",
)

spool_security_state_table = Table(
    "spool_security_state",
    metadata,
    Column("singleton_id", Integer, primary_key=True),
    Column("anchor_kind", String, nullable=False),
    Column("anchor_identifier", String, nullable=False),
    Column("anchor_evidence_digest", LargeBinary, nullable=False),
    Column("committed_generation", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("quarantined_at", String),
    Column("quarantine_reason_code", String),
    Column("updated_at", String, nullable=False),
    CheckConstraint("singleton_id = 1", name="ck_spool_security_state_singleton"),
    CheckConstraint(
        "anchor_kind IN ('tpm2', 'software') AND length(anchor_identifier) > 0",
        name="ck_spool_security_state_anchor_kind",
    ),
    CheckConstraint(
        "length(anchor_evidence_digest) = 32",
        name="ck_spool_security_state_anchor_evidence",
    ),
    CheckConstraint(
        "committed_generation > 0", name="ck_spool_security_state_generation"
    ),
    CheckConstraint(
        "(status = 'ready' AND quarantined_at IS NULL AND quarantine_reason_code IS NULL) OR "
        "(status = 'quarantined' AND quarantined_at IS NOT NULL AND quarantine_reason_code IS NOT NULL)",
        name="ck_spool_security_state_status",
    ),
)

spool_job_keys_table = Table(
    "spool_job_keys",
    metadata,
    Column(
        "job_id",
        String,
        ForeignKey("public_print_jobs.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "root_version",
        Integer,
        ForeignKey("spool_root_keys.root_version", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("intent_id", String, nullable=False),
    Column("format_version", Integer, nullable=False),
    Column("wrap_nonce", LargeBinary, nullable=False),
    Column("wrapped_key", LargeBinary, nullable=False),
    Column("created_at", String, nullable=False),
    Column("key_deleted_at", String),
    CheckConstraint("format_version = 1", name="ck_spool_job_keys_format_version"),
    CheckConstraint("length(intent_id) > 0", name="ck_spool_job_keys_intent"),
    CheckConstraint("length(wrap_nonce) = 12", name="ck_spool_job_keys_wrap_nonce"),
    CheckConstraint(
        "key_deleted_at IS NOT NULL OR length(wrapped_key) = 48",
        name="ck_spool_job_keys_wrapped_key",
    ),
    CheckConstraint(
        "key_deleted_at IS NULL OR key_deleted_at >= created_at",
        name="ck_spool_job_keys_deleted_at",
    ),
    CheckConstraint(
        "(key_deleted_at IS NULL AND length(wrapped_key) = 48) OR "
        "(key_deleted_at IS NOT NULL AND length(wrapped_key) = 0)",
        name="ck_spool_job_keys_key_lifecycle",
    ),
    UniqueConstraint("root_version", "wrap_nonce", name="uq_spool_job_keys_root_nonce"),
)

spool_admission_keys_table = Table(
    "spool_admission_keys",
    metadata,
    Column(
        "admission_id",
        String,
        ForeignKey("device_work_admissions.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("planned_job_id", String, nullable=False, unique=True),
    Column(
        "root_version",
        Integer,
        ForeignKey("spool_root_keys.root_version", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("intent_id", String, nullable=False),
    Column("format_version", Integer, nullable=False),
    Column("wrap_nonce", LargeBinary, nullable=False),
    Column("wrapped_key", LargeBinary, nullable=False),
    Column("created_at", String, nullable=False),
    CheckConstraint(
        "length(planned_job_id) BETWEEN 1 AND 128", name="ck_spool_admission_keys_job"
    ),
    CheckConstraint("length(intent_id) > 0", name="ck_spool_admission_keys_intent"),
    CheckConstraint(
        "format_version = 1", name="ck_spool_admission_keys_format_version"
    ),
    CheckConstraint(
        "length(wrap_nonce) = 12", name="ck_spool_admission_keys_wrap_nonce"
    ),
    CheckConstraint(
        "length(wrapped_key) = 48", name="ck_spool_admission_keys_wrapped_key"
    ),
    UniqueConstraint(
        "root_version", "wrap_nonce", name="uq_spool_admission_keys_root_nonce"
    ),
)

spool_root_key_provisioning_table = Table(
    "spool_root_key_provisioning",
    metadata,
    Column(
        "root_version",
        Integer,
        ForeignKey("spool_root_keys.root_version", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("state", String, nullable=False),
    Column("protected_key_identifier", String, nullable=False),
    Column("provisioned_at", String),
    Column("error_code", String),
    Column("updated_at", String, nullable=False),
    CheckConstraint(
        "state IN ('provisioning', 'available', 'quarantined')",
        name="ck_spool_root_key_provisioning_state",
    ),
    CheckConstraint(
        "length(protected_key_identifier) BETWEEN 1 AND 512",
        name="ck_spool_root_key_provisioning_identity",
    ),
    CheckConstraint(
        "(state = 'available' AND provisioned_at IS NOT NULL AND error_code IS NULL) OR "
        "(state = 'provisioning' AND provisioned_at IS NULL AND error_code IS NULL) OR "
        "(state = 'quarantined' AND provisioned_at IS NULL AND error_code IS NOT NULL)",
        name="ck_spool_root_key_provisioning_lifecycle",
    ),
)

spool_reservations_table = Table(
    "spool_reservations",
    metadata,
    Column("id", String, primary_key=True),
    Column("reservation_kind", String, nullable=False),
    Column(
        "admission_id",
        String,
        ForeignKey("device_work_admissions.id", ondelete="RESTRICT"),
    ),
    Column("job_id", String, ForeignKey("public_print_jobs.id", ondelete="RESTRICT")),
    Column(
        "device_id",
        String,
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("owner_id", String, nullable=False),
    Column("owner_generation", Integer, nullable=False),
    Column("queue_slots", Integer, nullable=False),
    Column("original_bytes", Integer, nullable=False),
    Column("persistent_bytes", Integer, nullable=False),
    Column("temporary_bytes", Integer, nullable=False),
    Column("state", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("released_at", String),
    CheckConstraint(
        "reservation_kind IN ('admission', 'execution_temp')",
        name="ck_spool_reservations_kind",
    ),
    CheckConstraint(
        "length(device_id) > 0 AND length(owner_id) > 0",
        name="ck_spool_reservations_identity",
    ),
    CheckConstraint("owner_generation > 0", name="ck_spool_reservations_owner"),
    CheckConstraint(
        "expires_at > created_at",
        name="ck_spool_reservations_expiry",
    ),
    CheckConstraint(
        "state IN ('held', 'committed', 'released', 'expired')",
        name="ck_spool_reservations_state",
    ),
    CheckConstraint(
        "((state IN ('held', 'committed')) AND released_at IS NULL) OR "
        "((state IN ('released', 'expired')) AND released_at IS NOT NULL)",
        name="ck_spool_reservations_release",
    ),
    CheckConstraint(
        "(reservation_kind = 'admission' AND admission_id IS NOT NULL AND queue_slots = 1 AND original_bytes > 0 AND persistent_bytes >= original_bytes + 65536 AND temporary_bytes = 0 AND "
        "((state = 'held' AND job_id IS NULL) OR (state = 'committed' AND job_id IS NOT NULL) OR (state IN ('released', 'expired')))) OR "
        "(reservation_kind = 'execution_temp' AND admission_id IS NULL AND job_id IS NOT NULL AND queue_slots = 0 AND original_bytes = 0 AND persistent_bytes = 0 AND temporary_bytes > 0 AND state IN ('held', 'released', 'expired'))",
        name="ck_spool_reservations_dimensions",
    ),
)
Index(
    "idx_spool_reservations_device_state",
    spool_reservations_table.c.device_id,
    spool_reservations_table.c.state,
)
Index(
    "idx_spool_reservations_expiry",
    spool_reservations_table.c.expires_at,
    spool_reservations_table.c.state,
)
Index(
    "idx_spool_reservations_admission_id",
    spool_reservations_table.c.admission_id,
)
Index(
    "uq_spool_reservations_admission_active",
    spool_reservations_table.c.admission_id,
    unique=True,
    sqlite_where=spool_reservations_table.c.admission_id.is_not(None)
    & spool_reservations_table.c.state.in_(["held", "committed"]),
)
Index(
    "uq_spool_reservations_execution_device_active",
    spool_reservations_table.c.device_id,
    unique=True,
    sqlite_where=(spool_reservations_table.c.reservation_kind == "execution_temp")
    & spool_reservations_table.c.state
    == "held",
)

spool_nonce_reservations_table = Table(
    "spool_nonce_reservations",
    metadata,
    Column("id", String, primary_key=True),
    Column("domain", String, nullable=False),
    Column(
        "root_version",
        Integer,
        ForeignKey("spool_root_keys.root_version", ondelete="RESTRICT"),
    ),
    Column("planned_job_id", String, nullable=True),
    Column("purpose", String, nullable=False),
    Column(
        "admission_id",
        String,
        ForeignKey("device_work_admissions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("nonce", LargeBinary, nullable=False),
    Column("created_at", String, nullable=False),
    CheckConstraint(
        "(domain = 'root_wrap' AND purpose = 'data_key_wrap' AND root_version IS NOT NULL AND planned_job_id IS NULL) OR "
        "(domain = 'artifact' AND purpose IN ('original', 'derived_raster') AND root_version IS NULL AND planned_job_id IS NOT NULL)",
        name="ck_spool_nonce_reservations_domain",
    ),
    CheckConstraint(
        "(planned_job_id IS NULL OR length(planned_job_id) BETWEEN 1 AND 128) AND length(nonce) = 12",
        name="ck_spool_nonce_reservations_identity",
    ),
    UniqueConstraint(
        "domain",
        "root_version",
        "planned_job_id",
        "nonce",
        name="uq_spool_nonce_reservations_domain_nonce",
    ),
)
Index(
    "uq_spool_nonce_reservations_root_nonce",
    spool_nonce_reservations_table.c.root_version,
    spool_nonce_reservations_table.c.nonce,
    unique=True,
    sqlite_where=spool_nonce_reservations_table.c.domain == "root_wrap",
)
Index(
    "uq_spool_nonce_reservations_job_nonce",
    spool_nonce_reservations_table.c.planned_job_id,
    spool_nonce_reservations_table.c.nonce,
    unique=True,
    sqlite_where=spool_nonce_reservations_table.c.domain == "artifact",
)
Index(
    "idx_spool_nonce_reservations_admission_id",
    spool_nonce_reservations_table.c.admission_id,
)

spool_artifacts_table = Table(
    "spool_artifacts",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "admission_id",
        String,
        ForeignKey("device_work_admissions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "reservation_id",
        String,
        ForeignKey("spool_reservations.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("job_id", String, ForeignKey("public_print_jobs.id", ondelete="RESTRICT")),
    Column("aad_job_id", String, nullable=False),
    Column("intent_id", String, nullable=False),
    Column("format_version", Integer, nullable=False),
    Column("artifact_kind", String, nullable=False),
    Column("storage_ref", String, nullable=False, unique=True),
    Column("nonce", LargeBinary, nullable=False),
    Column("plaintext_size_bytes", Integer, nullable=False),
    Column("ciphertext_size_bytes", Integer, nullable=False),
    Column("plaintext_sha256", LargeBinary, nullable=False),
    Column("state", String, nullable=False),
    Column("retention_policy", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("committed_at", String),
    Column("delete_after", String),
    Column("deleted_at", String),
    Column("delete_attempts", Integer, nullable=False),
    Column("last_delete_error_code", String),
    CheckConstraint(
        "length(aad_job_id) > 0 AND length(intent_id) > 0 AND length(storage_ref) > 0",
        name="ck_spool_artifacts_identity",
    ),
    CheckConstraint("format_version = 1", name="ck_spool_artifacts_format_version"),
    CheckConstraint(
        "artifact_kind IN ('original', 'derived_raster')",
        name="ck_spool_artifacts_kind",
    ),
    CheckConstraint("length(nonce) = 12", name="ck_spool_artifacts_nonce"),
    CheckConstraint(
        "length(plaintext_sha256) = 32", name="ck_spool_artifacts_plaintext_sha256"
    ),
    CheckConstraint(
        "plaintext_size_bytes > 0", name="ck_spool_artifacts_plaintext_size"
    ),
    CheckConstraint(
        "ciphertext_size_bytes >= plaintext_size_bytes + 16",
        name="ck_spool_artifacts_ciphertext_size",
    ),
    CheckConstraint(
        "job_id IS NULL OR job_id = aad_job_id", name="ck_spool_artifacts_job_id"
    ),
    CheckConstraint("delete_attempts >= 0", name="ck_spool_artifacts_delete_attempts"),
    CheckConstraint(
        "last_delete_error_code IS NULL OR delete_attempts > 0",
        name="ck_spool_artifacts_delete_error",
    ),
    CheckConstraint(
        "retention_policy IN ('active', 'success_immediate', 'integrity_immediate', 'failure_24h')",
        name="ck_spool_artifacts_retention_policy",
    ),
    CheckConstraint(
        "(state = 'staging' AND job_id IS NULL AND committed_at IS NULL AND retention_policy = 'active' AND delete_after IS NULL AND deleted_at IS NULL AND delete_attempts = 0) OR "
        "(state = 'committed' AND job_id IS NOT NULL AND committed_at IS NOT NULL AND retention_policy = 'active' AND delete_after IS NULL AND deleted_at IS NULL) OR "
        "(state = 'delete_pending' AND job_id IS NOT NULL AND committed_at IS NOT NULL AND retention_policy IN ('success_immediate', 'integrity_immediate', 'failure_24h') AND delete_after IS NOT NULL AND deleted_at IS NULL) OR "
        "(state = 'deleted' AND job_id IS NOT NULL AND committed_at IS NOT NULL AND retention_policy IN ('success_immediate', 'integrity_immediate', 'failure_24h') AND delete_after IS NOT NULL AND deleted_at IS NOT NULL)",
        name="ck_spool_artifacts_lifecycle",
    ),
)
Index(
    "idx_spool_artifacts_job_state",
    spool_artifacts_table.c.job_id,
    spool_artifacts_table.c.state,
)
Index(
    "idx_spool_artifacts_delete_after",
    spool_artifacts_table.c.delete_after,
    sqlite_where=spool_artifacts_table.c.delete_after.is_not(None),
)
Index(
    "uq_spool_artifacts_aad_nonce",
    spool_artifacts_table.c.aad_job_id,
    spool_artifacts_table.c.nonce,
    unique=True,
)
Index(
    "uq_spool_artifacts_admission_kind",
    spool_artifacts_table.c.admission_id,
    spool_artifacts_table.c.artifact_kind,
    unique=True,
)

public_print_job_events_table = Table(
    "public_print_job_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column(
        "job_id",
        String,
        ForeignKey("public_print_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("state_version", Integer, nullable=False),
    Column("event_type", String, nullable=False),
    Column("snapshot_json", Text, nullable=False),
    Column("occurred_at", String, nullable=False),
    CheckConstraint(
        "state_version > 0", name="ck_public_print_job_events_state_version"
    ),
    CheckConstraint(
        "length(event_type) BETWEEN 1 AND 64",
        name="ck_public_print_job_events_event_type",
    ),
    CheckConstraint(
        "json_valid(snapshot_json) AND json_type(snapshot_json) = 'object' AND length(CAST(snapshot_json AS BLOB)) <= 16384",
        name="ck_public_print_job_events_snapshot",
    ),
    CheckConstraint(
        "json_extract(snapshot_json, '$.receipt_payload') IS NULL AND "
        "json_extract(snapshot_json, '$.raw_driver_message') IS NULL AND "
        "json_extract(snapshot_json, '$.document_bytes') IS NULL",
        name="ck_public_print_job_events_content_free",
    ),
    UniqueConstraint(
        "job_id",
        "state_version",
        name="uq_public_print_job_events_job_version",
    ),
    sqlite_autoincrement=True,
)
Index(
    "idx_public_print_job_events_job_sequence",
    public_print_job_events_table.c.job_id,
    public_print_job_events_table.c.sequence.desc(),
)
Index(
    "idx_public_print_job_events_sequence",
    public_print_job_events_table.c.sequence,
)

# Private execution authority. Public Print Job state does not identify the
# worker that owns a lease, so stale workers cannot fence one another without
# this separate ledger.
physical_execution_attempts_table = Table(
    "physical_execution_attempts",
    metadata,
    Column("attempt_id", String, primary_key=True),
    Column(
        "job_id",
        String,
        ForeignKey("public_print_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("lease_id", String, nullable=False, unique=True),
    Column("owner_id", String, nullable=False),
    Column("owner_generation", Integer, nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("phase", String, nullable=False),
    Column("lease_expires_at", String, nullable=False),
    Column("marker_id", String),
    Column("marker_at", String),
    Column("marker_sequence", Integer),
    Column("io_permission_issued", Boolean, nullable=False),
    Column("execution_id", String, nullable=False),
    Column("platform_job_id", String),
    Column("result_json", Text),
    Column("error_code", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("finished_at", String),
    CheckConstraint(
        "length(attempt_id) BETWEEN 1 AND 128 AND length(job_id) BETWEEN 1 AND 128 AND "
        "length(lease_id) BETWEEN 1 AND 128 AND length(owner_id) BETWEEN 1 AND 256 AND "
        "owner_generation > 0 AND attempt_number > 0 AND state_version > 0 AND "
        "length(execution_id) BETWEEN 1 AND 128",
        name="ck_physical_execution_attempts_identity",
    ),
    CheckConstraint(
        "phase IN ('claimed', 'prepared', 'marker_committed', 'permission_delivered', 'finished', 'retryable', 'recovered')",
        name="ck_physical_execution_attempts_phase",
    ),
    CheckConstraint(
        "(marker_id IS NULL AND marker_at IS NULL AND marker_sequence IS NULL) OR "
        "(marker_id IS NOT NULL AND marker_at IS NOT NULL AND marker_sequence IS NOT NULL AND marker_sequence > 0)",
        name="ck_physical_execution_attempts_marker",
    ),
)
Index(
    "idx_physical_execution_attempts_job_phase",
    physical_execution_attempts_table.c.job_id,
    physical_execution_attempts_table.c.phase,
)
Index(
    "idx_physical_execution_attempts_owner",
    physical_execution_attempts_table.c.owner_id,
    physical_execution_attempts_table.c.owner_generation,
    physical_execution_attempts_table.c.phase,
)
Index(
    "uq_physical_execution_attempts_active_job",
    physical_execution_attempts_table.c.job_id,
    unique=True,
    sqlite_where=physical_execution_attempts_table.c.phase.in_(
        ("claimed", "prepared", "marker_committed", "permission_delivered")
    ),
)

drawer_intents_table = Table(
    "drawer_intents",
    metadata,
    Column("id", String, primary_key=True),
    Column("intent_id", String, nullable=False),
    Column("database", String, nullable=False),
    Column("organization_id", String, nullable=False),
    Column("site_id", String, nullable=False),
    Column("pos_configuration_id", String, nullable=False),
    Column("paired_client_id", String, nullable=False),
    Column("actor_id", String, nullable=False),
    Column("device_id", String, nullable=False),
    Column("binding_revision_id", String, nullable=False),
    Column("pos_session_id", String, nullable=False),
    Column("action_sequence", Integer, nullable=False),
    Column("reason", String, nullable=False),
    Column("contract_major", Integer, nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("fingerprint", LargeBinary, nullable=False),
    Column("state", String, nullable=False),
    Column("accepted_at", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("started_at", String),
    Column("terminal_at", String),
    Column("error_code", String),
    Column("message_key", String),
    Column("printer_name", String),
    Column("transport", String),
    Column("updated_at", String, nullable=False),
    CheckConstraint(
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
    CheckConstraint("length(fingerprint) = 32", name="ck_drawer_intents_fingerprint"),
    CheckConstraint(
        "state IN ('accepted', 'in_progress', 'succeeded', 'outcome_unknown', 'failed')",
        name="ck_drawer_intents_state",
    ),
    CheckConstraint("expires_at > accepted_at", name="ck_drawer_intents_retention"),
    CheckConstraint(
        "started_at IS NULL OR started_at >= accepted_at",
        name="ck_drawer_intents_started_at",
    ),
    CheckConstraint(
        "terminal_at IS NULL OR terminal_at >= accepted_at",
        name="ck_drawer_intents_terminal_at",
    ),
    CheckConstraint(
        "(state = 'accepted' AND started_at IS NULL AND terminal_at IS NULL AND error_code IS NULL AND message_key IS NULL) OR "
        "(state = 'in_progress' AND started_at IS NOT NULL AND terminal_at IS NULL AND error_code IS NULL AND message_key IS NULL) OR "
        "(state = 'succeeded' AND started_at IS NOT NULL AND terminal_at IS NOT NULL AND error_code IS NULL AND message_key IS NULL) OR "
        "(state = 'outcome_unknown' AND started_at IS NOT NULL AND terminal_at IS NOT NULL AND error_code IS NOT NULL AND message_key IS NOT NULL) OR "
        "(state = 'failed' AND started_at IS NULL AND terminal_at IS NOT NULL AND error_code IS NOT NULL AND message_key IS NOT NULL)",
        name="ck_drawer_intents_lifecycle",
    ),
)
Index(
    "uq_drawer_intents_action",
    drawer_intents_table.c.database,
    drawer_intents_table.c.organization_id,
    drawer_intents_table.c.site_id,
    drawer_intents_table.c.pos_configuration_id,
    drawer_intents_table.c.pos_session_id,
    drawer_intents_table.c.actor_id,
    drawer_intents_table.c.device_id,
    drawer_intents_table.c.action_sequence,
    unique=True,
)
Index(
    "uq_drawer_intents_scope_identity",
    drawer_intents_table.c.database,
    drawer_intents_table.c.organization_id,
    drawer_intents_table.c.site_id,
    drawer_intents_table.c.pos_configuration_id,
    drawer_intents_table.c.paired_client_id,
    drawer_intents_table.c.intent_id,
    unique=True,
)
Index(
    "idx_drawer_intents_scope_accepted_at",
    drawer_intents_table.c.database,
    drawer_intents_table.c.organization_id,
    drawer_intents_table.c.site_id,
    drawer_intents_table.c.pos_configuration_id,
    drawer_intents_table.c.paired_client_id,
    drawer_intents_table.c.accepted_at,
)
Index("idx_drawer_intents_expiry", drawer_intents_table.c.expires_at)

device_stream_state_table = Table(
    "device_stream_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("current_sequence", Integer, nullable=False),
    CheckConstraint("id = 1", name="ck_device_stream_state_singleton"),
    CheckConstraint(
        "current_sequence BETWEEN 0 AND 9007199254740991",
        name="ck_device_stream_state_sequence",
    ),
)

device_stream_generations_table = Table(
    "device_stream_generations",
    metadata,
    Column("scope_digest", String, primary_key=True),
    Column("generation", Integer, nullable=False),
    CheckConstraint(
        "length(scope_digest) BETWEEN 8 AND 256",
        name="ck_device_stream_generations_scope",
    ),
    CheckConstraint(
        "generation BETWEEN 0 AND 9007199254740991",
        name="ck_device_stream_generations_value",
    ),
)

device_authority_signer_keys_table = Table(
    "device_authority_signer_keys",
    metadata,
    Column("key_id", String, primary_key=True),
    Column("purpose", String, nullable=False),
    Column("public_key", LargeBinary, nullable=False),
    Column("state", String, nullable=False),
    Column("not_before", String, nullable=False),
    Column("not_after", String),
    Column("retired_at", String),
    CheckConstraint(
        "length(key_id) BETWEEN 1 AND 256",
        name="ck_device_authority_signer_keys_identity",
    ),
    CheckConstraint(
        "purpose IN ('authority_revision', 'driver_profile', 'certification_matrix', 'binding_revision', 'device_test_evidence', 'device_observation', 'authority_revocation')",
        name="ck_device_authority_signer_keys_purpose",
    ),
    CheckConstraint(
        "length(public_key) = 32",
        name="ck_device_authority_signer_keys_public_key",
    ),
    CheckConstraint(
        "state IN ('active', 'retired') AND ((state = 'active' AND retired_at IS NULL) OR (state = 'retired' AND retired_at IS NOT NULL))",
        name="ck_device_authority_signer_keys_state",
    ),
    CheckConstraint(
        "(not_after IS NULL OR not_after > not_before) AND (retired_at IS NULL OR retired_at >= not_before)",
        name="ck_device_authority_signer_keys_validity",
    ),
    UniqueConstraint("public_key", name="uq_device_authority_signer_keys_public_key"),
)

Index(
    "idx_device_authority_signer_keys_purpose_state",
    device_authority_signer_keys_table.c.purpose,
    device_authority_signer_keys_table.c.state,
)

device_authority_revisions_table = Table(
    "device_authority_revisions",
    metadata,
    Column("revision_id", String, primary_key=True),
    Column("revision_number", Integer, nullable=False),
    Column("manifest_digest", LargeBinary, nullable=False),
    Column("manifest", Text),
    Column("effective_at", String, nullable=False),
    Column("expires_at", String),
    Column("revision_digest", LargeBinary, nullable=False),
    Column(
        "signer_key_id",
        String,
        ForeignKey("device_authority_signer_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("signature", LargeBinary, nullable=False),
    CheckConstraint(
        "length(revision_id) BETWEEN 1 AND 256 AND revision_number > 0",
        name="ck_device_authority_revisions_identity",
    ),
    CheckConstraint(
        "length(manifest_digest) = 32 AND length(revision_digest) = 32 AND length(signature) = 64",
        name="ck_device_authority_revisions_cryptography",
    ),
    CheckConstraint(
        "expires_at IS NULL OR expires_at > effective_at",
        name="ck_device_authority_revisions_validity",
    ),
    UniqueConstraint("revision_number", name="uq_device_authority_revisions_number"),
)

Index(
    "idx_device_authority_revisions_signer_key",
    device_authority_revisions_table.c.signer_key_id,
)
Index(
    "uq_device_authority_revisions_digest",
    device_authority_revisions_table.c.revision_digest,
    unique=True,
)

device_authority_state_table = Table(
    "device_authority_state",
    metadata,
    Column("singleton_id", Integer, primary_key=True),
    Column(
        "current_revision_id",
        String,
        ForeignKey("device_authority_revisions.revision_id", ondelete="RESTRICT"),
    ),
    Column("current_revision_number", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("quarantined_at", String),
    Column("quarantine_reason_code", String),
    Column("updated_at", String, nullable=False),
    CheckConstraint("singleton_id = 1", name="ck_device_authority_state_singleton"),
    CheckConstraint(
        "current_revision_number >= 0",
        name="ck_device_authority_state_revision",
    ),
    CheckConstraint(
        "(current_revision_number = 0 AND current_revision_id IS NULL) OR "
        "(current_revision_number > 0 AND current_revision_id IS NOT NULL)",
        name="ck_device_authority_state_current_revision",
    ),
    CheckConstraint(
        "status IN ('ready', 'quarantined')",
        name="ck_device_authority_state_status",
    ),
    CheckConstraint(
        "(status = 'ready' AND quarantined_at IS NULL AND quarantine_reason_code IS NULL) OR "
        "(status = 'quarantined' AND quarantined_at IS NOT NULL AND quarantine_reason_code IS NOT NULL)",
        name="ck_device_authority_state_quarantine",
    ),
)

device_driver_profiles_table = Table(
    "device_driver_profiles",
    metadata,
    Column("profile_id", String, primary_key=True),
    Column("version", String, nullable=False),
    Column("driver_id", String, nullable=False),
    Column("min_agent_version", String, nullable=False),
    Column("capabilities", Text, nullable=False),
    Column("profile_digest", LargeBinary, nullable=False),
    Column(
        "signer_key_id",
        String,
        ForeignKey("device_authority_signer_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("signature", LargeBinary, nullable=False),
    Column("effective_at", String, nullable=False),
    Column("expires_at", String),
    Column(
        "authority_revision_id",
        String,
        ForeignKey("device_authority_revisions.revision_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    CheckConstraint(
        "length(profile_id) BETWEEN 1 AND 256 AND length(version) BETWEEN 1 AND 256 AND "
        "length(driver_id) BETWEEN 1 AND 256 AND length(min_agent_version) BETWEEN 1 AND 256",
        name="ck_device_driver_profiles_identity",
    ),
    CheckConstraint(
        "json_valid(capabilities) AND json_type(capabilities) = 'array' AND "
        "capabilities = json(capabilities) AND length(CAST(capabilities AS BLOB)) <= 16384",
        name="ck_device_driver_profiles_capabilities",
    ),
    CheckConstraint(
        "length(profile_digest) = 32 AND length(signature) = 64",
        name="ck_device_driver_profiles_cryptography",
    ),
    CheckConstraint(
        "expires_at IS NULL OR expires_at > effective_at",
        name="ck_device_driver_profiles_validity",
    ),
    UniqueConstraint("profile_digest", name="uq_device_driver_profiles_digest"),
)

Index(
    "idx_device_driver_profiles_driver_version",
    device_driver_profiles_table.c.driver_id,
    device_driver_profiles_table.c.version,
)

hardware_certification_matrix_rows_table = Table(
    "hardware_certification_matrix_rows",
    metadata,
    Column("row_id", String, primary_key=True),
    Column("version", Integer, nullable=False),
    Column(
        "device_id",
        String,
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("device_identity_digest", LargeBinary, nullable=False),
    Column("manufacturer", String, nullable=False),
    Column("model", String, nullable=False),
    Column("firmware_version", String, nullable=False),
    Column("firmware_build", String, nullable=False),
    Column("driver_id", String, nullable=False),
    Column("driver_profile_digest", LargeBinary, nullable=False),
    Column("capability_id", String, nullable=False),
    Column("platform_backend_id", String, nullable=False),
    Column("connection", String, nullable=False),
    Column("media_profile", String, nullable=False),
    Column("operating_system", String, nullable=False),
    Column("release_set_id", String, nullable=False),
    Column("effective_at", String, nullable=False),
    Column("expires_at", String),
    Column("matrix_row_digest", LargeBinary, nullable=False),
    Column(
        "signer_key_id",
        String,
        ForeignKey("device_authority_signer_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("signature", LargeBinary, nullable=False),
    Column(
        "authority_revision_id",
        String,
        ForeignKey("device_authority_revisions.revision_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    CheckConstraint(
        "length(row_id) BETWEEN 1 AND 256 AND version > 0 AND "
        "length(device_identity_digest) = 32 AND length(driver_id) BETWEEN 1 AND 256 AND "
        "length(driver_profile_digest) = 32 AND length(capability_id) BETWEEN 1 AND 256",
        name="ck_hardware_certification_matrix_rows_identity",
    ),
    CheckConstraint(
        "length(manufacturer) BETWEEN 1 AND 256 AND length(model) BETWEEN 1 AND 256 AND "
        "length(firmware_version) BETWEEN 1 AND 256 AND length(firmware_build) BETWEEN 1 AND 256 AND "
        "length(platform_backend_id) BETWEEN 1 AND 256 AND length(connection) BETWEEN 1 AND 256 AND "
        "length(media_profile) BETWEEN 1 AND 256 AND length(operating_system) BETWEEN 1 AND 256 AND "
        "length(release_set_id) BETWEEN 1 AND 256",
        name="ck_hardware_certification_matrix_rows_fields",
    ),
    CheckConstraint(
        "length(matrix_row_digest) = 32 AND length(signature) = 64",
        name="ck_hardware_certification_matrix_rows_cryptography",
    ),
    CheckConstraint(
        "expires_at IS NULL OR expires_at > effective_at",
        name="ck_hardware_certification_matrix_rows_validity",
    ),
    UniqueConstraint(
        "device_id",
        "version",
        "driver_id",
        "capability_id",
        name="uq_hardware_certification_matrix_rows_graph",
    ),
)

Index(
    "idx_hardware_certification_matrix_rows_profile_digest",
    hardware_certification_matrix_rows_table.c.driver_profile_digest,
)

device_binding_revisions_table = Table(
    "device_binding_revisions",
    metadata,
    Column("revision_id", String, primary_key=True),
    Column("binding_id", String, nullable=False),
    Column("revision_number", Integer, nullable=False),
    Column("database", String, nullable=False),
    Column("organization_id", String, nullable=False),
    Column("site_id", String, nullable=False),
    Column("scope_kind", String, nullable=False),
    Column("pos_configuration_id", String),
    Column("device_purpose", String, nullable=False),
    Column(
        "device_id",
        String,
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("device_identity_digest", LargeBinary, nullable=False),
    Column("capability_id", String, nullable=False),
    Column("driver_profile_digest", LargeBinary, nullable=False),
    Column(
        "matrix_row_id",
        String,
        ForeignKey("hardware_certification_matrix_rows.row_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("options_digest", LargeBinary, nullable=False),
    Column("binding_digest", LargeBinary, nullable=False),
    Column(
        "signer_key_id",
        String,
        ForeignKey("device_authority_signer_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("signature", LargeBinary, nullable=False),
    Column("effective_at", String, nullable=False),
    Column("expires_at", String),
    Column(
        "authority_revision_id",
        String,
        ForeignKey("device_authority_revisions.revision_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    CheckConstraint(
        "length(revision_id) BETWEEN 1 AND 256 AND length(binding_id) BETWEEN 1 AND 256 AND "
        "revision_number > 0 AND length(database) BETWEEN 1 AND 256 AND "
        "length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND "
        "length(device_purpose) BETWEEN 1 AND 256 AND length(device_identity_digest) = 32 AND "
        "length(capability_id) BETWEEN 1 AND 256 AND length(driver_profile_digest) = 32 AND "
        "length(options_digest) = 32",
        name="ck_device_binding_revisions_identity",
    ),
    CheckConstraint(
        "scope_kind IN ('site', 'pos_configuration') AND "
        "((scope_kind = 'site' AND pos_configuration_id IS NULL) OR "
        "(scope_kind = 'pos_configuration' AND length(pos_configuration_id) BETWEEN 1 AND 256))",
        name="ck_device_binding_revisions_scope",
    ),
    CheckConstraint(
        "length(binding_digest) = 32 AND length(signature) = 64",
        name="ck_device_binding_revisions_cryptography",
    ),
    CheckConstraint(
        "expires_at IS NULL OR expires_at > effective_at",
        name="ck_device_binding_revisions_validity",
    ),
    UniqueConstraint(
        "binding_id",
        "revision_number",
        name="uq_device_binding_revisions_number",
    ),
    UniqueConstraint("binding_digest", name="uq_device_binding_revisions_digest"),
)

Index(
    "idx_device_binding_revisions_scope",
    device_binding_revisions_table.c.database,
    device_binding_revisions_table.c.organization_id,
    device_binding_revisions_table.c.site_id,
    device_binding_revisions_table.c.scope_kind,
    device_binding_revisions_table.c.pos_configuration_id,
    device_binding_revisions_table.c.device_purpose,
)

device_test_evidence_table = Table(
    "device_test_evidence",
    metadata,
    Column("evidence_id", String, primary_key=True),
    Column(
        "revision_id",
        String,
        ForeignKey("device_binding_revisions.revision_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "device_id",
        String,
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("device_identity_digest", LargeBinary, nullable=False),
    Column("capability_id", String, nullable=False),
    Column("driver_profile_digest", LargeBinary, nullable=False),
    Column(
        "matrix_row_id",
        String,
        ForeignKey("hardware_certification_matrix_rows.row_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("output_evidence", String, nullable=False),
    Column("result", String, nullable=False),
    Column("test_pattern_digest", LargeBinary, nullable=False),
    Column("tested_at", String, nullable=False),
    Column("valid_until", String),
    Column("evidence_digest", LargeBinary, nullable=False),
    Column(
        "signer_key_id",
        String,
        ForeignKey("device_authority_signer_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("signature", LargeBinary, nullable=False),
    Column(
        "authority_revision_id",
        String,
        ForeignKey("device_authority_revisions.revision_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    CheckConstraint(
        "length(evidence_id) BETWEEN 1 AND 256 AND length(device_identity_digest) = 32 AND "
        "length(capability_id) BETWEEN 1 AND 256 AND length(driver_profile_digest) = 32 AND "
        "length(matrix_row_id) BETWEEN 1 AND 256 AND length(test_pattern_digest) = 32",
        name="ck_device_test_evidence_identity",
    ),
    CheckConstraint(
        "output_evidence IN ('transport', 'spooler', 'device')",
        name="ck_device_test_evidence_output",
    ),
    CheckConstraint(
        "result IN ('passed', 'failed_environment', 'failed_contract')",
        name="ck_device_test_evidence_result",
    ),
    CheckConstraint(
        "length(evidence_digest) = 32 AND length(signature) = 64",
        name="ck_device_test_evidence_cryptography",
    ),
    CheckConstraint(
        "valid_until IS NULL OR valid_until > tested_at",
        name="ck_device_test_evidence_validity",
    ),
    UniqueConstraint("evidence_digest", name="uq_device_test_evidence_digest"),
)

Index(
    "idx_device_test_evidence_revision",
    device_test_evidence_table.c.revision_id,
    device_test_evidence_table.c.result,
)

device_observations_table = Table(
    "device_observations",
    metadata,
    Column("observation_id", String, primary_key=True),
    Column(
        "device_id",
        String,
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("device_identity_digest", LargeBinary, nullable=False),
    Column("driver_id", String, nullable=False),
    Column("driver_profile_digest", LargeBinary, nullable=False),
    Column("platform_backend_id", String, nullable=False),
    Column("connection", String, nullable=False),
    Column("media_profile", String, nullable=False),
    Column("firmware_version", String, nullable=False),
    Column("firmware_build", String, nullable=False),
    Column("operating_system", String, nullable=False),
    Column("ready", Boolean, nullable=False),
    Column("state", String, nullable=False),
    Column("reason", String),
    Column("observed_at", String, nullable=False),
    Column("observation_digest", LargeBinary, nullable=False),
    Column(
        "signer_key_id",
        String,
        ForeignKey("device_authority_signer_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("signature", LargeBinary, nullable=False),
    CheckConstraint(
        "length(observation_id) BETWEEN 1 AND 256 AND length(device_identity_digest) = 32 AND "
        "length(driver_id) BETWEEN 1 AND 256 AND length(driver_profile_digest) = 32 AND "
        "length(platform_backend_id) BETWEEN 1 AND 256 AND length(connection) BETWEEN 1 AND 256 AND "
        "length(media_profile) BETWEEN 1 AND 256 AND length(firmware_version) BETWEEN 1 AND 256 AND "
        "length(firmware_build) BETWEEN 1 AND 256 AND length(operating_system) BETWEEN 1 AND 256 AND "
        "length(state) BETWEEN 1 AND 256",
        name="ck_device_observations_identity",
    ),
    CheckConstraint(
        "length(observation_digest) = 32 AND length(signature) = 64",
        name="ck_device_observations_cryptography",
    ),
)

Index(
    "idx_device_observations_device_observed_at",
    device_observations_table.c.device_id,
    device_observations_table.c.observed_at,
)

device_binding_authority_state_table = Table(
    "device_binding_authority_state",
    metadata,
    Column("state_id", String, primary_key=True),
    Column(
        "active_revision_id",
        String,
        ForeignKey("device_binding_revisions.revision_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "active_test_evidence_id",
        String,
        ForeignKey("device_test_evidence.evidence_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("database", String, nullable=False),
    Column("organization_id", String, nullable=False),
    Column("site_id", String, nullable=False),
    Column("scope_kind", String, nullable=False),
    Column("pos_configuration_id", String),
    Column("device_purpose", String, nullable=False),
    Column("status", String, nullable=False),
    Column("updated_at", String, nullable=False),
    CheckConstraint(
        "length(state_id) BETWEEN 1 AND 256 AND length(database) BETWEEN 1 AND 256 AND "
        "length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND "
        "length(device_purpose) BETWEEN 1 AND 256",
        name="ck_device_binding_authority_state_identity",
    ),
    CheckConstraint(
        "scope_kind IN ('site', 'pos_configuration') AND "
        "((scope_kind = 'site' AND pos_configuration_id IS NULL) OR "
        "(scope_kind = 'pos_configuration' AND length(pos_configuration_id) BETWEEN 1 AND 256))",
        name="ck_device_binding_authority_state_scope",
    ),
    CheckConstraint(
        "status IN ('active', 'inactive')",
        name="ck_device_binding_authority_state_status",
    ),
)

Index(
    "uq_device_binding_authority_state_active_scope_purpose",
    device_binding_authority_state_table.c.database,
    device_binding_authority_state_table.c.organization_id,
    device_binding_authority_state_table.c.site_id,
    device_binding_authority_state_table.c.scope_kind,
    func.coalesce(device_binding_authority_state_table.c.pos_configuration_id, ""),
    device_binding_authority_state_table.c.device_purpose,
    unique=True,
    sqlite_where=device_binding_authority_state_table.c.status == "active",
)
Index(
    "idx_device_binding_authority_state_binding",
    device_binding_authority_state_table.c.active_revision_id,
    device_binding_authority_state_table.c.status,
)

device_authority_revocations_table = Table(
    "device_authority_revocations",
    metadata,
    Column("revocation_id", String, primary_key=True),
    Column(
        "authority_revision_id",
        String,
        ForeignKey("device_authority_revisions.revision_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "signer_key_id",
        String,
        ForeignKey("device_authority_signer_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("subject_kind", String, nullable=False),
    Column("subject_id", String, nullable=False),
    Column("subject_digest", LargeBinary, nullable=False),
    Column("reason_code", String, nullable=False),
    Column("revoked_at", String, nullable=False),
    Column("signature", LargeBinary, nullable=False),
    CheckConstraint(
        "length(revocation_id) BETWEEN 1 AND 256 AND length(subject_id) BETWEEN 1 AND 256 AND "
        "length(reason_code) BETWEEN 1 AND 256 AND length(subject_digest) = 32",
        name="ck_device_authority_revocations_identity",
    ),
    CheckConstraint(
        "subject_kind IN ('authority_revision', 'driver_profile', 'certification_matrix_row', 'binding_revision', 'device_test_evidence')",
        name="ck_device_authority_revocations_subject",
    ),
    CheckConstraint(
        "length(signature) = 64",
        name="ck_device_authority_revocations_cryptography",
    ),
    UniqueConstraint(
        "subject_kind",
        "subject_id",
        "subject_digest",
        name="uq_device_authority_revocations_subject",
    ),
)

Index(
    "idx_device_authority_revocations_revision",
    device_authority_revocations_table.c.authority_revision_id,
    device_authority_revocations_table.c.revoked_at,
)

device_work_authority_proofs_table = Table(
    "device_work_authority_proofs",
    metadata,
    Column("proof_id", String, primary_key=True),
    Column(
        "admission_id",
        String,
        ForeignKey("device_work_admissions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("authority_revision_id", String, nullable=False),
    Column("authority_revision_number", Integer, nullable=False),
    Column("authority_revision_digest", LargeBinary, nullable=False),
    Column("snapshot_digest", LargeBinary, nullable=False),
    Column("graph_digest", LargeBinary, nullable=False),
    Column("scope_digest", LargeBinary, nullable=False),
    Column("observation_digest", LargeBinary, nullable=False),
    Column("binding_revision_id", String, nullable=False),
    Column("binding_revision_digest", LargeBinary, nullable=False),
    Column("driver_profile_id", String, nullable=False),
    Column("driver_profile_digest", LargeBinary, nullable=False),
    Column("matrix_row_id", String, nullable=False),
    Column("matrix_row_digest", LargeBinary, nullable=False),
    Column("test_evidence_id", String, nullable=False),
    Column("test_evidence_digest", LargeBinary, nullable=False),
    Column("device_id", String, nullable=False),
    Column("device_identity_digest", LargeBinary, nullable=False),
    Column("capability_id", String, nullable=False),
    Column("device_purpose", String, nullable=False),
    Column("operation", String, nullable=False),
    Column("media_type", String, nullable=False),
    Column("contract_major", Integer, nullable=False),
    Column("options_digest", LargeBinary, nullable=False),
    Column("issued_at", String, nullable=False),
    Column("valid_until", String, nullable=False),
    # Device Capability proof and Client Grant proof share one admission row.
    # Grant data stays content-free and is only an identity for rechecking.
    Column("grant_id", String),
    Column("grant_pairing_id", String),
    Column("grant_generation", Integer),
    Column("grant_authorization_digest", LargeBinary),
    CheckConstraint(
        "length(proof_id) BETWEEN 1 AND 256 AND authority_revision_number > 0 AND "
        "length(authority_revision_digest) = 32 AND length(snapshot_digest) = 32 AND "
        "length(graph_digest) = 32 AND length(scope_digest) = 32 AND "
        "length(observation_digest) = 32 AND length(binding_revision_digest) = 32 AND "
        "length(driver_profile_digest) = 32 AND length(matrix_row_digest) = 32 AND "
        "length(test_evidence_digest) = 32 AND length(device_identity_digest) = 32 AND "
        "length(options_digest) = 32",
        name="ck_device_work_authority_proofs_digests",
    ),
    CheckConstraint(
        "length(capability_id) BETWEEN 1 AND 256 AND length(device_purpose) BETWEEN 1 AND 256 AND "
        "length(operation) BETWEEN 1 AND 256 AND length(media_type) BETWEEN 1 AND 256 AND "
        "contract_major > 0",
        name="ck_device_work_authority_proofs_identity",
    ),
    CheckConstraint(
        "valid_until > issued_at",
        name="ck_device_work_authority_proofs_validity",
    ),
    ForeignKeyConstraint(
        ["authority_revision_id"],
        ["device_authority_revisions.revision_id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["binding_revision_id"],
        ["device_binding_revisions.revision_id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["driver_profile_id"],
        ["device_driver_profiles.profile_id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["matrix_row_id"],
        ["hardware_certification_matrix_rows.row_id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["test_evidence_id"],
        ["device_test_evidence.evidence_id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["device_id"],
        ["devices.id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("admission_id", name="uq_device_work_authority_proofs_admission"),
)

Index(
    "idx_device_work_authority_proofs_graph",
    device_work_authority_proofs_table.c.binding_revision_id,
    device_work_authority_proofs_table.c.test_evidence_id,
    device_work_authority_proofs_table.c.issued_at,
)

device_work_authority_proof_subjects_table = Table(
    "device_work_authority_proof_subjects",
    metadata,
    Column(
        "proof_id",
        String,
        ForeignKey("device_work_authority_proofs.proof_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("subject_kind", String, primary_key=True),
    Column("subject_id", String, primary_key=True),
    Column("subject_digest", LargeBinary, nullable=False),
    CheckConstraint(
        "subject_kind IN ('authority_revision', 'driver_profile', 'certification_matrix_row', 'binding_revision', 'device_test_evidence', 'device_observation') AND "
        "length(subject_id) BETWEEN 1 AND 256 AND length(subject_digest) = 32",
        name="ck_device_work_authority_proof_subjects_identity",
    ),
)
Index(
    "idx_device_work_authority_proof_subjects_subject",
    device_work_authority_proof_subjects_table.c.subject_kind,
    device_work_authority_proof_subjects_table.c.subject_id,
    device_work_authority_proof_subjects_table.c.subject_digest,
)

pairing_requests_table = Table(
    "client_trust_pairing_requests",
    metadata,
    Column("request_id", String, primary_key=True),
    Column("agent_id", String, nullable=False),
    Column("browser_origin", String, nullable=False),
    Column("agent_endpoint", String, nullable=False),
    Column("database", String, nullable=False),
    Column("company_id", String, nullable=False),
    Column("organization_id", String, nullable=False),
    Column("site_id", String, nullable=False),
    Column("pos_configuration_id", String),
    Column("audience", String, nullable=False),
    Column("browser_jwk_thumbprint", String, nullable=False),
    Column("requested_permissions", Text, nullable=False),
    Column("session_nonce", String, nullable=False),
    Column("phrase", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("state", String, nullable=False),
    CheckConstraint(
        "length(request_id) BETWEEN 1 AND 256 AND length(agent_id) BETWEEN 1 AND 256 AND "
        "length(database) BETWEEN 1 AND 256 AND length(company_id) BETWEEN 1 AND 256 AND "
        "length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND "
        "length(audience) BETWEEN 1 AND 256 AND length(browser_jwk_thumbprint) BETWEEN 8 AND 512 AND "
        "length(session_nonce) BETWEEN 1 AND 512 AND length(phrase) BETWEEN 5 AND 256",
        name="ck_client_trust_pairing_requests_identity",
    ),
    CheckConstraint(
        "length(browser_origin) > 8 AND browser_origin LIKE 'https://%' AND length(browser_origin) - length(replace(browser_origin, '/', '')) = 2 AND browser_origin NOT LIKE '%?%' AND browser_origin NOT LIKE '%#%' AND browser_origin NOT LIKE '%@%' AND browser_origin NOT LIKE '%*%' AND length(agent_endpoint) > 8 AND agent_endpoint LIKE 'https://%' AND length(agent_endpoint) - length(replace(agent_endpoint, '/', '')) = 2 AND agent_endpoint NOT LIKE '%?%' AND agent_endpoint NOT LIKE '%#%' AND agent_endpoint NOT LIKE '%@%' AND agent_endpoint NOT LIKE '%*%'",
        name="ck_client_trust_pairing_requests_origin",
    ),
    CheckConstraint(
        "(pos_configuration_id IS NULL) OR length(pos_configuration_id) BETWEEN 1 AND 256",
        name="ck_client_trust_pairing_requests_scope",
    ),
    CheckConstraint(
        "json_valid(requested_permissions) AND json_type(requested_permissions) = 'array' AND requested_permissions = json(requested_permissions)",
        name="ck_client_trust_pairing_requests_permissions",
    ),
    CheckConstraint(
        "expires_at > created_at",
        name="ck_client_trust_pairing_requests_validity",
    ),
    CheckConstraint(
        "state IN ('pending', 'approved', 'denied', 'canceled', 'expired', 'completed')",
        name="ck_client_trust_pairing_requests_state",
    ),
)
Index(
    "idx_client_trust_pairing_requests_state_expires_at",
    pairing_requests_table.c.state,
    pairing_requests_table.c.expires_at,
)

client_pairings_table = Table(
    "client_trust_pairings",
    metadata,
    Column("pairing_id", String, primary_key=True),
    Column(
        "pairing_request_id",
        String,
        ForeignKey("client_trust_pairing_requests.request_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("jwk_thumbprint", String, nullable=False),
    Column("agent_id", String, nullable=False),
    Column("browser_origin", String, nullable=False),
    Column("agent_endpoint", String, nullable=False),
    Column("database", String, nullable=False),
    Column("company_id", String, nullable=False),
    Column("organization_id", String, nullable=False),
    Column("site_id", String, nullable=False),
    Column("pos_configuration_id", String),
    Column("audience", String, nullable=False),
    Column("actor_id", String, nullable=False),
    Column("role", String, nullable=False),
    Column("permissions", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("expires_at", String),
    Column("lifecycle", String, nullable=False),
    Column("last_used_at", String),
    CheckConstraint(
        "length(pairing_id) BETWEEN 1 AND 256 AND length(pairing_request_id) BETWEEN 1 AND 256 AND length(jwk_thumbprint) BETWEEN 8 AND 512 AND "
        "length(agent_id) BETWEEN 1 AND 256 AND length(database) BETWEEN 1 AND 256 AND "
        "length(company_id) BETWEEN 1 AND 256 AND length(organization_id) BETWEEN 1 AND 256 AND "
        "length(site_id) BETWEEN 1 AND 256 AND length(audience) BETWEEN 1 AND 256 AND "
        "length(actor_id) BETWEEN 1 AND 256 AND length(role) BETWEEN 1 AND 256",
        name="ck_client_trust_pairings_identity",
    ),
    CheckConstraint(
        "length(browser_origin) > 8 AND browser_origin LIKE 'https://%' AND length(browser_origin) - length(replace(browser_origin, '/', '')) = 2 AND browser_origin NOT LIKE '%?%' AND browser_origin NOT LIKE '%#%' AND browser_origin NOT LIKE '%@%' AND browser_origin NOT LIKE '%*%' AND length(agent_endpoint) > 8 AND agent_endpoint LIKE 'https://%' AND length(agent_endpoint) - length(replace(agent_endpoint, '/', '')) = 2 AND agent_endpoint NOT LIKE '%?%' AND agent_endpoint NOT LIKE '%#%' AND agent_endpoint NOT LIKE '%@%' AND agent_endpoint NOT LIKE '%*%'",
        name="ck_client_trust_pairings_origin",
    ),
    CheckConstraint(
        "pos_configuration_id IS NULL OR length(pos_configuration_id) BETWEEN 1 AND 256",
        name="ck_client_trust_pairings_scope",
    ),
    CheckConstraint(
        "json_valid(permissions) AND json_type(permissions) = 'array' AND permissions = json(permissions)",
        name="ck_client_trust_pairings_permissions",
    ),
    CheckConstraint(
        "expires_at IS NULL OR expires_at > created_at",
        name="ck_client_trust_pairings_validity",
    ),
    CheckConstraint(
        "lifecycle IN ('active', 'revoked', 'expired')",
        name="ck_client_trust_pairings_lifecycle",
    ),
)
Index(
    "idx_client_trust_pairings_scope",
    client_pairings_table.c.agent_id,
    client_pairings_table.c.database,
    client_pairings_table.c.organization_id,
    client_pairings_table.c.site_id,
    client_pairings_table.c.pos_configuration_id,
)

client_grants_table = Table(
    "client_trust_grants",
    metadata,
    Column("grant_id", String, primary_key=True),
    Column(
        "pairing_id",
        String,
        ForeignKey("client_trust_pairings.pairing_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("jwk_thumbprint", String, nullable=False),
    Column("agent_id", String, nullable=False),
    Column("browser_origin", String, nullable=False),
    Column("agent_endpoint", String, nullable=False),
    Column("database", String, nullable=False),
    Column("company_id", String, nullable=False),
    Column("organization_id", String, nullable=False),
    Column("site_id", String, nullable=False),
    Column("pos_configuration_id", String),
    Column("audience", String, nullable=False),
    Column("actor_id", String, nullable=False),
    Column("role", String, nullable=False),
    Column("permissions", Text, nullable=False),
    Column("authorization_digest", String, nullable=False),
    Column("generation", Integer, nullable=False),
    Column("issued_at", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("offline_renewal_until", String),
    Column("lifecycle", String, nullable=False),
    Column("last_used_at", String),
    CheckConstraint(
        "length(grant_id) BETWEEN 1 AND 256 AND length(pairing_id) BETWEEN 1 AND 256 AND "
        "length(jwk_thumbprint) BETWEEN 8 AND 512 AND length(agent_id) BETWEEN 1 AND 256 AND "
        "length(database) BETWEEN 1 AND 256 AND length(company_id) BETWEEN 1 AND 256 AND "
        "length(organization_id) BETWEEN 1 AND 256 AND length(site_id) BETWEEN 1 AND 256 AND "
        "length(audience) BETWEEN 1 AND 256 AND length(actor_id) BETWEEN 1 AND 256 AND "
        "length(role) BETWEEN 1 AND 256 AND length(authorization_digest) BETWEEN 1 AND 256 AND "
        "generation >= 0",
        name="ck_client_trust_grants_identity",
    ),
    CheckConstraint(
        "length(browser_origin) > 8 AND browser_origin LIKE 'https://%' AND length(browser_origin) - length(replace(browser_origin, '/', '')) = 2 AND browser_origin NOT LIKE '%?%' AND browser_origin NOT LIKE '%#%' AND browser_origin NOT LIKE '%@%' AND browser_origin NOT LIKE '%*%' AND length(agent_endpoint) > 8 AND agent_endpoint LIKE 'https://%' AND length(agent_endpoint) - length(replace(agent_endpoint, '/', '')) = 2 AND agent_endpoint NOT LIKE '%?%' AND agent_endpoint NOT LIKE '%#%' AND agent_endpoint NOT LIKE '%@%' AND agent_endpoint NOT LIKE '%*%'",
        name="ck_client_trust_grants_origin",
    ),
    CheckConstraint(
        "pos_configuration_id IS NULL OR length(pos_configuration_id) BETWEEN 1 AND 256",
        name="ck_client_trust_grants_scope",
    ),
    CheckConstraint(
        "json_valid(permissions) AND json_type(permissions) = 'array' AND permissions = json(permissions)",
        name="ck_client_trust_grants_permissions",
    ),
    CheckConstraint(
        "expires_at > issued_at AND (offline_renewal_until IS NULL OR offline_renewal_until > expires_at)",
        name="ck_client_trust_grants_validity",
    ),
    CheckConstraint(
        "lifecycle IN ('active', 'revoked', 'expired')",
        name="ck_client_trust_grants_lifecycle",
    ),
)
Index(
    "idx_client_trust_grants_pairing_id",
    client_grants_table.c.pairing_id,
    client_grants_table.c.expires_at,
)

client_trust_replays_table = Table(
    "client_trust_replays",
    metadata,
    Column("kind", String, nullable=False),
    Column("jti", String, nullable=False),
    Column("consumed_at", String, nullable=False),
    Column("expires_at", String),
    CheckConstraint(
        "kind IN ('pairing_assertion', 'dpop') AND length(jti) BETWEEN 1 AND 512",
        name="ck_client_trust_replays_identity",
    ),
    CheckConstraint(
        "(kind = 'pairing_assertion' AND expires_at IS NULL) OR (kind = 'dpop' AND expires_at IS NOT NULL AND expires_at > consumed_at)",
        name="ck_client_trust_replays_kind_shape",
    ),
    PrimaryKeyConstraint("kind", "jti"),
)
Index(
    "idx_client_trust_replays_expiry",
    client_trust_replays_table.c.expires_at,
)

client_trust_nonces_table = Table(
    "client_trust_nonces",
    metadata,
    Column("nonce", String, primary_key=True),
    Column("issued_at", String, nullable=False),
    Column("expires_at", String, nullable=False),
    Column("consumed_at", String),
    CheckConstraint(
        "length(nonce) BETWEEN 8 AND 512",
        name="ck_client_trust_nonces_identity",
    ),
    CheckConstraint(
        "expires_at > issued_at AND (consumed_at IS NULL OR consumed_at >= issued_at)",
        name="ck_client_trust_nonces_validity",
    ),
)
Index("idx_client_trust_nonces_expiry", client_trust_nonces_table.c.expires_at)

MANAGED_TABLE_NAMES = frozenset(
    {
        devices_table.name,
        device_events_table.name,
        jobs_table.name,
        job_attempts_table.name,
        job_events_table.name,
        gateway_inbound_commands_table.name,
        gateway_outbox_table.name,
        public_print_jobs_table.name,
        device_work_admissions_table.name,
        spool_root_keys_table.name,
        spool_security_state_table.name,
        spool_job_keys_table.name,
        spool_admission_keys_table.name,
        spool_root_key_provisioning_table.name,
        spool_reservations_table.name,
        spool_nonce_reservations_table.name,
        spool_artifacts_table.name,
        public_print_job_events_table.name,
        physical_execution_attempts_table.name,
        drawer_intents_table.name,
        device_stream_state_table.name,
        device_stream_generations_table.name,
        device_authority_signer_keys_table.name,
        device_authority_revisions_table.name,
        device_authority_state_table.name,
        device_driver_profiles_table.name,
        hardware_certification_matrix_rows_table.name,
        device_binding_revisions_table.name,
        device_test_evidence_table.name,
        device_authority_revocations_table.name,
        device_observations_table.name,
        device_binding_authority_state_table.name,
        device_work_authority_proofs_table.name,
        device_work_authority_proof_subjects_table.name,
        pairing_requests_table.name,
        client_pairings_table.name,
        client_grants_table.name,
        client_trust_replays_table.name,
        client_trust_nonces_table.name,
    }
)


def create_database_engine(database_path: Path) -> Engine:
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(database_path)),
        connect_args={"timeout": 30, "check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        del connection_record
        configure_sqlite_dbapi_connection(dbapi_connection)

    return engine


def configure_sqlite_dbapi_connection(dbapi_connection) -> None:  # type: ignore[no-untyped-def]
    """Apply the durability contract to each runtime or migration connection."""

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = FULL")
    finally:
        cursor.close()
