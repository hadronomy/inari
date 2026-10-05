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

The current draft version is `2026-10-03`.

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
- `dispatch_key` — X25519 public key used to seal Managed Device Work for this
  Agent;
- `csr_pem` — PKCS#10 request signed by that key;

During enrollment the controller MUST:

1. accept only the supported Ed25519 JWK shape;
2. calculate and validate the RFC 7638 thumbprint;
3. verify the CSR signature;
4. compare the CSR subject public key with the JWK;
5. bind the enrollment credential, protocol version, invitation state, and
   agent identity in one transaction;
6. consume a one-use credential only when enrollment succeeds.

An issued certificate MUST bind to the enrolled `agent_id`. Its SANs and key
usage MUST stay within the authorization approved by the controller.

Protocol `2026-10-03` authenticates enrollment with the invitation and signed CSR.
An installed certificate is not part of the enrollment request. A fresh invitation
can replace an obsolete certificate without changing the protected Agent Identity.
Upgrade the Agent and Controller together before enabling managed enrollment.
The Controller migration removes the unused stored Agent certificate column.

The Agent MUST compare the cached CA with the pinned SHA-256 fingerprint before
accepting an installed certificate or sending a certificate request. A different
cached CA triggers root bootstrap. The Agent MUST validate the installed client
chain, identity, usage, and validity against that root. An invalid certificate
requires fresh enrollment. When a fresh one-time token exists, the Agent replaces
the certificate and retains its private key. All CA endpoints MUST use HTTPS
without URL credentials, query, or fragment.

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
    "version": "2026-10-03",
    "supported_versions": ["2026-10-03"]
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
  "dispatch_key": {
    "key_id": "dispatch_0123456789abcdef",
    "kem": "dhkem_x25519_hkdf_sha256",
    "public_key_base64url": "..."
  },
  "state_signing_jwk": {
    "kty": "OKP",
    "crv": "Ed25519",
    "alg": "EdDSA",
    "use": "sig",
    "kid": "agent_state_<sha256-of-raw-public-key>",
    "x": "..."
  },
  "csr_pem": "-----BEGIN CERTIFICATE REQUEST-----\n...\n-----END CERTIFICATE REQUEST-----\n",
  "snapshot": {
    "generated_at": "2026-07-15T10:00:00Z",
    "protocol": {},
    "service": {},
    "security": {},
    "runtime": {"inventory": {"devices": []}},
    "capabilities": {},
    "observability": {}
  }
}
```

`agent_id`, `key_id`, `public_jwk`, `dispatch_key`, `state_signing_jwk`, `csr_pem`,
and `snapshot` are required. The snapshot describes the Agent’s observed state and capabilities.
It never grants permissions to the Controller. The Agent keeps the dispatch
private key in its protected secret store. The Controller stores only the public
key.

`state_signing_jwk` registers the separate Ed25519 Agent State signing key.
Its `kid` is `agent_state_` followed by the lowercase SHA-256 digest of the
32-byte public key. The Agent stores its private key in protected storage.
A storage failure or corrupt key stops enrollment. The Agent never replaces
a corrupt key or sends a private JWK field.

The Controller retains each registered Agent verification key with its owner,
purpose, public-key thumbprint, and first registration time. Enrollment cannot
assign a registered key to another Agent or another purpose. Registration of a
new state key preserves the previous public key. The state key must differ from
the Agent transport identity.

Protocol `2026-10-03` requires this field. Upgrade the Agent and Controller
together, apply the Controller migrations, and enroll each Agent with a new
invitation. Cached enrollment without the current state-key identity is invalid.
The migration registers existing Agent transport keys before new enrollments.

All API errors use [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem
details with `Content-Type: application/problem+json`.

### Device inventory

Every enrollment and managed snapshot MUST contain `runtime.inventory.devices`.
The list contains only the Devices that the operator confirmed for this Controller.
An empty list withdraws every Device from this Agent's Controller Projection.
Local Device Work and local discovery remain independent of that selection.

Each Device item uses this closed contract. Unknown or missing fields invalidate
the complete inventory.

```json
{
  "device_id": "dev_counter",
  "kind": "printer",
  "device_class": "physical",
  "display_name": "Front counter",
  "system_name": "POS-80",
  "driver_key": "windows.spooler",
  "connection_state": "online",
  "transport": "spooler",
  "identity_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "capabilities": ["raw", "text"],
  "metadata": {}
}
```

- `kind` is `printer`, `scale`, `scanner`, or `display`.
- `device_class` is `physical` or `virtual`.
- `connection_state` is `online` or `offline`.
- `transport` is `spooler`, `network`, `usb`, `hid`, or `serial`.
- `identity_digest` is the lowercase SHA-256 digest of the UTF-8 Agent identity
  stable key. The Agent MUST NOT publish that raw stable key.
- `capabilities` contains distinct discovery values: `raw`, `text`, `documents`,
  or `cash_drawer`. These values do not authorize Managed Device Work.
- `metadata` contains at most 16 KiB of serialized JSON.

The list contains at most 1,024 Devices. Names contain 1–1,024 UTF-8 bytes and
no control characters. Device identifiers and `(kind, identity_digest)` pairs
MUST be unique within one Agent inventory. A Device identifier MUST retain its
kind and identity digest throughout the current enrollment.

The Controller keys Device rows by `(agent_id, device_id)`. Separate Agents can
publish the same host-local Device identity. A valid snapshot replaces only
its Agent's Projection and retains each present Device's `first_seen_at`.
The Controller currently stores an empty Device Capability list. Discovery does
not prove a Driver Profile, Device Test Result, or Hardware Certification Matrix entry.

The Controller applies a snapshot only to its current enrollment and only when
`generated_at` exceeds the latest applied snapshot. Exact publication replay
and older snapshots preserve history without changing the Projection.
New enrollment resets the Projection to its new Site. Credential retirement
deletes that Agent's Projection. Historical publications remain available.

Invalid inventory rejects the whole publication or enrollment transaction.
The Controller records rejected publications as `agent.inventory_rejected`
audit events with outcome `denied`. These records contain no inventory content.

#### Inventory rollout

Upgrade every Agent before deploying this Controller change. Older Agents omit
the required inventory fields, so the new Controller rejects their enrollment
and snapshot publications. The protocol draft remains `2026-10-03`.
Changes to this closed Device item contract require an explicit protocol upgrade.

Stop old Controller replicas before applying migration
`m20261005_223036_project_device_inventory`. The Helm migration hook runs before
the Deployment update. The `Recreate` strategy alone does not stop old replicas
before that hook. Keep the old replicas at zero until the migration completes.
Old replicas cannot read the renamed Device column or the
new composite key. A rollback across this schema migration requires a matching
Recovery Point and its Release Set.

The migration clears derived Device rows with unverified identity fingerprints.
Current Agent snapshots rebuild them. It preserves invitations, publications,
and Print Audit Records. Managed Work retains its immutable Device identity
after the current Device Projection disappears.
Managed dispatch remains disabled until its authority
and Recovery Point acceptance gates pass.

### Response

```json
{
  "selected_protocol_version": "2026-10-03",
  "controller": {
    "name": "Acme Inari Controller",
    "instance_id": "controller-01"
  },
  "permissions": {
    "controller_actions": [
      "system:read",
      "devices:read",
      "events:read",
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

### Invitation consumption and retry

The Controller consumes an invitation in the database transaction that stores
the Agent. The transaction locks the invitation row and reads the current time
after it gets the lock. An invitation that expires while a request waits for
the lock rejects that request. The transaction then does these steps in
sequence:

1. It rejects the request when the failed-attempt limit is reached.
2. It compares the invitation secret. A wrong secret records a failed attempt,
   commits that record, and returns `403`.
3. It rejects an expired invitation and an invitation for a different
   Organization or Site.
4. It mints the one-time CA token. It does not use the network while it holds
   the lock.
5. It stores the Agent, its verification keys, the invitation state, the
   snapshot, the enrollment fingerprint, and one `agent.enrolled` audit event.
   Then it commits.

If token creation or a write fails, the transaction rolls back. The invitation
stays `created`, and the Agent can send the same request again.

The enrollment fingerprint is the SHA-256 digest of the
[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) form of the validated facts:
Agent ID, key ID, CSR fingerprint, transport and state key thumbprints, dispatch
key, Organization, Site, protocol version, namespace, and Controller actions.
The enrollment time and the snapshot are not part of the fingerprint.

When the Agent does not receive the response, it can send the same request
with the same invitation code. The Controller accepts this retry only when all
these conditions are true:

- the invitation is `enrolled` and has not expired;
- the request comes from the bound Agent and key;
- the enrollment fingerprint is the same;
- no later enrollment changed the Agent.

An accepted retry returns a new one-time token and the original `enrolled_at`.
It does not write the Agent, its keys, or the audit event again. The retry
holds a shared lock on the Agent until it commits. An enrollment through another
invitation that changes the Agent waits for that lock. A changed
request from the bound Agent returns `409`. A request from another Agent returns
`403`. An `online`, `revoked`, `expired`, or `failed` invitation returns `403`.
Revocation of an `enrolled` invitation stops all later retries.

Invitations have no `claimed` state. The migration changes a `claimed`
invitation to `failed`, so that Agent needs a new invitation. An `enrolled`
invitation from before the migration has no fingerprint. A retry with that
invitation returns `409`.

### step-ca exchange

For `step_ca` enrollment:

1. The Agent requires a valid SHA-256 `root_fingerprint` before any CA request.
   It fetches `GET /root/{root_fingerprint}` and reads the JSON `ca` PEM string.
   It verifies exactly one CA root against that fingerprint.
2. It submits the same CSR with the controller-minted one-time token to the
   step-ca `POST /sign` endpoint.
3. It verifies that the returned certificate contains the CSR key, the authorized
   identity, client authentication usage, and `digitalSignature` key usage.
   It verifies the full chain against the pinned root.
4. It stores the certificate and private key through the protected local
   credential boundary.
5. It opens Zenoh with mutual TLS.
6. Later renewals use certificate-backed authentication with `POST /renew`.

The CA response supplies untrusted intermediate certificates. It MUST NOT replace
the pinned root. The Agent stores the verified leaf and intermediate chain for
mutual TLS and preserves the root that passed fingerprint verification.
Root bootstrap rejects certificate bundles. Enrollment and renewal TLS trust
only the pinned root. They MUST NOT add operating-system trust roots or send a
one-time token before pinned trust is available.

Controller invitation preview and enrollment HTTPS use system trust and the
explicit Controller CA bundle. They MUST NOT use the managed CA or present a
managed client certificate. A new operator invitation supersedes cached enrollment
and configured enrollment credentials. Unsupported cached protocol versions
require fresh enrollment. Concurrent enrollment callers share one request, and a
superseded response MUST NOT consume a newer invitation.

Every data-plane operation requires a current certificate lifecycle result.
Validation failure closes an existing session before publication or Device Work.
Commands from a superseded enrollment MUST NOT execute under the new enrollment.
Certificate renewal opens a new session with the renewed certificate.

The controller mints the one-time CA token only after it has verified the
invitation and CSR. It MUST NOT persist or reuse that token.

The Agent Identity uses an Ed25519 key. Let `digest` be the lowercase hexadecimal
SHA-256 digest of the raw 32-byte public key. `agent_id` MUST equal `agt_` followed
by the first 24 digest characters. `key_id` MUST equal `kid_` followed by the first
12 digest characters. The Controller rejects different identifiers before it
reads the invitation or issues a CA token.

The CSR subject MUST contain exactly one common name equal to `agent_id`. Its
SAN extension MUST contain exactly one URI, `urn:inari:<agent_id>`. The Controller
checks the signed CSR and an existing certificate against this contract.
Certificate issuance and renewal use the same identity contract on the Agent.
The Agent rejects missing, additional, duplicate, or incorrectly typed names,
even when the CA signature and public key are valid.

Certificate names come from the protected Agent Identity. The Controller does
not accept a global SAN override. Remove `step_ca_authorized_sans` from a
Controller configuration and `managedGateway.certificate.stepCa.authorizedSans`
from Helm values when upgrading an existing step-ca deployment. A transport key
replacement creates a new Agent Identity.

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
| Signed Print Job commit query | `iot/v1/agents/agt_123/state/commit` |
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

## Managed Work admission

The Odoo backend uses a separate HTTPS workload API for report and label
admission:

| Operation | Route |
| --- | --- |
| Check the target and get its dispatch key | `POST /api/inari/v1/managed-work/preflight` |
| Submit sealed work | `POST /api/inari/v1/managed-work` |
| Read durable state | `GET /api/inari/v1/managed-work/{managed_work_id}` |

These routes accept an OIDC workload access token. The token must use the
configured workload audience. It must contain `inari_database`,
`inari_company_id`, `inari_organization_id`, and a `scope` claim. Write routes
require `managed_work:write`. The read route requires `managed_work:read`. Each
request must match the database, company, and Organization in the token.

Preflight checks the durable Agent and Device scope, online state, print
capability, and current dispatch key. A ready result fixes the work deadline,
idempotency deadline, payload media type, and dispatch key before Odoo renders
the document.

Submission requires `Idempotency-Key` and sends one PDF or Label Document over
HTTPS. The Payload Fingerprint covers the Contract Major, operation, Device ID,
media type, exact document bytes, canonical options, and resolved work deadline.
The Controller checks this full fingerprint against the preflight deadline.
Changing the fingerprint during replay is a conflict.

The Controller signs and seals the Agent dispatch with HPKE. It encrypts the
pending dispatch at rest with a per-payload key protected by OpenBao Transit.
Protected payload, fingerprints, Binding claim, scope, deadlines, and state commit
in one transaction. Plaintext document bytes remain in memory during admission.
The PDF plaintext limit is 10 MiB. The Label Document plaintext limit is 2 MiB.
Queue admission returns HTTP 429 when the Agent or Organization limit is full.

## Commands

Every controller command has:

- a transport `message_id`;
- a stable `command_id` used for idempotency;
- a monotonically increasing per-agent `sequence`;
- an optional `issued_at` time;
- a discriminating `type`.

Managed document delivery uses `controller.command.dispatch_device_work`.
The dispatch contains signed Managed Device Work inside an HPKE envelope addressed
to the Agent dispatch key. It names a stable Device ID and a Report Binding.

Authenticated data binds the Agent, Organization, Site, Managed Work, sequence,
Dispatch Epoch, Payload Fingerprint, and deadlines. `expires_at` limits the
dispatch credential. `work_expires_at` fixes the Device Work deadline and uses UTC
with six fractional digits. The dispatch credential cannot outlive Device Work.
The Agent fingerprints and admits work with `work_expires_at`; a shorter dispatch
credential lifetime does not shorten the admitted Print Job deadline.

Upgrade the Controller, Agent, and submitting clients together for this contract.
Stop new admissions before the upgrade. The Controller migration refuses active
pending or dispatching work until it finishes or expires. It then deletes retained
payloads, marks expired pending work `Expired`, and marks expired dispatching work
`RecoveryUncertain`. Historical fingerprints remain unchanged. Records with the
old document-only fingerprint cannot reconcile against full Device Work evidence;
the upgrade does not infer a Print Job outcome or authorize another print.

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

Controllers MUST target one stable `device_id`.

## Agent publications

The agent publishes a discriminated message for each outcome:

- `agent.status.snapshot` — current service, security, runtime, capability, and
  inventory state;
- `agent.command.accepted` — accepted command and resulting local job, when one
  exists;
- `agent.command.rejected` — stable error code and safe detail;
- `agent.runtime.event` — local job or device event;
- `agent.error` — managed transport or execution failure.

Each publication has a stable `message_id`. The Agent persists publications
before sending them. Signed Print Job observations require a Controller storage
receipt before the Agent marks them sent. Other publications become sent after
Zenoh accepts the publish; these do not have end-to-end storage receipts.

For Managed Work, unsigned `agent.command.accepted` and `agent.command.rejected`
publications are informational. The Controller MUST NOT use them to change
Managed Work or its dispatch state, or to delete a Managed Payload. Acceptance
requires a verified Agent State Envelope. A rejection receipt does not prove
that durable Agent acceptance did not occur.

### Operator selection

For a managed connection, the Agent MUST publish only the Device IDs that the
operator confirmed in native Setup for the current Controller. This selection
controls inventory, its Device counts, Device events, and local job events.
A missing or invalid confirmation publishes no Devices. A new invitation clears
the selection. The Agent reads selection changes without a runtime restart.
It checks pending Device and local job events again before publication. Events
for unselected Devices leave the outbox without a delivery record.

New Controller Device actions MUST target a selected Device. New Managed Device
Work checks selection before staging and again before durable Agent acceptance.
An unselected Device returns `permission_denied` before staging. Withdrawal
during staging aborts admission with `capability_changed` and creates no Print Job.
Recovery applies the same check before it completes a staged admission.

An exact accepted Retry MUST retain its original result after selection changes.
Accepted Controller work retains its audit events. Selection does not grant a
Device Capability, replace Device Test evidence, or change Local Device Work.

### Signed Print Job observations

For a managed Print Job, `agent.runtime.event` uses `resource_kind: "print_job"`.
Its `resource_id` and top-level `job_id` identify the public Print Job.
The event payload contains `state_envelope`, an attached compact JWS signed with
the registered Agent State key. Its protected header uses `alg: "EdDSA"`, the
registered `kid`, and `typ: "application/inari-agent-state+jws"`.
The signed payload uses RFC 8785 canonical JSON.

Each envelope contains the Agent ID, Agent Boot ID, Dispatch Epoch,
reconciliation session ID, envelope ID, and observation and issue times.
The envelope sequence and durable state sequence use the local Print Job journal
position. The nested `job` contains the public Print Job and Print Intent IDs,
Managed Work ID, Device ID, Print Origin, state, state version, lifecycle times,
contract version, and optional error or Output Evidence.
The Payload Fingerprint uses the `sha256:<lowercase-hex>` form.
`output_confirmed` requires `device`, `spooler`, or `transport` Output Evidence.
Missing or unknown evidence is rejected. The evidence level must meet the
Driver Profile contract used at admission.

The Agent commits each signed publication and its journal cursor in one SQLite
transaction. A restart preserves the exact pending envelope. Historical events
retain their original state and state version. The projection can recover an
admitted Print Job even when its command acceptance reply was not recorded.

Managed replies, signed observations, and projection cursors are bound to the
original Agent, Organization, and Site. An enrollment change does not send pending
managed publications to the new recipient. Migration `20260906_0015` adds this
scope to existing managed replies from their stored dispatch command.

The Agent sends each signed publication as the JSON payload of a Zenoh query to
`{namespace}/state/commit`. The request is limited to 128 KiB. The Controller
returns a JSON receipt only after it commits the verified observation:

```json
{
  "contract_major": 1,
  "message_id": "ase_example",
  "state_envelope_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

The digest covers the exact UTF-8 compact JWS. The Agent requires the same query
key, message ID, digest, and contract version. An absent or invalid receipt leaves
the publication pending. Retries keep the original message and envelope. A lost
reply after commit therefore causes an idempotent retry, not another print.
Migration `20260906_0016` requeues previously sent signed observations once, so
transport-only delivery does not become an assumed storage receipt.

The managed router permits incoming state commit queries and outgoing receipts.
It rejects Agent declarations and replies on this key. Deploy this policy with
the Controller and Agent update. Receipt trust depends on the authenticated
router connection and these direction-specific permissions.

The Controller verifies the envelope with the registered State key for the
authenticated Agent. It checks the signed scope, Print Intent, Device, Report
Binding, Payload Fingerprint, and deadline against Managed Work before recording
the observation. Transport identity keys cannot sign Agent State.

The Managed Work response includes `print_job_observation` when verified evidence
exists. This field contains the parsed `observation` and original `state_envelope`
for independent signature verification. Managed Work remains `accepted` while
the nested Print Job state describes physical execution.

A valid observation proves Agent acceptance even if the command acceptance reply
was lost. The Controller records the observation, stops dispatch, and deletes the
protected payload in one transaction. Lower state versions cannot replace the
current projection. A changed snapshot at the same state version is a conflict.
Terminal states remain immutable, including `outcome_unknown`.

Migration `m20260906_223031_store_agent_state_observations` adds the current
projection and signed observation history. It also requires each Agent Print Job
ID to identify one Managed Work record. Existing Managed Work has no observation
until the Controller receives verified Agent evidence.

Enrollment retires the previous verification key for each purpose in the same
transaction that registers its replacement. Retirement is permanent. A retired
Agent State key can verify an exact observation already stored before retirement.
It cannot introduce another observation, including one with a backdated timestamp.

Migration `m20261002_223032_retire_agent_verification_keys` adds the retirement
timestamp and permits one active key per Agent and purpose. It retires earlier
keys. If existing Agent State keys share the latest registration time, it retires
all ambiguous keys. The Agent must enroll with a new key before fresh evidence
can enter the Controller.

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
- `jobs:cancel`
- `commands:execute`

These values constrain what the controller may ask the agent to do. Agent
capability advertisement describes what the software supports; it never grants
the controller an action.

## Payload size

Device commands contain no document bytes. Managed Device Work uses its own
bounded, encrypted payload contract. Command replay never carries document
content.

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
