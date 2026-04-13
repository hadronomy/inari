# Gateway Protocol

This document defines the current managed-mode contract between:

- the local `iot-agent` process acting as the edge gateway
- an external IoT controller service

This is the Boundary 2 protocol: controller `<->` agent/gateway.

## Status

- Protocol version: `2026-04-13`
- Agent API version at time of writing: `1.12.0a1`
- Transport model: outbound `HTTPS` for enrollment and status sync, outbound `WSS` for the control stream

The agent is the client in this relationship. The controller is the server.

## Goals

The protocol covers:

- enrollment
- enrollment-code bootstrap
- controller-issued or external access-token bootstrap
- controller-issued step-ca OTT bootstrap for client certificates
- optional controller-installed client-certificate installation
- periodic status synchronization
- persistent controller-to-agent commands
- persistent agent-to-controller event delivery
- protocol negotiation
- replay-safe acknowledgements

The protocol does not attempt to model raw device links such as Windows spooler, USB, or serial transport. Those stay inside the agent.

## Trust Model

Managed mode uses an outbound trust model:

1. The agent connects outward to the controller.
2. The agent verifies the controller TLS certificate.
3. The controller authenticates the agent with one of:
   - a bootstrap bearer token plus controller-issued access token
   - an external bearer token provider such as ZITADEL
4. The controller edge may additionally authenticate the agent with a client certificate, for example through Caddy mTLS.
5. Client certificates may be:
   - installed by the controller during enrollment
   - provisioned directly from a private CA such as step-ca

### Authentication Materials

The agent may use four credential types across the lifecycle:

- `enrollment code`
  Optional human-facing bootstrap input that the controller exchanges for agent credentials and, when needed, a one-time step-ca issuance token.
- `bootstrap token`
  Used only for initial enrollment.
- `access token`
  Used for status sync and control-stream authentication. This may be controller-issued or obtained from an external identity provider.
- `refresh token`
  Used to renew a controller-issued access token before expiry, if the controller provides one.
- `client certificate`
  Optional. If configured or provided, the agent installs it locally and presents it on outbound TLS connections.

### Agent Identity

The agent has a persistent identity consisting of:

- `agent_id`
- `key_id`
- `public_jwk`
- `csr_pem`
- optional `certificate_pem`

The controller should treat `agent_id` as the stable logical identity of the gateway installation.

## Default URLs

If the controller only provides `upstream_base_url`, the agent derives these default endpoints:

- enrollment:
  `/api/iot-agent/enroll`
- status sync:
  `/api/iot-agent/agents/{agent_id}/status`
- control stream:
  `/api/iot-agent/agents/{agent_id}/events`

The controller may override the status, events, and refresh URLs explicitly in the enrollment response.

## Protocol Version Negotiation

The current protocol version is:

```text
2026-04-13
```

The agent sends its version in:

- enrollment payload:
  `protocol.version`
- status sync header:
  `X-IoT-Agent-Protocol-Version`
- WebSocket hello:
  `agent.hello.protocol.version`

The controller sends its version in:

- enrollment response:
  `protocol_version`
- WebSocket hello:
  `controller.hello.protocol_version`

If the controller announces a version not listed in the agent snapshot’s `protocol.supported_versions`, the agent treats that as a protocol mismatch and will not continue normal control-stream operation.

## Enrollment

### Request

The agent enrolls with:

```http
POST /api/iot-agent/enroll
Authorization: Bearer <bootstrap-token>
Content-Type: application/json
```

The `Authorization` header is optional when the installation is using an enrollment code instead of a controller-issued bootstrap bearer token.

Request body:

