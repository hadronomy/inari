PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    driver_key TEXT NOT NULL,
    name TEXT NOT NULL,
    connection_state TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_default INTEGER NOT NULL,
    preferred_transport TEXT,
    capabilities_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE device_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(id)
);

CREATE INDEX idx_device_events_device_id
ON device_events(device_id, sequence DESC);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    operation TEXT NOT NULL,
    device_id TEXT NOT NULL,
    device_kind TEXT NOT NULL,
    device_name TEXT NOT NULL,
    state TEXT NOT NULL,
    request_json TEXT NOT NULL,
    request_metadata_json TEXT NOT NULL,
    content_kind TEXT,
    command_kind TEXT,
    attempt_count INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    next_run_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    lease_expires_at TEXT,
    result_json TEXT,
    last_error_code TEXT,
    last_error_detail TEXT
);

CREATE INDEX idx_jobs_state_next_run_at
ON jobs(state, next_run_at, created_at);

CREATE INDEX idx_jobs_device_id
ON jobs(device_id, created_at);

CREATE TABLE job_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_code TEXT,
    error_detail TEXT,
    result_json TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX idx_job_attempts_job_id
ON job_attempts(job_id, attempt_number DESC);

CREATE TABLE job_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX idx_job_events_job_id
ON job_events(job_id, sequence DESC);

CREATE TABLE gateway_inbound_commands (
    command_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    response_json TEXT,
    error_code TEXT,
    error_detail TEXT,
    job_id TEXT,
    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_gateway_inbound_job_id
ON gateway_inbound_commands(job_id, updated_at DESC);

CREATE TABLE gateway_outbox (
    message_id TEXT PRIMARY KEY,
    message_type TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    correlation_id TEXT,
    dedupe_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    acknowledged_at TEXT,
    last_error TEXT
);

CREATE UNIQUE INDEX idx_gateway_outbox_dedupe_key
ON gateway_outbox(dedupe_key)
WHERE dedupe_key IS NOT NULL;

CREATE INDEX idx_gateway_outbox_state_created_at
ON gateway_outbox(state, created_at);
