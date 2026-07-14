# Gateway protocol

This document is the wire contract between an Inari edge agent and a managed
controller. It describes the protocol implemented by the Rust controller and
Python agent in this repository.

The agent always connects outward:

- HTTPS handles invitation preview and enrollment;
- Zenoh carries status, commands, results, events, replay, and liveliness after
  enrollment.

The local HTTP API used by Odoo and Device Center is a separate boundary and is
not part of this protocol.

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are
used as defined by [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174).

## Version and compatibility

The current draft version is `2026-07-12`.

An enrollment request contains the agent’s preferred version and the versions
it supports. The controller MUST return one of those versions as
`selected_protocol_version`. If there is no overlap, enrollment fails and the
agent MUST NOT open the managed data plane.

Applied protocol changes update the Rust types, Python models, shared fixtures,
tests, and this document together.

## Identity and trust

Each agent keeps a persistent logical and cryptographic identity:

- `agent_id` — stable managed agent identifier;
- `key_id` — identifier for the signing key;
- `public_jwk` — Ed25519 public key in OKP JWK form;
- `csr_pem` — PKCS#10 request signed by that key;
- `certificate_pem` — current managed certificate, when one exists.

During enrollment the controller MUST:

1. accept only the supported Ed25519 JWK shape;
2. calculate and validate the RFC 7638 thumbprint;
3. verify the CSR signature;
4. compare the CSR subject public key with the JWK;
5. compare any supplied certificate with the same key;
6. bind the enrollment credential, protocol version, invitation state, and
   agent identity in one transaction;
7. consume a one-use credential only when enrollment succeeds.

An issued certificate MUST bind to the enrolled `agent_id`. Its SANs and key
usage MUST stay within the authorization approved by the controller.

## Invitation bootstrap

An authenticated operator creates invitations through the controller UI. The
secret-bearing setup link has this form:

```text
https://controller.example.com/setup/{invitation_id}#code=INR-...
```

URL fragments are not sent to the web server. The server renders a neutral
setup page; hydrated Rust reads and validates the fragment, then offers the
equivalent `inari://enroll?...#code=...` handoff to Device Center.

The public preview endpoint is intentionally secret-free:

```http
GET /api/inari/v1/invitations/{invitation_id}
Accept: application/json
```

It may return the controller and organization identity, invitation state,
expiry, supported protocol versions, and certificate posture. It MUST NOT
return the invitation code, a digest of that code, or any credential material.

Invitation creation, listing, and revocation are private server functions under
the controller’s OIDC session and role policy. They do not add administrative
routes to the public API.

## Enrollment

### Request

```http
POST /api/inari/v1/enrollments
Authorization: Bearer <one-use-invitation-code>
Content-Type: application/json
```

```json
{
  "protocol": {
    "version": "2026-07-12",
    "supported_versions": ["2026-07-12"]
  },
  "agent_id": "agt_123",
  "key_id": "kid_123",
  "public_jwk": {
    "kty": "OKP",
    "crv": "Ed25519",
    "alg": "EdDSA",
    "use": "sig",
    "kid": "kid_123",
    "x": "..."
  },
  "certificate_pem": null,
  "csr_pem": "-----BEGIN CERTIFICATE REQUEST-----\n...\n-----END CERTIFICATE REQUEST-----\n",
  "snapshot": {
    "generated_at": "2026-07-15T10:00:00Z",
    "protocol": {},
    "service": {},
    "security": {},
    "runtime": {},
    "capabilities": {},
    "observability": {}
  }
}
```

`agent_id`, `key_id`, `public_jwk`, `csr_pem`, and `snapshot` are required. The
snapshot describes the agent’s observed state and capabilities; it never grants
permissions to the controller.