```json
{
  "protocol": {
    "version": "2026-04-13",
    "supported_versions": ["2026-04-13"]
  },
  "agent_id": "agt_123...",
  "key_id": "kid_123...",
  "public_jwk": {
    "kty": "OKP",
    "crv": "Ed25519",
    "alg": "EdDSA",
    "use": "sig",
    "kid": "kid_123...",
    "x": "..."
  },
  "certificate_pem": null,
  "csr_pem": "-----BEGIN CERTIFICATE REQUEST-----\n...\n-----END CERTIFICATE REQUEST-----\n",
  "enrollment_code": "SITE-A-4F7K-92LM",
  "snapshot": {
    "generated_at": "2026-04-12T10:00:00Z",
    "protocol": {
      "version": "2026-04-13",
      "supported_versions": ["2026-04-13"]
    },
    "service": {
      "name": "IoT Agent",
      "version": "1.12.0a1",
      "agent_id": "agt_123...",
      "key_id": "kid_123..."
    },
    "security": {
      "mode": "managed",
      "exposure": "loopback",
      "tls_required": false,
      "mutual_tls_enabled": false,
      "certificate_expires_at": null
    },
    "runtime": {
      "queue": {
        "total": 0,
        "queued": 0,
        "dispatched": 0,
        "running": 0,
        "retry_scheduled": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0
      },
      "devices": {
        "count": 1,
        "online_count": 1,
        "offline_count": 0,
        "kind_counts": {
          "printer": 1
        },
        "default_device_id": "dev_123...",
        "default_device_name": "Kitchen Printer"
      }
    },
    "capabilities": {
      "supported_content_kinds": ["structured_receipt", "receipt_image", "text", "html", "pdf", "raw"],
      "supported_device_commands": ["open_cash_drawer", "print_test_page", "feed_lines", "feed_dots", "cut_paper"],
      "granted_scopes": ["system:read", "devices:read", "events:read", "jobs:read", "jobs:submit", "commands:execute"],
      "features": [
        "status_sync",
        "control_stream",
        "command_ack",
        "event_replay",
        "runtime_event_forwarding",
        "token_refresh",
        "certificate_rotation",
        "protocol_negotiation"
      ],
      "transport": "https+wss",
      "client_certificate_present": false
    },
    "observability": {
      "gateway": {},
      "runtime": {
        "queue_states": {}
      }
    }
  }
}
```

### Response

Successful enrollment returns:

```json
{
  "protocol_version": "2026-04-13",
  "controller_name": "Acme IoT Controller",
  "controller_instance_id": "controller-01",
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "enrolled_at": "2026-04-12T10:00:02Z",
  "expires_at": "2026-04-12T11:00:02Z",
  "refresh_url": "https://controller.example/api/iot-agent/agents/agt_123/refresh",
  "status_url": "https://controller.example/api/iot-agent/agents/agt_123/status",
  "events_url": "wss://controller.example/api/iot-agent/agents/agt_123/events",
  "granted_scopes": [
    "system:read",
    "devices:read",
    "events:read",
    "jobs:read",
    "jobs:submit",
    "commands:execute"
  ],
  "certificate_bootstrap": {
    "mode": "step_ca_ott",
    "ca_url": "https://ca.example.com",
    "root_fingerprint": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "ott": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9...",
    "sign_url": "https://ca.example.com/1.0/sign",
    "renew_url": "https://ca.example.com/1.0/renew",
    "expires_at": "2026-04-12T10:05:02Z",
    "subject": "agt_123",
    "authorized_sans": ["urn:iot-agent:agt_123"],
    "requires_mutual_tls_after_issuance": true
  }
}
```

### Enrollment Rules

- `enrollment_code` is optional and intended for user-friendly installations where the controller turns a short invite code into real gateway credentials.
- `access_token` is required only when the controller is responsible for ongoing bearer authentication.
- `access_token` may be omitted when the agent uses an external auth provider such as ZITADEL.
- `refresh_token` is optional.
- `status_url` and `events_url` are optional if the agent can derive them.
- `granted_scopes` determine what remote actions the controller is allowed to request.
- `certificate_pem` and `ca_certificate_pem` are optional.
- `certificate_bootstrap` is optional, but it is the recommended production bootstrap for `step_ca` client-certificate mode.

If the controller provides a certificate, the agent installs it and may use it on future outbound TLS connections.

If the controller provides `certificate_bootstrap`, the agent:

1. validates the step-ca root certificate fingerprint
2. calls `/1.0/sign` with its CSR and the controller-issued one-time token
3. installs the returned client certificate
4. later renews with `/1.0/renew`

The one-time token should be short-lived, single-use, and scoped to the specific agent identity and allowed SAN set.

## Controller Auth Modes

The current agent supports two managed auth modes:

- `controller`
  The controller returns `access_token` and optionally `refresh_token` during enrollment. The agent uses those credentials for status sync and the control stream.
- `zitadel_service_account`
  The agent obtains bearer tokens directly from ZITADEL using a service account and private-key JWT. In this mode the controller may omit `access_token` from the enrollment response.

Controllers should document which auth mode an agent installation is expected to use.

## Token Refresh

Controller-managed token refresh only applies when the controller issued the access token.

If the agent has:

- an enrollment record
- a `refresh_url`
- and either a `refresh_token` or at least the current `access_token`

then it may refresh credentials before token expiry.

Refresh request:

```http
POST <refresh_url>
Authorization: Bearer <refresh-token-or-access-token>
Content-Type: application/json
```

Request body:

```json
{
  "protocol_version": "2026-04-13",
  "agent_id": "agt_123..."
}
```

Refresh response uses the same body shape as the enrollment response.

If refresh returns `401` or `403`, the agent clears its stored enrollment and returns to an unenrolled state.

When the agent uses an external auth provider such as ZITADEL, the bearer token lifecycle is handled by that provider instead of the controller refresh endpoint.

## Status Sync

### Request

