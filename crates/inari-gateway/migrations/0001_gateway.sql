CREATE TABLE organizations (
    organization_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE sites (
    site_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, name)
);

CREATE TABLE agents (
    agent_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE RESTRICT,
    key_id TEXT NOT NULL UNIQUE,
    jwk_thumbprint TEXT NOT NULL,
    public_jwk JSONB NOT NULL,
    certificate_pem TEXT,
    namespace TEXT NOT NULL UNIQUE,
    protocol_version TEXT NOT NULL,
    controller_actions JSONB NOT NULL,
    enrolled_at TIMESTAMPTZ NOT NULL,
    last_enrolled_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE invitations (
    invitation_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE RESTRICT,
    label TEXT,
    secret_digest BYTEA NOT NULL CHECK (octet_length(secret_digest) = 32),
    state TEXT NOT NULL CHECK (
        state IN ('created', 'claimed', 'enrolled', 'online', 'expired', 'failed', 'revoked')
    ),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    claimed_at TIMESTAMPTZ,
    enrolled_at TIMESTAMPTZ,
    online_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    last_error TEXT,
    bound_agent_id TEXT,
    bound_key_id TEXT,
    latest_snapshot JSONB
);

CREATE INDEX invitations_state_expires_at ON invitations (state, expires_at);

CREATE TABLE invitation_attempts (
    invitation_id TEXT NOT NULL REFERENCES invitations(invitation_id) ON DELETE CASCADE,
    attempted_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX invitation_attempts_window ON invitation_attempts (invitation_id, attempted_at);

CREATE TABLE consumed_enrollment_tokens (
    token_digest BYTEA PRIMARY KEY CHECK (octet_length(token_digest) = 32),
    agent_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    consumed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE commands (
    command_id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    message_id TEXT NOT NULL UNIQUE,
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    state TEXT NOT NULL,
    command JSONB NOT NULL,
    request_fingerprint BYTEA NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    issued_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (agent_id, command_id),
    UNIQUE (agent_id, sequence)
);

CREATE TABLE publications (
    message_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    key_expr TEXT NOT NULL,
    message_type TEXT,
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX publications_agent_received ON publications (agent_id, received_at DESC);

CREATE TABLE devices (
    device_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('printer', 'scale', 'scanner')),
    display_name TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('discovered', 'pending_approval', 'online', 'offline', 'degraded', 'blocked')
    ),
    transport TEXT NOT NULL,
    hardware_fingerprint TEXT NOT NULL,
    capabilities JSONB NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    UNIQUE (agent_id, hardware_fingerprint)
);

CREATE INDEX devices_site_state ON devices (site_id, state);

CREATE TABLE audit_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_id TEXT,
    outcome TEXT NOT NULL,
    request_id TEXT,
    detail JSONB NOT NULL DEFAULT '{}'::JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX audit_events_organization_time
    ON audit_events (organization_id, occurred_at DESC);