All API errors use [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem
details with `Content-Type: application/problem+json`.

### Response

```json
{
  "selected_protocol_version": "2026-07-12",
  "controller": {
    "name": "Acme Inari Controller",
    "instance_id": "controller-01"
  },
  "permissions": {
    "controller_actions": [
      "system:read",
      "devices:read",
      "events:read",
      "jobs:create",
      "jobs:cancel",
      "commands:execute"
    ]
  },
  "data_plane": {
    "kind": "zenoh",
    "session_mode": "client",
    "connect_endpoints": ["tls/router.example.com:7447"],
    "namespace": "iot/v1/agents/agt_123",
    "serialization": "json",
    "auth": { "kind": "mtls" },
    "tls": { "close_link_on_expiration": true }
  },
  "certificate": {
    "mode": "step_ca",
    "enrollment": {
      "base_url": "https://ca.example.com",
      "trust": {
        "root_fingerprint": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
      },
      "bootstrap_auth": {
        "type": "ott",
        "token": "short-lived-agent-bound-token",
        "expires_at": "2026-07-15T10:05:00Z"
      },
      "subject": "agt_123",
      "authorized_sans": ["urn:inari:agt_123"],
      "requires_mutual_tls_after_issuance": true
    }
  },
  "enrolled_at": "2026-07-15T10:00:02Z"
}
```

The response rules are:

- `selected_protocol_version` is required and must have been advertised;
- `permissions.controller_actions` is the controller’s authority at the agent;
- `data_plane.kind` is `zenoh` and `session_mode` is `client`;
- `connect_endpoints` and `namespace` identify the managed data plane;
- `certificate` is optional, but a `step_ca` value contains one cohesive
  enrollment object;
- `bootstrap_auth.token` is a short-lived secret. The agent stores it only in
  protected secret storage and removes it after use or expiry.

### step-ca exchange

For `step_ca` enrollment:

1. The agent fetches the CA root and compares its lowercase, separator-free
   SHA-256 fingerprint with `root_fingerprint`.
2. It submits the same CSR with the controller-minted one-time token to the
   step-ca sign endpoint.
3. It verifies that the returned certificate contains the CSR key and the
   authorized identity.
4. It stores the certificate and private key through the protected local
   credential boundary.
5. It opens Zenoh with mutual TLS.
6. Later renewals use certificate-backed authentication.

The controller mints the one-time CA token only after it has verified the
invitation and CSR. It MUST NOT persist or reuse that token.

## Zenoh keyspace

The controller assigns one namespace per agent. With
`iot/v1/agents/agt_123`, the protocol uses:

| Purpose | Key expression |
| --- | --- |
| Agent presence | `iot/v1/agents/agt_123/presence/agent` |
| Latest status | `iot/v1/agents/agt_123/status/latest` |
| Live command | `iot/v1/agents/agt_123/commands/live/{command_id}` |
| Command replay query | `iot/v1/agents/agt_123/commands/history` |
| Command result | `iot/v1/agents/agt_123/results/{command_id}` |
| Runtime event | `iot/v1/agents/agt_123/events/{message_id}` |
| Agent error | `iot/v1/agents/agt_123/errors/{message_id}` |

The agent holds a liveliness token at `{namespace}/presence/agent`. Presence is
an observation, not a delivery guarantee or replay mechanism.

The optional HTTP compatibility route exposes the same keyspace directly:

```http
GET /api/zenoh/v1/iot/v1/agents/agt_123/status/latest
```

It does not replace native Zenoh traffic. The typed Inari API exposes controller
resources such as `GET /api/inari/v1/agents/{agent_id}` separately; an agent
detail includes the latest durable status observed by the controller.

## Commands

Every controller command has:

- a transport `message_id`;
- a stable `command_id` used for idempotency;
- a monotonically increasing per-agent `sequence`;
- an optional `issued_at` time;
- a discriminating `type`.

### Submit a print job

```json
{
  "type": "controller.command.submit_print_job",
  "message_id": "msg_100",
  "command_id": "cmd_100",
  "sequence": 105,
  "issued_at": "2026-07-15T10:05:00Z",
  "payload": {
    "content": {
      "kind": "text",
      "text": "Hello printer",
      "document_name": "Greeting"
    },
    "target": { "device_id": "dev_123" },
    "options": {
      "transport": "auto",
      "open_cash_drawer": false
    },
    "metadata": { "source": "controller" }
  }
}
```

### Execute a device command

```json
{
  "type": "controller.command.execute_device_command",
  "message_id": "msg_101",
  "command_id": "cmd_101",
  "sequence": 106,
  "issued_at": "2026-07-15T10:06:00Z",
  "payload": {
    "target": { "device_id": "dev_123" },
    "command": { "kind": "cut_paper", "mode": "partial" },
    "metadata": { "source": "controller" }
  }
}
```

Supported device commands are `open_cash_drawer`, `print_test_page`,
`feed_lines`, `feed_dots`, and `cut_paper`. The agent validates their bounds and
checks the granted `commands:execute` authority before dispatch.

### Cancel a job

```json
{
  "type": "controller.command.cancel_job",
  "message_id": "msg_102",
  "command_id": "cmd_102",
  "sequence": 107,
  "issued_at": "2026-07-15T10:07:00Z",
  "job_id": "job_123"
}
```

Controllers SHOULD target a stable `device_id`. `printer_name` is a convenience
for manual diagnostics and should not become a durable automation key.

## Agent publications

The agent publishes a discriminated message for each outcome:

- `agent.status.snapshot` — current service, security, runtime, capability, and
  inventory state;
- `agent.command.accepted` — accepted command and resulting local job, when one
  exists;
- `agent.command.rejected` — stable error code and safe detail;
- `agent.runtime.event` — local job or device event;
- `agent.error` — managed transport or execution failure.

Each publication has a stable `message_id`. The agent persists publications
before sending them and removes them from its outbox after Zenoh accepts the
publish. A future protocol version may add controller receipts; this version
does not claim end-to-end acknowledgement beyond that point.

## Replay and reconnect

Live delivery is not sufficient. The agent persists the last applied controller
sequence and, after reconnecting, queries:

```text
{namespace}/commands/history?from_sequence=<last_applied_sequence + 1>
```

The controller returns commands after that sequence in order. The agent applies
the recovered commands idempotently before relying on the live subscription.

`command_id` prevents duplicate execution. `sequence` establishes replay order.
`message_id` identifies transport publications. These identifiers are not
interchangeable.

## Permissions

The current controller-action vocabulary is:

- `system:read`
- `devices:read`
- `events:read`
- `jobs:create`
- `jobs:cancel`
- `commands:execute`

These values constrain what the controller may ask the agent to do. Agent
capability advertisement describes what the software supports; it never grants
the controller an action.

## Payload size

Small text, receipts, and device commands may be inline. Large PDFs, HTML, and
images should move to a future typed `content_ref` flow so command replay does
not carry unbounded bodies. Until that contract exists, deployments must enforce
their configured request and message limits.

## Controller compatibility

A compatible controller:

- implements the preview and enrollment routes;
- authenticates and transactionally consumes one-use credentials;
- selects an advertised protocol version;
- verifies JWK, CSR, and certificate identity binding;
- returns typed permissions, Zenoh endpoints, namespace, and step-ca bootstrap
  material;
- publishes commands with stable IDs and ordered sequence numbers;
- answers command-history queries;
- consumes typed agent publications from the assigned namespace;
- protects the data plane with TLS and agent client certificates;
- keeps `/api` responses JSON or Zenoh-compatible and outside the Leptos
  fallback.

Deployment topology and certificate ownership are described in
[Managed deployment](managed_gateway_stacks.md). The HTTP view of the keyspace
is documented in [Zenoh HTTP compatibility](zenoh_rest_axum.md).