The agent periodically sends a snapshot to the controller:

```http
POST <status_url>
Authorization: Bearer <access-token>
X-IoT-Agent-Protocol-Version: 2026-04-13
Content-Type: application/json
```

The request body is the same `GatewaySnapshotPayload` shape used during enrollment.

### Response

The current implementation treats the response body as optional.

If present, the response may include:

- `protocol_version`
- `controller_name`
- `controller_instance_id`

Those fields are used only to enrich controller status on the agent side.

### Status Sync Failure Handling

- `401` or `403`:
  The agent clears enrollment and enters `auth_failed`.
- transport or server error:
  The agent enters `recovering` and retries later.

## Control Stream

The control stream is an outbound WebSocket connection from the agent to the controller:

```http
GET <events_url>
Authorization: Bearer <access-token>
```

The stream is full-duplex.

### Connection Sequence

1. The agent connects to the controller WebSocket.
2. The agent immediately sends `agent.hello`.
3. The controller should send `controller.hello`.
4. The agent validates the announced controller protocol version.
5. Normal command and event traffic begins.

### Agent Hello

Sent immediately after WebSocket connection:

```json
{
  "type": "agent.hello",
  "message_id": "ghello_...",
  "protocol": {
    "version": "2026-04-13",
    "supported_versions": ["2026-04-13"]
  },
  "snapshot": { "...GatewaySnapshotPayload..." }
}
```

### Controller Hello

Expected from the controller:

```json
{
  "type": "controller.hello",
  "message_id": "hello_1",
  "protocol_version": "2026-04-13",
  "controller_name": "Acme IoT Controller",
  "controller_instance_id": "controller-01"
}
```

If the controller protocol version is unsupported, the agent treats that as a protocol mismatch and does not continue normal operation.

### Ping / Pong

Controller ping:

```json
{
  "type": "controller.ping",
  "message_id": "ping_1",
  "detail": "health-check"
}
```

Agent pong:

```json
{
  "type": "agent.pong",
  "message_id": "gpong_...",
  "acknowledged_message_id": "ping_1",
  "detail": "health-check"
}
```

### Passive Snapshot Heartbeat

If no controller message is received before the agent’s event timeout, the agent sends:

```json
{
  "type": "agent.status.snapshot",
  "message_id": "gsnap_...",
  "snapshot": { "...GatewaySnapshotPayload..." }
}
```

Controllers should treat this as a keepalive plus state refresh.

## Controller-to-Agent Commands

The controller may send three command types.

All command messages must contain:

- `message_id`
- `command_id`

`command_id` is the idempotency key for inbound command execution.

### Submit Print Job

```json
{
  "type": "controller.command.submit_print_job",
  "message_id": "msg_100",
  "command_id": "cmd_100",
  "issued_at": "2026-04-12T10:05:00Z",
  "payload": {
    "content": {
      "kind": "text",
      "text": "Hello printer",
      "document_name": "Greeting"
    },
    "target": {
      "printer_name": "Kitchen Printer"
    },
    "options": {
      "transport": "text",
      "open_cash_drawer": false
    },
    "metadata": {
      "source": "controller"
    }
  }
}
```

`payload` must exactly match the REST `POST /print-jobs` request body.

Required granted scope:

- `jobs:submit`

### Execute Device Command

```json
{
  "type": "controller.command.execute_device_command",
  "message_id": "msg_101",
  "command_id": "cmd_101",
  "issued_at": "2026-04-12T10:06:00Z",
  "payload": {
    "target": {
      "printer_name": "Kitchen Printer"
    },
    "command": {
      "kind": "cut_paper",
      "mode": "partial"
    },
    "metadata": {
      "source": "controller"
    }
  }
}
```

`payload` must exactly match the REST `POST /device-commands` request body.

Required granted scope:

- `commands:execute`

### Cancel Job

```json
{
  "type": "controller.command.cancel_job",
  "message_id": "msg_102",
  "command_id": "cmd_102",
  "issued_at": "2026-04-12T10:07:00Z",
  "job_id": "job_123"
}
```

Required granted scope:

- `jobs:submit`

## Agent Responses to Commands

The agent does not execute controller commands inline on the WebSocket thread. It validates, persists, and then emits a durable response message through its outbound outbox.

### Accepted

If a command is accepted and translated into runtime work:

```json
{
  "type": "agent.command.accepted",
  "message_id": "gack_...",
  "command_id": "cmd_100",
  "accepted_at": "2026-04-12T10:05:01Z",
  "job": {
    "...": "the same shape as JobResponse from the agent HTTP API"
  },
  "detail": "Accepted upstream command and queued job job_123."
}
```

### Rejected

If a command is rejected:

