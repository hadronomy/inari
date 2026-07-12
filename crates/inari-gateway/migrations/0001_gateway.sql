CREATE TABLE agents (
    agent_id TEXT PRIMARY KEY NOT NULL,
    key_id TEXT NOT NULL,
    jwk_thumbprint TEXT NOT NULL,
    public_jwk_json TEXT NOT NULL CHECK (json_valid(public_jwk_json)),
    certificate_pem TEXT,
    namespace TEXT NOT NULL UNIQUE,
    protocol_version TEXT NOT NULL,
    controller_actions_json TEXT NOT NULL CHECK (json_valid(controller_actions_json)),
    enrolled_at TEXT NOT NULL,
    last_enrolled_at TEXT NOT NULL,
    UNIQUE (key_id)
);

CREATE TABLE invitations (
    invitation_id TEXT PRIMARY KEY NOT NULL,
    label TEXT,
    secret_digest BLOB NOT NULL CHECK (length(secret_digest) = 32),
    state TEXT NOT NULL CHECK (state IN ('created', 'claimed', 'enrolled', 'online', 'expired', 'failed', 'revoked')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    claimed_at TEXT,
    enrolled_at TEXT,
    online_at TEXT,
    revoked_at TEXT,
    failed_at TEXT,
    last_error TEXT,
    bound_agent_id TEXT,
    bound_key_id TEXT,
    latest_snapshot_json TEXT CHECK (latest_snapshot_json IS NULL OR json_valid(latest_snapshot_json))
);

CREATE INDEX invitations_state_expires_at ON invitations (state, expires_at);

CREATE TABLE invitation_attempts (
    invitation_id TEXT NOT NULL REFERENCES invitations(invitation_id) ON DELETE CASCADE,
    attempted_at TEXT NOT NULL
);

CREATE INDEX invitation_attempts_window ON invitation_attempts (invitation_id, attempted_at);

CREATE TABLE consumed_enrollment_tokens (
    token_digest BLOB PRIMARY KEY NOT NULL CHECK (length(token_digest) = 32),
    agent_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    consumed_at TEXT NOT NULL
);

CREATE TABLE commands (
    command_id TEXT NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    message_id TEXT NOT NULL UNIQUE,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    state TEXT NOT NULL,
    command_json TEXT NOT NULL CHECK (json_valid(command_json)),
    issued_at TEXT NOT NULL,
    published_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, command_id),
    UNIQUE (agent_id, sequence)
);

CREATE TABLE publications (
    message_id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    key_expr TEXT NOT NULL,
    message_type TEXT,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    received_at TEXT NOT NULL
);

CREATE INDEX publications_agent_received ON publications (agent_id, received_at DESC);