```json
{
  "type": "agent.command.rejected",
  "message_id": "gerr_...",
  "command_id": "cmd_100",
  "rejected_at": "2026-04-12T10:05:01Z",
  "code": "UPSTREAM_SCOPE_DENIED",
  "detail": "The upstream controller is not authorized for scope 'jobs:submit'."
}
```

## Runtime Event Forwarding

The agent forwards runtime events to the controller as durable outbox messages:

```json
{
  "type": "agent.runtime.event",
  "message_id": "gevt_...",
  "occurred_at": "2026-04-12T10:05:03Z",
  "event": {
    "sequence": 99,
    "resource_kind": "job",
    "resource_id": "job_123",
    "event_type": "job.succeeded",
    "occurred_at": "2026-04-12T10:05:03Z",
    "payload": {
      "job_id": "job_123"
    }
  },
  "command_id": "cmd_100",
  "job_id": "job_123"
}
```

If the runtime event is related to a job created by a controller command, the agent includes the original `command_id`.

## Acknowledgement Semantics

The controller acknowledges agent-originated messages with:

```json
{
  "type": "controller.ack",
  "message_id": "ack_1",
  "acknowledged_message_id": "gevt_..."
}
```

The agent marks the referenced outbox record as acknowledged.

### Important Delivery Rule

The agent may resend a previously sent message after reconnect if it was not acknowledged.

Controllers must therefore treat `message_id` as the deduplication key for agent-originated messages.

## Replay and Deduplication Rules

### Inbound Commands

Inbound commands are deduplicated by `command_id`.

If the controller resends a command with the same `command_id`:

- the agent does not execute it again
- the agent replays the previously stored acceptance or rejection message

Controllers must never reuse `command_id` for semantically different commands.

### Outbound Messages

The agent persists outbound messages in an outbox.

Controllers should deduplicate by `message_id` for:

- `agent.command.accepted`
- `agent.command.rejected`
- `agent.runtime.event`
- `agent.status.snapshot`

## Scope Enforcement

The controller may only invoke operations that the agent granted during enrollment.

Current required scopes:

- `jobs:submit`
  Required for `controller.command.submit_print_job`
- `commands:execute`
  Required for `controller.command.execute_device_command`
- `jobs:submit`
  Required for `controller.command.cancel_job`

If a scope is missing, the agent rejects the command with `UPSTREAM_SCOPE_DENIED`.

## Certificate Lifecycle

The controller may still return:

- `certificate_pem`
- `ca_certificate_pem`

If present, the agent installs them locally and may present the client certificate on later outbound TLS connections.

For production `step_ca` mode, the preferred flow is controller-issued bootstrap material instead of a shared step-ca provisioner key on the agent. In that mode:

- the installer supplies an enrollment code or other bootstrap input to the agent
- the controller validates the installation policy
- the controller mints a short-lived step-ca OTT for that specific agent
- the controller returns `certificate_bootstrap`
- the agent bootstraps the step-ca root with the supplied fingerprint
- the agent calls `/1.0/sign` with its CSR and the OTT
- the agent renews later through `/1.0/renew`

This keeps CA-authorizing secrets off the edge host while still supporting seamless installation.

## Caddy Edge Compatibility

When the controller is fronted by Caddy:

- enrollment and status sync should be exposed over `HTTPS`
- the control stream should be exposed over `WSS`
- optional client authentication may be enforced at the Caddy edge

If Caddy is configured with `require_and_verify` client auth, the agent must already have a usable client certificate before it attempts enrollment or control-stream connection. The recommended way to satisfy that requirement is step-ca certificate provisioning.

In practice, strict Caddy mTLS deployments should expose a bootstrap enrollment URL that is reachable before the first client certificate is issued. After initial certificate issuance, the normal controller status and events URLs can require client certificates.

## Controller Implementation Checklist

A controller implementation is production-compatible with the current agent if it:

1. Exposes the enrollment endpoint.
2. Accepts and validates the bootstrap token.
3. Returns an enrollment response with at least `access_token`.
4. Accepts status snapshots over HTTPS.
5. Exposes a WSS control-stream endpoint.
6. Sends `controller.hello` on stream startup.
7. Sends `controller.ack` for each durable agent-originated message it has processed.
8. Uses unique `command_id` values for controller commands.
9. Treats agent `message_id` values as idempotency keys.
10. If using `step_ca`, returns controller-issued `certificate_bootstrap` data rather than requiring the agent to hold a CA provisioner key.
11. Optionally exposes `refresh_url` and signed client certificates.

## Non-Goals of the Current Protocol

The following are not yet standardized beyond the current implementation:

- server-side error envelope shape for enrollment and status sync failures
- batched controller commands
- controller-driven pagination or replay cursors
- explicit server-to-agent backpressure messages
- multi-controller coordination for a single agent identity

Those can be added in future protocol versions, but they are not part of the current `2026-04-13` contract.
