# Inari Odoo integration design

Status: accepted architecture. Shared understanding is complete.

This document records accepted product decisions for the Inari Odoo Addon. The
[research record](./odoo-inari-integration-research.md) contains the supporting
evidence.

## Product contract

The Inari Odoo Addon is a reusable and unbranded product. It preserves each
supported Odoo workflow through a Device Adapter.

Inari handles a device only after that device contract passes the required
test suite. A Native Device Path remains active until an Inari Device Adapter
meets that standard.

The first supported Odoo target is Community 19. Enterprise IoT becomes a
separate conformance target after a licensed source tree and test database are
available.

The first POS Device Adapter keeps Odoo `OrderReceipt` and
`OrderChangeReceipt` as the receipt renderers. It submits their JPEG output as
`receipt_image`.

The public print contract exposes only `receipt_image`, `report_pdf`, and
`label_document`. It does not expose general raw, text, HTML, or structured
receipt operations.

## System responsibilities

The Odoo addon owns Device Bindings, operator permissions, workflow routing,
status, and recovery controls.

The Agent owns Device identity, discovery, Drivers, local queues, retries, and
execution. The Controller owns Organizations, Sites, policy, enrollment,
security audit records, and Managed Device Work.

The Inari contracts are the canonical device boundary. Odoo IoT compatibility
routes can exist only as narrow Device Adapters.

## Work paths

Operator sessions submit Local Device Work directly to an Agent. This path
preserves POS operation during a Controller or wide-area network failure.

The Odoo backend submits all `report_pdf` and `label_document` work through the
Controller as Managed Device Work. This rule also applies when an operator
starts the report in a browser.

Before a POS Device Adapter renders or submits Device Work, it creates one
immutable Submission Context. The context contains the Print Origin, Binding
Revision, Device, POS session, document kind, content revision, and copy
ordinal. It contains no actor or authority claim. The Agent derives those
claims from the accepted Client Grant.

The Device Adapter copies the Submission Context into the Print Intent and
Print Audit Record. A Retry reuses the context. A Reprint creates a new context
and copy ordinal.

The Odoo backend uses a versioned HTTPS Managed Workload Interface. OIDC
protects the Interface. Each Odoo company uses one Organization Workload
Identity.

Every request binds its database, company, Organization, Site, and target
Agent. The Controller rejects a missing or mismatched scope.

The Managed Workload Interface exposes preflight, submission, single-work query,
batch Job Reconciliation, and Projection delta operations. It does not expose
Zenoh or internal Controller transport models.

The Controller forwards authoritative Agent State Envelopes to a
company-specific Odoo webhook. The stream includes `Accepted`, `In Progress`,
and terminal states.

The Controller signs each webhook with RFC 9421 HTTP Message Signatures and a
content digest. The signed components bind the method, target URI, content
digest, key identity, timestamp, delivery identity, audience, database, and
Organization.

The content digest uses SHA-256. The signature covers `@method`, `@authority`,
`@path`, `content-type`, `content-digest`, delivery identity, audience,
database, Organization, and attempt number. Signature parameters contain
`created`, `expires`, `keyid`, nonce, `alg=ed25519`, and
`tag=inari-webhook-v1`.

Webhook retries keep the same delivery identity. Each retry uses a fresh
timestamp and signature.

Odoo accepts a five-minute timestamp window. It keeps delivery identities for
90 days and rejects a replay before business processing.

The Controller retries for 24 hours with bounded backoff. Odoo also runs
cursor Job Reconciliation every five minutes.

Odoo derives the webhook URL from `web.base.url` and one fixed addon route.
It registers that URL through the authenticated Managed Workload Interface.

The Controller accepts an HTTPS host only from the Organization allowlist. It
runs a signed challenge before it activates the webhook.

The webhook uses no shared secret. The route disables browser sessions, CORS,
and CSRF processing. Every HTTP ingress uses a 16 MiB body limit.

Each reconciliation cursor is opaque and belongs to one Organization. The
Controller retains its event history for 90 days.

Odoo commits each new cursor with its related records. An expired cursor
returns `410 cursor_expired` and starts a bounded full reconciliation.

Odoo stores only a higher resource version. It accepts duplicate and older
deliveries without changing state.

A version gap starts batch Job Reconciliation. Odoo commits the Print Audit
Record before it returns `204`.

The Controller creates a Managed Work record before Agent delivery. It returns
`202 pending_agent` with a stable `managed_work_id`.

Managed Work uses `pending_agent`, `dispatching`, `accepted`, `rejected`,
`canceled`, `expired`, and `recovery_uncertain`. No Print Job exists before
Agent acceptance.

The transitions are:

```text
pending_agent -> dispatching | rejected | canceled | expired
dispatching -> accepted | rejected | recovery_uncertain
recovery_uncertain -> accepted | canceled | expired from signed Agent evidence
```

The `accepted` state links one authoritative Print Job. Managed Work does not
store later authoritative Print Job states.

The Controller preserves the Idempotency Key and Payload Fingerprint. Odoo
reports success only after the Agent returns `Accepted`.

A marked report observes Managed Work for Agent acceptance for at most 10
seconds. If the work remains Pending Agent, Odoo returns a handled `Waiting for
Agent` result. This result is not print success and never starts native browser
printing.

The global recovery panel continues Job Reconciliation after this observation
window. An automatic sequence records the pending document and continues.

### Local POS print recovery

One POS recovery coordinator owns customer-receipt and preparation-ticket
recovery. It serializes work for each Binding Revision and Device. The POS
stores the content-free Submission Context and recovery state in IndexedDB
before it calls the Agent. The IndexedDB database is scoped to the Odoo
database, company, and POS configuration. It keeps the exact JPEG only in the
current tab.

If the Agent response is uncertain, the coordinator queries the protected
`/v1/jobs/query` endpoint before it permits a retry. Each query contains no
more than 100 Print Intent identities. It also polls admitted work while the
state is `accepted` or `in_progress`, so a later failure becomes visible without
an operator refresh. A missing Print Intent permits an exact same-tab retry.
After a reload, missing work becomes `content_unavailable` because the JPEG no
longer exists. The addon does not render replacement content from partial
state.

`outcome_unknown` never permits an automatic retry. The operator must check
the printer, query the Agent again, or explicitly finish the task without a
confirmed ticket. Customer receipts also permit an explicit browser print
while the current tab still holds the exact JPEG. Preparation tickets do not
use browser print.

Odoo increments receipt copy state only after Agent admission. Odoo advances
preparation change state only after Agent admission or an explicit operator
dismissal. Native printers keep Odoo's native retry dialog. An authoritative
Inari printer uses only the global Inari recovery dialog, including in a mixed
native and Inari printer setup.

The cash-drawer adapter writes one content-free Drawer Intent to IndexedDB
before it contacts the Agent. The record includes only stable scope and action
identities. A storage failure blocks the pulse. A safe pre-I/O failure keeps
the same identity for an explicit Retry. An uncertain result blocks a new
pulse and tells the operator to request manager review.

Pending work expires at the deadline that the Controller assigned at Controller
Admission. Agent acceptance links the Managed Work record to the authoritative
Print Job.

Odoo does not supply `expires_at`. The Controller derives an immutable deadline
from the operation policy at Controller Admission.

Preparation work expires after 60 seconds. Receipt and report work expires
after five minutes.

For reports and labels, the deadline starts when the Controller creates Managed
Work after Odoo renders the document. The Agent checks the exact deadline
before acceptance and before the first Device I/O marker. A Retry keeps that
deadline.

The Controller enforces a unique `(Organization, Idempotency Key)` pair for 90
days from Controller Admission. An exact Payload Fingerprint returns the
existing Managed Work record. A different Payload Fingerprint returns `409`.

The Controller encrypts each pending payload with a random AES-256-GCM data
key. OpenBao Transit protects that key.

One Transit wrapping key serves Managed Payloads in one environment. Named-key
deletion and plaintext Transit backup stay disabled. The key rotates every 90
days. Hard key deletion is a controlled security response.

The first release stores each Managed Payload in a separate PostgreSQL table.
The table stores ciphertext, its wrapped data key, key version, fingerprint,
byte count, deadline, and deletion time.

Pre-existing Controller delivery records store only identifiers, lifecycle
state, and dispatch metadata. They never store Managed Payload content. The
design adds no object store for first-release Managed Payloads.

At dispatch, the Controller encrypts the complete Controller-signed dispatch
envelope to the target Agent with RFC 9180 HPKE Base mode. The suite uses
X25519, HKDF-SHA256, and AES-256-GCM.

The suite identifiers are `DHKEM(X25519, HKDF-SHA256)` `0x0020`,
HKDF-SHA256 `0x0001`, and AES-256-GCM `0x0002`. The Controller creates one HPKE
context for each dispatch.

The frame contains protocol version, suite, recipient `kid`, encapsulated key,
canonical AAD, and ciphertext. Binary values use unpadded base64url. HPKE
`info` binds the protocol label, version, Agent, and recipient `kid`.

The plaintext is the compact JWS dispatch envelope. The frame contains no
plaintext Device Work content. The Agent rejects a difference between the AAD
and the signed dispatch claims.

Each Agent has a separate X25519 encryption recipient key. The Agent does not
reuse a TLS key or Ed25519 signing key for encryption.

RFC 8785 canonical JSON metadata is HPKE authenticated data. It binds the
Organization, Site, Agent, Managed Work, Idempotency Key, Payload Fingerprint,
Dispatch Epoch, sequence, issue time, and expiry.

The Agent rotates its encryption recipient key every 30 days and publishes the
new public key through the Controller. It keeps the prior private key for 15
minutes. Revocation rejects the old `kid` immediately.

Zenoh routers receive only the Agent-encrypted payload. The Controller keeps
plaintext only in bounded memory during conversion and dispatch.

Each Agent certificate contains one exact URI SAN in this form:
`urn:inari:organization:<organization_id>:site:<site_id>:agent:<agent_id>`.
One Zenoh router ACL subject authorizes that identity for one exact Agent
keyspace and denies every other keyspace.

Controllers, routers, and Agents use separate certificate identities. The
certificate authority controls each subject. Revocation closes active sessions
and removes keyspace access within 60 seconds.

Each Dispatch Envelope contains its target, tenant scope, monotonic sequence,
Dispatch Epoch, audience, issue time, and expiry. The Agent enforces one atomic
`(Dispatch Epoch, sequence)` state per Agent.

An exact replay returns the stored result. A sequence gap blocks execution and
starts reconciliation. The Agent applies the same replay checks to live
delivery and recovery history.

If OpenBao cannot wrap or unwrap a data key, the Controller rejects new
content work with `503 service_unavailable`. It pauses affected dispatch and
raises an immediate security alert.

Content-free queries and webhook delivery remain active. The Controller keeps
no plaintext data key after active dispatch.

The Controller deletes payload content immediately after Agent `Accepted`,
rejection, cancellation, expiry, or entry into Recovery Uncertain. It retains
content-free metadata for 90 days.

One PostgreSQL transaction deletes the wrapped data key and live ciphertext
row. It records the deletion time in content-free metadata.

WAL archives and backups retain encrypted historical pages until their normal
14-day expiry. The recovery procedure never dispatches restored expired work.

Managed Payload deletion is immediate in live systems and final after the
Backup Exposure Window ends. All PostgreSQL and OpenBao backups are encrypted.
Every restore records its actor, recovery point, purpose, and result.

A Controller backup or recovery starts a Recovery Fence. The fence rejects new
Managed Device Work and stops managed dispatch. Local Device Work continues.

One PostgreSQL row stores the fence state and Admission Epoch. Managed Work
acceptance and fence activation lock this row with `SELECT FOR UPDATE`.

Acceptance stores the current Admission Epoch in the same transaction that
creates Managed Work and its Managed Payload. Fence activation increments the
epoch and sets the fence state. A request that loses the row-lock race returns
`recovery_fence` and creates no Managed Payload.

One signed Recovery Point manifest contains:

- the Recovery Point identity
- the Dispatch Epoch
- the Controller Release Set and schema revision
- the PostgreSQL backup, WAL range, timeline, and final LSN
- the OpenBao snapshot digest, Raft index, and Raft term
- the external auto-unseal key identity and version
- the recovery-signing identity and version
- the creation time.

Google Cloud KMS in the `europe` multi-region provides the production trust
root through Workload Identity Federation. Separate keys provide OpenBao
auto-unseal, backup wrapping, and Ed25519 Recovery Point signing.

A Cloud KMS outage blocks a new OpenBao unseal. The deployment has no static
unseal fallback. An already running OpenBao cluster remains active. Local
Device Work continues.

Locked `eur4` dual-region Google Cloud Storage holds encrypted backup artifacts
and each immutable manifest. Recovery keys remain outside the cluster and
backup set.

Root-key recovery and destructive key actions require approval from two people.
The recovery audit records both actors. Agent Quarantine uses the separate
single-System-Administrator rule.

Backup artifacts are encrypted before local persistence or off-site upload. A
missing database, OpenBao snapshot, checksum, encryption result, or upload
makes the Recovery Point incomplete.

The Controller records the fence before it captures the PostgreSQL LSN and
OpenBao Raft index. It checks every eligible wrapped data key against the
selected snapshot.

The system creates one daily fenced base Recovery Point. It also writes an
immutable signed Recovery Checkpoint at least every five minutes from the WAL
archive. Each OpenBao Transit key change triggers a new OpenBao snapshot.

The Controller releases the fence after it captures a consistent database and
OpenBao boundary. The Recovery Fence has a 60-second limit.

If capture reaches this limit, the Controller aborts the Recovery Point and
releases the fence. Artifact encryption, upload, and restore checks continue
outside a successful fence.

PostgreSQL and OpenBao have no shared commit. The Recovery Point records their
checked relationship and does not claim cross-system atomicity.

The restore process creates new PostgreSQL and OpenBao instances in an
isolated namespace. The recovered Controller starts without dispatch
credentials.

The recovery process checks each eligible Managed Payload. It decrypts the
payload, calculates its fingerprint, compares that value, and deletes the
plaintext from memory.

A reconciliation-only Controller reads signed Agent State Envelopes. An Agent
terminal state replaces the restored Controller projection.

An Agent `Accepted` or `In Progress` state remains authoritative. The
Controller never sends that work again.

Never-dispatched Pending Agent work can return to dispatch only when all these
conditions are true:

- the Agent has no record
- the Agent is reachable
- the original deadline is current
- the Payload Fingerprint matches.

An unreachable Agent after dispatch, or a missing record after dispatch,
produces Recovery Uncertain. Restored work past its deadline records `expired`
with the restricted cause `expired_recovery`. Neither state permits dispatch.

A Site with a permanently lost Agent remains blocked until Agent Quarantine.
A Device Manager records signed Agent-absence evidence, physical inventory, and
duplicate-output evidence. A System Administrator approves Agent Quarantine
permanently.

The Site then uses a new Agent identity and new Binding Revisions. Recovery
Uncertain work keeps its state.

The recovered Controller creates a strictly higher Dispatch Epoch and rotates
its workload identity. Each Agent records the new epoch before dispatch opens.

The new epoch is higher than the restored value and every reachable Agent
value. Managed dispatch stays blocked for a Site with an unreachable Agent.

Managed dispatch opens after reconciliation, Recovery Canaries, and the
one-hour Site soak. Recovery Canaries run separately for each Site.

Operation-specific Recovery Canaries run at the start and end of the soak. The
gate includes one Retry, one Agent reconnect, and zero Outcome Unknown results.

Print canaries require exactly one marked physical output. A Certified Scale
uses a signed reference-weight result. Drawer and scanner canaries use their
operation-specific signed evidence.

The gate permits no new security or contract error. Required active Devices
cannot skip the gate. A canary failure blocks its Site and does not block a
passed Site.

Continuous WAL archiving and signed Recovery Checkpoints have a five-minute
maximum lag. Encrypted base backups and aligned OpenBao snapshots run daily.

Backup checks finish within 15 minutes. A full recovery drill runs monthly.
Content-free recovery evidence remains for at least 90 days.

The production recovery-time objective is 60 minutes from the start of each
Site soak to passed Site canaries and controlled dispatch. Reconciliation must
finish before that soak starts.

A Recovery Point manifest remains through its 14-day Backup Exposure Window
and one later completed recovery drill.

The Controller accepts at most 32 pending jobs or 64 MiB for one Agent. One
Organization accepts at most 256 jobs or 512 MiB.

The Controller returns `429` before durable acceptance when either limit is
full. Controlled details include job count, byte use, and retry timing.

Odoo displays pending work as `Waiting for Agent`. It displays the target
Device, deadline, and last Agent contact.

Odoo creates one Device Manager activity after two minutes or half the work
lifetime, whichever occurs first. Unrelated POS sessions do not display this
managed-work state.

The Controller serializes Agent acceptance, cancellation, and expiry with one
Managed Work row lock.

Odoo can cancel work directly only in `pending_agent`. The Controller deletes
the encrypted payload and records `canceled` before dispatch starts.

After dispatch starts, a cancellation request stops later dispatch attempts.
It also asks the Agent for signed acceptance evidence. The Controller records
`canceled` only after signed evidence proves that the Agent did not accept.

If this evidence is unavailable, the Controller records `recovery_uncertain`.
Signed late evidence can resolve this state to `accepted`, `canceled`, or
`expired`.

The Agent durable acceptance time decides deadline compliance. An acceptance
before `expires_at` remains valid when the Controller receives its evidence
later. The Controller never retracts or silently resubmits accepted work.

After `cursor_expired`, Odoo reconciles nonterminal records and records changed
within 90 days. Each query contains at most 100 stable identities.

Projection recovery uses a separate full snapshot. Odoo commits each bounded
page before it advances the related cursor.

The Controller publishes its separate Ed25519 verification keys through the
Managed Workload Interface. Odoo refreshes this set every hour.

An unknown `kid` starts one immediate refresh. Key rotation overlaps old and
new signing keys for 24 hours. Public-key history remains available for 90
days.

The Controller uses separate Ed25519 keys for dispatch envelopes and webhooks.
Each Agent uses a separate Ed25519 key for Agent State Envelopes. Each company
uses a separate Pairing Assertion key. Each database uses a Compliance Export
key. Each Controller environment uses a Recovery Point key.

Dispatch and Agent State Envelopes use compact JWS over RFC 8785 canonical
JSON. Webhooks use RFC 9421 HTTP Message Signatures with a content digest.

Each compact JWS has an attached payload and protected `alg`, `typ`, and `kid`
headers. Dispatch uses `application/inari-dispatch+jws`. Agent state uses
`application/inari-agent-state+jws`. Each verifier rejects an unprotected
header, unknown critical header, wrong key purpose, or noncanonical payload.

An Agent can run on one POS workstation or on a shared Agent Host. Each
operator session receives a separate Client Grant.

The POS browser uses a locally trusted HTTPS Agent endpoint. Device Center
provisions certificate trust. Browser Local Network Access controls network
reachability and does not grant Inari permissions.

The POS browser creates a non-exportable Ed25519 key for Client Pairing. Device
Center displays the Odoo origin, database, POS configuration, Devices, and
requested scopes before a Device Manager approves the request.

Odoo issues a short-lived signed Pairing Assertion for each Pairing Request.
It binds the browser JWK thumbprint, Pairing Request, Agent, audience, database,
company, Organization, Site, POS configuration, actor, session nonce, role,
scopes, issue time, expiry, and one-time `jti`.

The assertion is compact JWS with `iss`, `sub`, `aud`, `iat`, `exp`, and `jti`
claims. The company Pairing Assertion key signs through OpenBao Transit. Its
private key never enters Odoo memory or the Controller.

The browser requests the Pairing Assertion from
`POST /inari_devices/pairing/v1/assertion`. This same-origin Odoo JSON-RPC route
requires an authenticated Device Operator and an active POS session. Odoo
validates the complete approved Pairing Request against the active receipt
Binding Revision before it signs anything. It issues one assertion for each
Pairing Request and safely replays the same result for an identical request.
The route enables no CORS policy. Odoo's JSON-RPC dispatcher does not process
CSRF tokens, and browsers cannot submit its JSON content type across origins
without CORS approval.

The Odoo pod authenticates to OpenBao with its Kubernetes workload identity.
Odoo stores no OpenBao token or private signing key in its database. Each
database and company uses the deterministic Transit key
`inari-odoo-pairing-<database>-<company_id>`. Odoo reads the current Ed25519 key
version first. It places that exact version in the protected `kid`, signs the
compact JWS input through Transit, and rejects a different key type or a
malformed signature.

The Odoo deployment supplies `INARI_OPENBAO_ADDR` and
`INARI_OPENBAO_KUBERNETES_ROLE`. It can also set the Kubernetes auth mount,
Transit mount, OpenBao namespace, service-account token file, CA certificate,
and client certificate files. OpenBao access uses HTTPS and a bounded request
timeout.

The Agent checks the assertion through the Odoo signing key in Controller
policy. The Client Grant contains the same actor and business scope.

The Agent stores the one-time assertion `jti` before it issues the Client
Grant. A replay cannot create another grant after a restart.

One browser pairing module owns the non-exportable Ed25519 key, IndexedDB
record, DPoP proofs, Agent nonce retry, approval polling, Pairing Assertion
request, Client Grant admission, renewal, and cancellation. POS printing sees
only the resulting credential interface. If a ticket starts pairing, the same
ticket resumes after the Agent issues the Client Grant.

If Client Pairing is absent, the POS status control opens one guided flow. It
covers Device Center, certificate trust, request review, manager approval, and
final Device Preflight.

The status control displays current progress and permits cancellation. A
Pairing Request expires after ten minutes.

The POS opens Device Center through the registered `inari://` protocol. The
URI contains the Pairing Request identity and no secret.

An approved signed operating-system package installs Device Center and the
Agent service before Client Pairing. The package identity belongs to the active
Release Set.

If Device Center or the Agent service is absent, Odoo blocks Client Pairing and
displays the exact installation or repair action. Odoo does not host or execute
the installer.

The Release Set defines supported operating-system and architecture pairs,
minimum package versions, and package digests. Device Center reports signature,
installation, service-registration, startup, repair, and rollback results.

Installation and repair require the operating-system permission declared by the
signed package. A signature failure blocks pairing and reports one safe repair
action.

Device Center displays the exact Odoo origin, database, POS configuration,
requested scopes, and a three-word phrase. The POS displays the same phrase.

The Device Manager matches the phrase and approves with operating-system
authentication. The browser completes proof of possession for its paired key.

If protocol activation fails, the status control displays persistent
instructions to open Device Center manually.

A denial returns a stable reason without restricted diagnostics. Denial,
expiry, or cancellation requires a new Pairing Request.

The Agent accepts browser requests only from the exact origin in Client
Pairing. It rejects wildcard, missing, and `null` origins. The Local Agent Interface
uses no cookies.

Device Center uses a separate application identity. It does not use a browser
origin exception.

The Agent issues 15-minute access tokens and renews them through signed
challenges. A change to a Device Binding or scope requires new approval.

Each access token contains the thumbprint of the paired public key. Every
browser request uses RFC 9449 DPoP with that key.

The Agent enforces a DPoP nonce, a two-minute clock window, and a `jti` replay
cache. A copied bearer token cannot authorize Device Work without its paired
private key.

The access token contains `cnf.jkt`. Each DPoP proof contains exact `htm`,
normalized `htu`, `iat`, `ath`, nonce, and `jti` values. The Agent rejects a
missing or mismatched claim.

The proof header contains `typ=dpop+jwt`, `alg=Ed25519`, and the public Ed25519
JWK without private members. The normalized `htu` excludes query and fragment
components. Requests present the access token with the `DPoP` authorization
scheme.

The durable replay cache covers the access-token lifetime and clock window. An
Agent restart cannot make an accepted proof valid again.

After a clock-related DPoP rejection, the browser derives a clock offset from
the Agent `Date` value and retries once with the new nonce.

A second rejection displays a workstation clock-correction error. The Agent
does not widen its two-minute window.

An enrolled Agent permits new Client Pairing for 24 hours after its last
Controller policy sync. Existing Client Grants can renew offline for seven
days. The Agent requires Controller access after either limit.

An accepted Print Job continues after an offline trust limit expires. The
limit affects new Client Pairing and Client Grant renewal only.

The local HTTPS endpoint uses a separate local-server certificate from the
Inari certificate authority. The certificate has a 30-day lifetime and renews
at two-thirds of its lifetime.

Device Center trusts only the Organization certificate authority. Controller
revocation invalidates the endpoint and its Client Grants.

A Device Manager can approve Client Pairing during a Controller outage for 24
hours after the last policy sync. Odoo must remain reachable so it can issue
the Pairing Assertion. Operating-system authentication protects this action,
and the audit record synchronizes later.

An Odoo outage blocks new Client Pairing. Existing Client Grants continue
within their offline trust limit.

The Policy Snapshot includes the Odoo signing key. A stale or revoked key
cannot authorize a new Pairing Assertion.

New Agent enrollment always requires the Controller. Loss of the browser key
requires a new Client Pairing. The system does not export or restore browser
private keys.

The Agent revokes a Client Grant after 30 days without use.

A workstation Agent uses the fixed loopback Agent Endpoint. A shared Agent
publishes a signed mDNS record that Device Center checks against the
Organization certificate authority.

Managed DNS provides the shared Agent Endpoint when the network blocks mDNS.
The addon does not accept arbitrary URLs or raw IP address configuration.

The browser selects the exact `agent_id` from the Device Binding. The
discovered Agent must match the Organization and Site in that binding.

A loopback Agent must present the same identity. The browser never selects the
nearest or first discovered Agent.

The production Odoo origin uses a strict content security policy and Trusted
Types. It loads no third-party scripts into the paired origin.

## Tenant model

One Odoo company maps to one Inari Organization. Each store or warehouse maps
to one Site. Each Agent belongs to one Site.

Every Controller lookup for an Agent, Device, Print Job, or Managed Work includes
Organization scope. A Device Binding cannot cross an Organization boundary.

Printers can be Shared Devices within one Site. Scales, cash drawers, and
scanner streams are Exclusive Devices unless their Driver declares safe
sharing.

Every device purpose requires an explicit Device Binding. The addon does not
select the first available Device or reroute a Print Intent automatically.

Each Device has one bounded FIFO queue. The Agent rejects a submission before
`Accepted` when that queue is full.

Local Device Work and Managed Device Work enter the same queue after
`Accepted`. The first release has no source priority.

The source remains content-free audit data. The Agent expires stale work before
the first Device I/O.

Odoo stores read-only Projections of Organizations, Sites, Agents, Devices,
and capabilities. Odoo owns Device Bindings, Device Test Results, and Print
Audit Records.

Inari owns live device state, Print Jobs, events, credentials, and Receipt
Payloads. Odoo does not store authoritative Print Job state.

The addon defines these persistent models:

- `inari.organization`
- `inari.site`
- `inari.agent`
- `inari.device`
- `inari.device.capability`
- `inari.device.binding`
- `inari.device.binding.revision`
- `inari.report.binding`
- `inari.report.sequence`
- `inari.report.sequence.document`
- `inari.device.test.result`
- `inari.managed.work`
- `inari.print.audit`.

The first five models and `inari.managed.work` are Projections. Odoo users
cannot modify their Inari fields.

The sync identity can create, update, and archive Projections. Odoo owns Device
Bindings, Report Bindings, Device Test Results, and Print Audit Records.

Each Projection stores its Controller UUID as an immutable external key. The
key is unique within its Organization scope.

The sync archives a record that disappears from the Controller. It does not
delete a record while a Device Binding or Print Audit Record refers to it.

A Projection cannot move between Odoo companies. A changed Organization
assignment is a sync error that requires administrator action.

A logical Device Binding stores:

- `company_id`
- `site_id`
- `scope_type`
- `purpose`
- optional `pos_config_id` for a POS scope
- optional `pos_printer_id` for preparation printing
- `active_revision_id`.

A separate immutable Binding Revision stores the assigned Device, Device
Capability, Driver Profile digest, normalized options, authorization digest,
and revision number. The logical Device Binding owns `active_revision_id`.
Latest-observed and latest-passed Device Test references are derived Projections
over immutable Device Test Results.

The `scope_type` value is `pos_config` or `site`. Exactly one scope shape is
valid for each Device Binding.

The first release uses these Device Purpose values:

- `pos_receipt`
- `pos_preparation`
- `pos_cash_drawer`
- `pos_scale`
- `pos_scanner`
- `report_pdf`
- `stock_label`.

The `pos_receipt`, `pos_preparation`, `pos_cash_drawer`, `pos_scale`, and
`pos_scanner` purposes require `pos_config` scope. The `report_pdf` and
`stock_label` purposes require `site` scope. Only `pos_preparation` permits and
requires `pos_printer_id`. That printer belongs to the selected POS
configuration.

`stock_label` is the Device Purpose for routing. `label_document` is the
public Device Work operation for one checked label envelope.

One POS configuration has one active binding for each Device Purpose except
`pos_preparation`. Each preparation printer has one active
`pos_preparation` binding.

A Site can have multiple active `report_pdf` or `stock_label` Device Bindings.
Each Report Binding selects one exact Binding Revision.

A Binding Revision is immutable. A Device Manager creates one only when the
assignment authorization digest changes. The digest binds Device identity,
Device Capability, Driver Profile, certification, tested-capability contract,
normalized options, purpose, and scope.

The manager runs the physical Device Test outside the Binding lock. A short
activation transaction locks the logical Device Binding, verifies that the
tested authorization digest is still current, and sets `active_revision_id`.
The prior revision remains immutable in history.

Each Device Test creates an immutable Device Test Result. The result stores:

- the Binding Revision and Driver Profile digest
- the Device Test pattern version
- each check and its evidence
- the actor, start time, and end time
- the Agent-signed result.

The Binding Revision points separately to its latest observed and latest passed
Device Test Results. Odoo retains all results while that revision is active.

After deactivation, Odoo retains each Device Test Result for 90 days.

Printer, drawer, and scanner test results have no calendar expiry. A change to
the Device identity, Binding Revision, Driver Profile digest, or tested
capability invalidates the result.

A scale test result stops authorizing price calculations when its
Certification Record expires.

A Device Test records `failed_environment` when surrounding conditions prevent
a valid result. It records `failed_contract` when the tested Device Capability
does not meet its Driver Profile or Device Purpose.

An inactive Binding Revision cannot activate after either failure. An
environmental failure on an active revision updates Device Health, keeps the
latest passed result, and resumes use when readiness returns. A contract
failure on an active revision also marks the revision `Needs attention` and
blocks that Device Purpose.

Activation deactivates the prior revision. Existing Device Work and audit
records keep their prior Binding Revision.

Activation locks the target Site, `pos.config`, or `pos.printer` record. A
partial unique index enforces each POS-scoped assignment.

For a Site scope, a partial unique index prevents duplicate active assignments
of one Device, Device Capability, and Device Purpose.

The same transaction deactivates the prior revision and activates the tested
revision.

A Retry keeps its original Binding Revision, Device, Agent, deadline, and
Payload Fingerprint. An unavailable original target makes that Retry fail.

An operator creates a Reprint to use the current active Binding Revision. The
operator interface identifies the changed Device before submission.

A new active Binding Revision reaches a POS after its next reload. An open POS
displays a nonblocking configuration-change banner.

The active POS does not change a Device target during an order.

The Device, Device Capability, Site, and POS configuration must belong to the
same company and Organization. The record stores no Agent Endpoint or
credential.

Odoo updates the Projections from the Controller every five minutes. The sync
uses an incremental cursor or ETag and also supports a Device Manager refresh.

The POS reads fresh operational state directly from the Agent. Odoo does not
proxy live local state through the Controller.

Device Operators use controlled POS actions and cannot write integration
models directly. Device Managers manage Device Bindings and Device Tests only
within their permitted companies and Sites.

A Device Operator receives only the active Binding Revisions and Print Audit
Records for the current POS session. The role cannot search fleet
Projections.

A Device Manager can read Projections, Binding Revisions, Device Test results,
and Print Audit Records for permitted Sites. A System Administrator can read
all integration records in allowed companies.

The sync identity writes Projections only. System Administrators manage the
workload identity and system policy.

The sync identity has no interactive login and no POS access.

Every integration model has a required `company_id`, company consistency
rules, and Odoo record rules. Cross-company reads and writes fail closed.

Each Driver publishes a versioned Device Capability contract through the
Agent. The contract contains:

- supported operations
- accepted media types
- the schema for normalized device options
- available Output Evidence
- sharing mode
- payload and queue limits
- Certification Record references
- Driver version
- compatible Contract Majors.

The Controller provides the Device Capability Projection for backend use. The
POS reads the live contract from the bound Agent during Device Preflight.

The addon does not infer a Device Capability from a device name, model name,
or transport type.

Device Preflight sends the required operation, Contract Major, media type, and
normalized device options. The Agent returns each unmet requirement
explicitly.

Capability Negotiation ignores unknown additive fields. It does not infer a
missing capability or downgrade a requirement.

The Driver contract assigns the maximum Output Evidence for each operation.
Odoo can display weaker wording and cannot upgrade that evidence.

If a Driver update removes a required Device Capability, the active Binding
Revision enters `Needs attention`. Device Preflight blocks only its Device
Purpose.

A Device Manager must create and test a new Binding Revision. The addon does
not downgrade the requirement or select another Device.

Additive fields and new operations do not invalidate an existing Device Test.
These changes invalidate it:

- a reduced limit
- weaker Output Evidence
- changed meaning for a normalized option
- a removed required operation
- a changed Certification Record.

The Binding Revision stores the tested capability digest. Device Preflight
compares the compatible requirement subset instead of unrelated additive
fields.

Each Driver Profile has a stable identity, version, SHA-256 digest, minimum
Agent version, capability contract, and certification facts.

The Controller signs each canonical Driver Profile with Ed25519. The Agent
checks its signature, `kid`, digest, Contract Major, and minimum Agent version.

New Driver Profile activation requires a Controller policy sync within 24
hours. An active checked profile remains usable offline for seven days.

A Binding Revision pins one immutable Driver Profile digest. A new digest
requires a new Device Test before activation.

The Controller can revoke a Driver Profile. Revocation blocks its Device Work
and marks each affected Binding Revision `Needs attention`.

Revocation fails queued work with public code `capability_changed` before
Device I/O. Restricted diagnostics record `profile_revoked`.

For active work, the Agent asks the Driver to stop.

Without proof that output did not occur, the active result is
`outcome_unknown`. Completed records remain unchanged.

## Delivery sequence

### First production gate

The first physical canary covers:

- customer receipts, including automatic, manual, basic, and reprint flows
- preparation printers with product-category routing
- cash-move receipts
- cash drawers attached to receipt printers
- `Accepted`, `Output Confirmed`, failure, and `Outcome Unknown` states
- Retry, browser print, and continue-without-ticket recovery actions
- local operation during an Odoo or Controller outage
- idempotent submission and duplicate suppression.

The first platform gate covers Windows 11 and Linux. Each immutable Hardware
Certification Matrix Row records the exact Device, firmware, connection,
media, Driver, Platform Backend, and operating-system combination.

The row records its identity, version, status, Release Set, signed test
evidence, effective date, and revocation date. Device-kind rules use
`not_applicable` for unused dimensions.

The release publishes no physical Driver Profile before that exact combination
passes the laboratory gate. Generic protocol-family tests remain emulated and
do not claim physical support.

A discovered Device without an active exact Matrix Row cannot create or
activate a physical Driver Profile. It returns `certification_required` with a
Device Manager qualification action and performs no Device I/O.

Firmware, connection, media, or host drift returns `capability_changed` and
marks the Binding Revision `Needs attention`.

The laboratory inventory is a release input. It includes Windows spooler,
CUPS, USB ESC/POS, and network ESC/POS combinations where certified hardware
exists.

macOS enters the production matrix after it passes the shared contract suite.

### Next device layer

The next layer adds scales and proxied scanners. Managed reports, office
printers, and label workflows follow the local device layer.

Only a Certified Scale can affect an Odoo price calculation. Its Device Binding
records the certification identity and operating jurisdiction.

Manual Weight is disabled by default. A company can enable it with a dedicated
permission. Each use creates a content-free audit record and never claims a
Certified Scale reading.

Direct keyboard scanners remain Native Device Paths. Inari handles scanners
that connect to an Agent Host. One POS configuration has one active Barcode
Source.

Each Barcode Event contains `device_id`, sequence, timestamp, symbology, and
value. It also contains Agent Boot Identity, Subscription Identity, Client
Grant, and Binding Revision.

The Device Adapter removes duplicates by Agent Boot Identity, `device_id`, and
sequence. Logs and metrics never contain barcode values.

The Agent replays ordered Barcode Events only while the authenticated scanner
subscription and Transport Leader Lease remain active. Lease expiry stops the
replay and deletes unacknowledged events.

After subscription loss, the POS opens product search and displays the scanner
recovery action. It never applies a Barcode Event from a stale fencing
generation.

A Scale Reading contains:

- `device_id`
- `agent_boot_id`
- `subscription_id`
- `client_grant_id`
- `binding_revision_id`
- `sequence`
- `observed_at`
- Agent monotonic observation time
- `value_mantissa`
- `decimal_exponent`
- UCUM `unit`
- `resolution_mantissa`
- `stable`
- `range_state`
- `certification_id`
- Agent signature.

The `range_state` value is `valid`, `underload`, or `overload`. The resolution
uses the same decimal exponent as the value.

For example, `12345` with exponent `-3` and unit `kg` means `12.345 kg`.
The signed wire contract represents `value_mantissa` and
`resolution_mantissa` as canonical decimal strings. The values stay within the
signed 64-bit range. The contract contains no binary floating-point weight and
does not lose precision in a JavaScript browser.

The Agent supplies gross Scale Readings. The Odoo POS keeps its current tare
behavior and stores the selected gross reading as tare.

The Device Adapter converts the exact decimal value only at the Odoo scale
seam. The first release sends no hardware-tare Device Work.

Opening the Odoo scale screen starts one authenticated Agent subscription. The
Agent sends at most two Scale Readings each second.

Closing the scale screen stops the subscription. Each Scale Reading is
ephemeral and does not enter the durable Device queue.

A reading gap longer than one second marks the scale stale and disables weight
acceptance. Lease loss, stale data, or a Binding Revision change invalidates the
current reading immediately.

Two new stable readings must differ by no more than one resolution before Odoo
enables acceptance. Recovery offers Retry, permitted Manual Weight, or product
removal.

Odoo keeps its one-live-tab rule for each POS session. The Inari addon does not
replace that behavior.

The Agent owns the Scale Lease across separate POS sessions and auxiliary
browser contexts. A browser page uses a random in-memory tab identity.

The Transport Leader owns one Agent scale subscription. It sends notification
messages through a dedicated, scoped `BroadcastChannel`.

The channel contains no Odoo access token and does not enforce exclusivity. Its
messages are hints until the browser checks the Agent signature.

A second valid scale screen displays `Scale in use on another register`. It
offers `Try again` after the owner lease expires.

The first release permits no lease stealing.

Closing the owner releases the lease. A missing owner heartbeat releases it
after three seconds.

A Scale Reading can affect an order only when all these conditions are true:

- it comes from the active Binding Revision
- its Agent Boot Identity and Subscription Identity match the active stream
- its Client Grant authorizes the current POS session
- its Agent signature is valid
- its Certification Record is current
- its Certification Record and Binding Revision reference one active exact
  Hardware Certification Matrix Row
- `stable` is true
- `range_state` is `valid`
- its sequence is newer than the prior reading
- its age is one second or less
- its UCUM unit is compatible with the product unit
- its net value is positive and within the certified range.

The POS also requires the physical weight to change after the previous product
addition. Exact decimal arithmetic applies before the final Odoo seam.

Provider-specific payment terminals, cash machines, and direct Epson paths
remain Native Device Paths until a separate Device Adapter passes its contract
suite.

## Print lifecycle

One Print Intent represents one requested physical copy. A Retry keeps the same
Print Intent and resolves to the same Print Job. A Reprint creates a new Print
Intent.

Every Print Intent contains one discriminated Print Origin.

A POS Print Origin contains the Odoo database, POS configuration, POS session,
offline order UUID, optional server order identity, document kind, and content
revision.

A preparation origin also contains segment kind, segment index, and preparation
revision. Copy Ordinal remains separate from the Print Origin.

A report Print Origin contains the Odoo database, company, Site, Report Binding,
Report Route, report action, and source model. It also contains ordered record
identifiers or a controlled wizard-input digest and rendered-document index.

The Print Intent identifier contains its format version, canonical Print
Origin, immutable Binding Revision, `device_id`, and copy ordinal. Odoo
allocates the copy ordinal in the same transaction that creates the Print
Intent.

Before Copy Ordinal allocation, an Origin Submission Key binds the Print
Origin, Binding Revision, Device, and initial or approved-Reprint allocation.
The Agent atomically maps one key to one Print Intent. A concurrent first
submission returns that existing mapping. A Reprint Approval creates a new key.

The identifier does not include a retry count or request time.

The Odoo printer Device Adapter reports success when the Agent reaches
`Accepted`. Odoo increments its receipt counter at this state. Inari tracks
physical output without blocking checkout.

The public state remains `accepted` through admission, preparation, queueing,
and every step before the first Device I/O marker. The Agent publishes
`in_progress` only when it commits that marker.

The public Print Job Interface exposes:

- `accepted`
- `in_progress`
- `output_confirmed`
- `failed`
- `outcome_unknown`
- `expired`
- `canceled`.

The Print Job representation contains:

- `job_id`, `intent_id`, and `device_id`
- the discriminated Print Origin and optional `managed_work_id`
- `state` and `state_version`
- `accepted_at`, `started_at`, `terminal_at`, and `expires_at`
- `retryable`, `error_code`, and `message_key`
- `confirmation_evidence`
- `contract_version`.

The representation excludes Device Work, Receipt Payloads, and raw Driver
messages.

The `confirmation_evidence` value is `device`, `spooler`, or `transport`.
Odoo displays `Printed`, `Sent to print queue`, or `Sent to printer` for these
levels.

An `output_confirmed` state means that the Driver met its declared completion
contract. The Output Evidence keeps the operator text precise about physical
certainty.

The Print Job Module keeps `queued`, `dispatched`, `running`,
`retry_scheduled`, and other scheduler states inside its implementation.

The public states use these transitions:

```text
accepted -> in_progress -> output_confirmed
accepted -> failed | expired | canceled
in_progress -> output_confirmed | failed | outcome_unknown
outcome_unknown -> output_confirmed | failed
```

The last transition applies only during the 24-hour active reconciliation
period. Later evidence changes the Projection annotation, not the terminal
Print Job state.

Cancellation produces `canceled` only before the first Device I/O marker.
During `in_progress`, the Agent asks the Driver to stop and reports
`outcome_unknown` unless the Driver proves that no output occurred.

A Driver proof of no output permits the normal `failed` state. The Agent never
reports `canceled` after the first Device I/O marker.

Active Job Reconciliation continues for an `outcome_unknown` Print Job for 24
hours. The Agent then stops active polling and deletes its Receipt Payload.

The Print Job remains terminally `outcome_unknown` after this window. Its alert
remains open until a Device Manager records a reasoned review.

The system accepts correlated signed evidence for 90 days. This evidence adds
`Later confirmed` or `Later failed` to the current projection. The history
keeps the unknown observation and every later Reprint.

A failure before `Accepted` opens the operator recovery flow. Browser printing
at this point records a manual recovery action on the same Print Intent.

After `Accepted`, browser printing creates a Reprint with a recovery reason. An
`Outcome Unknown` state requires reconciliation, a duplicate-risk warning, and
Device Manager approval.

Opening a browser print dialog does not claim physical output.

Payment remains complete when printing fails. Printing does not reverse or
invalidate a completed payment.

An automatic report or label failure does not reverse a completed stock
operation. The failure remains Device Work recovery.

A preparation Print Job becomes `Expired` when execution does not start within
60 seconds. A customer receipt becomes `Expired` after five minutes.

For Local Device Work, the Agent selects the policy TTL at `Accepted`. It uses
a monotonic clock for the execution gate and publishes UTC timestamps.

For Managed Device Work, the Controller assigns the deadline. The Agent checks
and stores the exact value at `Accepted`.

A client cannot extend the TTL. A Retry uses the original `expires_at` value.

The Agent never starts an `Expired` Print Job. An operator must create an
explicit Reprint.

The Agent can record `expired` only before the first Device I/O marker. A
deadline or timeout after the marker follows the normal evidence rule and
becomes `outcome_unknown` without stronger proof.

The Agent is the authority for Print Job state. POS IndexedDB stores recent
Print Intent identifiers and cached states during an Odoo outage.

IndexedDB stores only content-free state and unsynced Print Audit Records. It
never stores Receipt Payloads.

The browser deletes synced integration data after 24 hours. A seven-day hard
limit applies to all integration data in IndexedDB.

Agent events provide fast updates. The addon uses the job interface for Job
Reconciliation because event delivery is not durable.

Each Agent event stream uses a persistent, increasing sequence. Each changed
resource also has a version. The browser stores the last sequence for each
Agent.

A sequence gap, resource-version reversal, reconnection, or Transport Leader
change starts Job Reconciliation. An event never starts an irreversible Odoo
business action.

After reconnection, the POS sends content-free Print Audit Records to Odoo.

Each offline audit update has a unique `audit_event_id`. Odoo uses this value
for an idempotent upsert and returns the existing record after a replay.

The authenticated Odoo session supplies actor evidence. An Agent State
Envelope supplies execution evidence.

The envelope contains Organization, Site, Agent, Print Intent, Print Job,
Print Origin, optional Managed Work, Device, state, state version, timestamps,
Output Evidence, and Payload Fingerprint.

It also contains Dispatch Epoch, Agent Boot Identity, monotonic envelope
sequence, durable local-state sequence, observation time, and reconciliation
session identity.

Odoo checks the Agent signature through its Agent Projection. It rejects an
invalid signature or a company, Organization, Site, Print Intent, or Print Job
mismatch.

The Controller retains Agent JWK and certificate history for 90 days. Odoo
checks the certificate status at the envelope `issued_at` time.

The history records key-compromise times for later security review. Certificate
renewal does not invalidate an earlier valid envelope.

Each Agent State Envelope contains a unique `envelope_id`. Odoo binds it to one
`audit_event_id` and one Print Intent.

Odoo rejects an `envelope_id` that another audit event or company already
uses.

Every Print Intent submission requires `Idempotency-Key`. An identical request
returns the existing Print Job. A changed Payload Fingerprint returns `409`.

The addon encodes stable Print Intent fields with canonical length prefixes,
hashes the encoding with SHA-256, and writes the opaque key as
`pi_v1_<base32-digest>`. The key does not contain readable business data.

The Payload Fingerprint hashes this normalized Device Work envelope:

- Contract Major
- operation
- `device_id`
- media type
- exact payload bytes
- normalized device options
- resolved `expires_at`.

Credentials, transport headers, and request timestamps do not enter the
Payload Fingerprint. A Retry uses the stored `expires_at` before comparison.

For Managed Device Work, the Controller loads the original stored deadline
before it calculates the replay fingerprint. A later policy change cannot turn
an exact Retry into a conflict.

For Local Device Work, the Agent keeps the content-free idempotency record for
90 days from `Accepted`. For Managed Device Work, the Controller keeps the pair
for 90 days from Controller Admission. The Agent keeps its linked record until
the Controller-provided `idempotency_expires_at`. A Reprint uses a new
Idempotency Key.

The Agent also keeps content-free Print Job metadata for 90 days. A daily
bounded cleanup task deletes older metadata without delaying active Device
Work.

Odoo `BasePrinter` remains a transient serialization queue. It does not own
durable retries or create a new Print Intent after Agent acceptance.

IndexedDB coordinates Transport Leader candidates. BroadcastChannel carries
content-free hints only. Neither browser mechanism grants stream ownership.

The Agent issues a 10-second Transport Leader Lease for one exact Client
Pairing, Agent, POS configuration, and Authorization Digest. The lease contains
the holder identity and a monotonically increasing fencing generation. The
holder renews the lease every three seconds. A suspended tab loses the lease.

Each tab uses its own Client Grant and DPoP key for actions. A successor obtains
a new lease and completes sequence reconciliation before it processes Agent
input. Print idempotency remains the final duplicate barrier.

The Transport Leader Lease controls SSE ownership. The separate Scale Lease
controls Exclusive Device use. Scale-Lease heartbeat expiry does not shorten
the 10-second Transport Leader Lease.

The Transport Leader opens one fetch-based SSE stream with DPoP. Each stream
message carries the fencing generation. The Agent rejects a stale holder and
sends a final signed `lease_lost` reason before teardown when transport permits.

The Odoo browser transport treats IndexedDB and BroadcastChannel as advisory.
It still asks the Agent for the authoritative Transport Leader Lease when
either browser mechanism is unavailable. A BroadcastChannel hint contains only
the contract version, hint kind, and a random nonce.

The stream sends heartbeats and carries no access token in its URL.

The Agent sends a heartbeat every 15 seconds. The Transport Leader reconnects
after 30 seconds without stream data.

Reconnect delay starts at 250 ms and increases to 10 seconds with full jitter.
After access-token renewal, the Transport Leader reconnects with the new token.

The first event is `ready`. It contains the stream identity, current sequence,
and an immutable reconciliation high-water mark.

The browser verifies the lease key against the paired Agent identity. It then
verifies every compact JWS before it sends data to a Device Adapter. It
completes the `ready` reconciliation barrier before it reads later events. It
acknowledges a Barcode Event only after the scanner handler accepts it.

Scale activity changes rebuild the selected Event Lease. This keeps a scanner
stream active while the scale screen is closed and adds the Scale Lease only
while the scale screen is open.

The Local Agent Interface uses these fixed DPoP-protected endpoints:

- `POST /v1/events/lease` acquires the Transport Leader Lease.
- `POST /v1/events/lease/renew` renews the Transport Leader Lease.
- `DELETE /v1/events/lease` releases the Transport Leader Lease.
- `POST /v1/events/scale-lease` acquires the separate three-second Scale Lease.
- `POST /v1/events/scale-lease/renew` renews the Scale Lease.
- `DELETE /v1/events/scale-lease` releases the Scale Lease.
- `GET /v1/events` opens the fetch-based SSE stream.
- `POST /v1/events/ack` acknowledges accepted Barcode Events.

The SSE request puts the lease, Subscription Identity, and fencing generation
in bounded request headers. It does not put credentials, scope, or Device
identities in the URL. The Agent derives business scope from the Client Grant.

Each signed message uses an Ed25519 compact JWS with the protected type
`inari-agent-event+jws`. The signature covers the canonical message, Agent Boot
Identity, Subscription Identity, Client Grant, Binding Revision, scope digest,
and fencing generation. The first `ready` message also returns the Agent public
JWK. The browser verifies that key against the paired Agent identity before it
accepts the message.

The stream uses one sequence for each Subscription Identity and one durable
Agent sequence for its reconciliation barrier. Scale Readings are ephemeral.
The Agent keeps at most 128 unacknowledged Barcode Events in memory for the
active lease. It never writes barcode values to SQLite, logs, metrics, runtime
events, or the gateway. Buffer exhaustion sends signed
`replay_unavailable` evidence and closes the stream. It never drops an older
Barcode Event to admit a newer value.

The older unscoped `/events` WebSocket is not part of the Local Agent
Interface. The Agent does not expose it.

A successor reconciles through that high-water mark before it subscribes to
later events. A sequence gap starts the same barrier flow. This contract
prevents loss between the snapshot and subscription.

Other browser contexts receive hints through the browser channel. They
reconcile authoritative state before they apply a hint.

Device Operators can Retry, use browser print, continue without a ticket, and
Reprint an `Output Confirmed` customer receipt with a reason.

Only a POS Print Origin increments the existing Odoo receipt counter for each
accepted Print Intent. Inari views label this value `Copies requested`. A
report Print Origin updates report audit and summary records without changing
the Odoo receipt counter.

The Print Audit state provides confirmed-output and duplicate-risk information.
The count does not claim physical output.

Print Recovery Reasons use these codes:

- `customer_request`
- `print_quality`
- `device_error`
- `outcome_unknown_override`
- `preparation_recovery`
- `other`.

The `other` code requires a short operator note. The note cannot contain
customer or payment data.

A Reprint Approval binds one Device Manager, Print Intent, Device, Binding
Revision, reason, and new Copy Ordinal. An authorized manager can approve it
inline.

Without an inline manager, Odoo creates one manager activity that expires after
15 minutes. The approval permits one Reprint only. Post-hoc approval and
standing approval are not valid.

The Agent accepts no generic managed drawer Device Work. A local Drawer Intent
contains the POS session, actor, Device Binding, action sequence, and an
allowed payment or manual-open reason.

The Agent stores each Drawer Intent and its idempotency record for 90 days. A
timeout after Device I/O produces `outcome_unknown`.

The addon never retries an uncertain Drawer Intent silently. The payment stays
complete. A Device Manager must approve a new Drawer Intent for another
physical opening.

## Identity and data

The Odoo backend uses an Organization Workload Identity for Managed Device
Work. Each Odoo company has one identity for its Inari Organization.

The identity receives short-lived OIDC access tokens. OpenBao stores and
rotates its client credential.

Each request contains database and company claims. The default grant contains
fleet reads and Managed Work reads or writes within one Organization.

The grant permits `report_pdf` and `label_document` only through an active
Report Binding and compatible Binding Revision.

The default grant excludes enrollment, administration, raw Device Work, and
direct Zenoh access.

The Controller owns each Certification Record. Its key contains the device
model, Driver, jurisdiction, certificate identity, and expiry date.

Odoo reads Certification Records through Projections. The operator interface
warns 30 days before expiry and blocks price calculation at expiry.

The addon defines three roles:

- a Device Operator uses assigned Devices and permitted recovery actions
- a Device Manager pairs, binds, diagnoses, and does tests of Devices
- a System Administrator manages workload identity and integration policy.

Local Client Grants follow the same role policy. Raw Device Work stays disabled
for Device Operators and Device Managers.

The Agent stores content-bearing Device Work in an encrypted Device Spool. A
job row contains only a manifest, fingerprint, reservation, and encrypted
artifact references.

The Device queue accepts at most 32 jobs or 64 MiB of original payloads. The
Agent queue accepts at most 256 jobs. Queue count and original-byte limits are
independent from Device Spool capacity.

The Device Spool accepts at most 128 MiB for one Device and 512 MiB for one
Agent. Both limits count committed artifacts and outstanding Spool
Reservations.

One active operation reserves at most 128 MiB of temporary space. All active
operations share a separate 512 MiB temporary-work limit. Driver RSS,
Document Imaging worker RSS, Agent temporary disk, and operating-system spool
use have separate budgets.

A production Agent Host provides at least a 4 GiB volume for SQLite and the
Device Spool. The Agent measures the filesystem reserve on that exact volume
and keeps the greater of 1 GiB or 10% of its capacity free.

A Receipt Image accepts 2 MiB of input and can reserve 16 MiB for its derived
raster. A Report PDF accepts 10 MiB and persists no derived raster.

A Label Document accepts 2 MiB and produces no derived artifact. Every job
adds 64 KiB for encrypted-file and manifest overhead.

Before `Accepted`, one `BEGIN IMMEDIATE` transaction reserves the queue slot,
original bytes, and persistent footprint. The persistent footprint includes
decoded content, every persisted derived raster, and 64 KiB.

Before worker start, another `BEGIN IMMEDIATE` transaction acquires the
operation-specific Temporary Work Reservation. The job waits or expires when
this separate budget is unavailable. The Agent never counts one reservation in
both budgets.

Temporary work uses an explicitly configured volume. Before reservation, the
Agent checks committed files, both reservation classes, and that volume's
free-space floor.

SQLite uses `PRAGMA synchronous=FULL`. The Agent streams encrypted content to a
temporary artifact, syncs the file, renames it, and syncs the containing
directory.

Only then does one FULL-durability SQLite transaction commit the manifest,
Spool Reservation conversion, Print Job, and `Accepted` state.

A quota failure causes no eviction and no automatic reroute. Startup
reconciliation deletes partial and orphan artifacts. It checks reservations,
manifests, and referenced artifacts before content-bearing work starts.

Every encryption, sync, rename, worker-start, and transaction failure releases
its owned reservation in an idempotent transaction. Each live reservation has
an owner and deadline. Startup removes reservations whose owner is invalid.

A missing referenced artifact fails only its affected pre-I/O Print Job and
raises an alert. It does not block unrelated Device queues.

A production Agent Host requires a TPM 2.0 Rollback Anchor. The TPM stores a
monotonic Agent-start generation. SQLite stores the matching generation in its
durable root record.

Startup compares both values, advances the TPM generation, and commits the new
value before readiness. A missing TPM, access failure, or mismatch blocks
production readiness and triggers Agent Quarantine.

Enrollment provisions one dedicated TPM NV index and binds its public evidence
to the Agent identity. A crash after TPM advance and before SQLite commit also
triggers quarantine. Device Center records the evidence and requires fresh
enrollment. It never rewinds the generation.

Development mode can use an explicit non-production software anchor. A restored
Agent database or Device Spool triggers fresh enrollment. The Agent never
continues with the restored identity.

The Agent deletes successful content after terminal-state commit. It keeps
failed and Outcome Unknown content for 24 hours, then deletes it.

The system keeps Print Audit Records for 90 days. These records exclude receipt
content, credentials, and customer data that is not required for operations.

A Print Audit Record contains actor, Organization, Site, Print Origin,
Submission Context when present, Managed Work when present, Print Intent, Print
Job when present, Device, action, state, and reason.

It also contains the Payload Fingerprint, size, and contract version.

Odoo is the authority for business audit records. The Agent owns local Print
Job history. The Controller owns security, enrollment, and Managed Device Work
audit records. Shared identifiers correlate these records.

## Contract errors

The Device Adapter Interface uses these stable error codes:

- `trust_required`
- `permission_denied`
- `binding_required`
- `contract_mismatch`
- `capability_changed`
- `device_unavailable`
- `paper_out`
- `cover_open`
- `queue_full`
- `spool_quota_exceeded`
- `spool_storage_low`
- `payload_invalid`
- `label_data_invalid`
- `document_policy_rejected`
- `document_processing_failed`
- `certification_required`
- `expired`
- `recovery_fence`
- `recovery_uncertain`
- `outcome_unknown`
- `service_unavailable`
- `internal_error`.

The Odoo addon maps each code to translated operator text and a permitted
action. Raw Driver text stays in restricted diagnostics.

One versioned operator-contract matrix defines each public state and error. An
entry contains `message_key`, English text, Spanish text, primary action,
secondary action, retry rule, required permission, and escalation rule.

The safe `internal_error` entry displays one correlation identity and one
contact action. It never displays restricted diagnostics.

Aggregate Device Health uses this precedence:

1. `Needs attention`
2. `Setup required`
3. `Offline`
4. `Working`
5. `Checking`
6. `Ready`.

The highest applicable state controls the label. The details view still lists
every lower-priority condition.

Restricted causes include `profile_revoked`, `resource_denied`,
`spool_corrupt`, `spool_key_unavailable`, `secret_store_unavailable`,
`renderer_failed`, and `expired_recovery`.

The public Interface maps each restricted cause to one safe code. Operator
responses contain no path, host, credential, payload, or raw Driver text.

## Source and release

The reusable addon source lives at `packages/odoo-inari/inari_devices` in the
Inari repository. The addon has an independent version and an explicit Inari
contract compatibility range.

Inari publishes an immutable addon artifact. `mze-infra` pins the artifact and
owns MZE deployment configuration, secret delivery, rollout, and rollback.

The first MZE Controller deployment uses the signed Inari Controller chart in
a separate `mze-infra` namespace. Local Device Work remains independent of
that cluster.

The current single-host MZE cluster is a staging environment for Managed
Device Work. Local POS printing can enter production after its physical gate.

Managed Device Work cannot enter production until MZE has three independent
failure domains. The minimum topology has three hosts, three K3s server nodes,
three instances in each production CloudNativePG cluster, three OpenBao
replicas, three Controller replicas, and three Zenoh routers.

Controller, Zenoh, OpenBao, and CloudNativePG Pod disruption budgets set
`minAvailable: 2`.

OpenBao uses a three-member Raft quorum. CloudNativePG uses three instances and
synchronous quorum replication across the three failure domains.

Hard anti-affinity separates hosts and storage failure domains. Controller and
Zenoh workloads use topology spread.

Controller recovery uses Cloud KMS in the `europe` multi-region and locked
`eur4` dual-region Cloud Storage. The gate tests loss of one host and one
storage domain.

The Controller keeps its multi-tenant architecture for a later dedicated
control plane.

The artifact is dedicated to the Inari Odoo addon and uses a content-addressed
OCI digest. Its metadata declares the supported Inari contract range.

Contract negotiation blocks Device Work when the addon and Agent are
incompatible.

An incompatible POS displays both contract versions and one administrator
action. Browser printing and Native Device Paths remain available.

Additive contract changes stay within one contract major and use capability
negotiation. A breaking change increases the major and uses a coordinated
canary deployment without compatibility paths.

A breaking deployment uses a POS maintenance window. The team coordinates the
Agent, addon artifact, database update, and POS assets. One Site runs the
canary before the fleet rollout.

The current hard-coded Odoo module update list must become declarative before
the addon enters production.

The deployment stores required addon names in one declarative list. The Odoo
initialization process queries each module state.

It passes installed modules to `--update` and absent modules to `--init`. An
initialization failure stops the rollout.

The deployment pins the MZE and Inari addon artifacts by OCI digest. Separate
init containers check each signature and unpack each artifact.

GitHub Actions signs each addon artifact with keyless Cosign. Each artifact
also contains its SBOM and build provenance.

Flux checks the OCI artifact against the exact GitHub Actions issuer,
repository, workflow, and source-ref identity. Odoo starts only with the
checked digest.

The accepted issuer is `https://token.actions.githubusercontent.com`. The
accepted certificate subjects are:

- `^https://github.com/hadronomy/inari/.github/workflows/release\.yaml@refs/heads/main$`
- `^https://github.com/hadronomy/mze-infra/.github/workflows/release\.yaml@refs/heads/main$`.

The signing policy rejects pull-request refs, tag refs, arbitrary workflows,
and reusable-workflow identities.

Each artifact has its own addon directory. Odoo lists both directories in
`addons_path` and never merges their files into one mutable directory.

One signed Release Set pins:

- the Agent artifact
- the signed Device Center package for each supported operating system
- the Controller chart
- the Inari addon artifact
- the MZE addon artifact
- the Odoo image
- the Contract Major
- the Odoo migration revision
- the POS asset build identifier.

It also pins Zenoh, qpdf, PDFium, CUPS filters and backends, and the ZPL parser.

Its signed manifest records every digest, signature identity, Contract Major,
migration revision, Odoo patch build, compatibility matrix, and test-evidence
identity. The protected MZE workflow signs it with the same keyless Cosign
identity policy as the MZE addon artifact.

Production Helm rendering fails when a required executable digest is absent.

`mze-infra` owns the environment-specific Release Set. The Inari release
workflow signs Inari artifacts before MZE promotion.

The protected MZE release workflow checks each source signature, combines the
fixed digests, and signs the Release Set. Promotion does not rebuild an
artifact.

The deployment consumes one Release Set. It blocks traffic when a digest,
signature, Contract Major, migration revision, or build identifier differs.

The Conformance Target supports only Odoo 19 patch builds that pass the complete
Release Set gate. An untested Odoo image digest is outside the supported target.

The addon exposes one authenticated `GET /inari/readiness/v1` route. Release
Readiness contains the Release Set identity, addon artifact identity, Odoo
patch build and image digest, Contract Major, migration revision, POS asset
build identity, required module states, and database identity.

The POS JavaScript bundle contains the same POS asset build identity. The
release gate checks the route and loads the POS bundle before it opens traffic.

A breaking release starts a POS Drain five minutes before maintenance. Odoo
sends the state through its bus, and each POS tab also polls it.

The POS stores an integrity-protected maintenance schedule in its local state.
An active tab sends a heartbeat every 15 seconds.

The release gate marks a tab unresponsive after two minutes without a
heartbeat. The acknowledgement proves receipt of the notice only.

An active payment can finish. The addon then blocks new payment validation and
keeps IndexedDB data intact. The deployment waits for each POS tab with a
heartbeat in the last two minutes to acknowledge the drain. It also waits for
zero paid unsynchronized orders.

An offline register enforces a received schedule from local state. A register
that never received the schedule does not count as acknowledged.

After reconnection, the register synchronizes paid orders before it permits
payment. The resume notice contains the active Release Set identity.

If the POS asset identity is stale, the register keeps payment blocked. It
displays `Update required before payments resume` and preserves IndexedDB.

A System Administrator can abort the release or override the drain with a
recorded reason. A POS Drain never closes an Odoo POS session.

Release Set activation uses this order:

1. The deployment announces the POS Drain.
2. It checks drain acknowledgements and paid unsynchronized orders.
3. It stops Odoo traffic and starts maintenance mode.
4. It checks the Release Set and backup gate.
5. It runs declarative module migrations.
6. It restamps addon files.
7. It deletes all `/web/assets/` attachments.
8. It starts the new Odoo Pod.
9. It checks Release Readiness.
10. It loads the POS bundle and checks its build identity.
11. It reopens traffic and clears the POS Drain.

Before traffic reopens, rollback restores the checked database and prior
Release Set.

After traffic reopens, database recovery uses a forward fix. An application
rollback is permitted only when the prior artifact declares compatibility with
the current schema and Contract Major.

## Operator experience

The POS runs Device Preflight when a session opens. One compact Devices control
in the POS header displays `Ready`, `Working`, `Setup required`,
`Needs attention`, `Offline`, or `Checking`.

The control opens one contextual panel from its trigger origin. A print error
stays beside the affected print action. The interface does not repeat status
toasts.

An unavailable printer does not block POS startup or sales. The affected print
action starts the recovery flow after payment.

An unavailable Certified Scale blocks only a weighed-product calculation.

The receipt screen displays the Print Job state. A dialog opens only when an
operator decision is required. Detailed diagnostics stay in the Odoo backend.

Queue saturation displays `Printer queue is full. Wait for the current tickets
to finish.` The addon does not accept or reroute that Device Work.

Low storage displays `Inari cannot accept this ticket because local storage is
low.` An oversized document displays `This document exceeds the printer
limit.`

Receipt recovery offers Retry, browser print, or continuation without a
ticket. Preparation recovery offers Retry before `Accepted` and a
manager-approved Reprint after a final state.

Before acceptance, the dialog displays `Ticket was not sent to the printer.`
It offers `Retry`, `Print ticket`, and `Finish without ticket`.

`Finish without ticket` creates an explicit Print Audit Record. It does not
hide the unresolved state without a record.

After acceptance, the receipt screen displays `Ticket sent to [Device name].`
Evidence changes this state to `Printed`, `Sent to print queue`, or `Sent to
printer`.

Outcome Unknown displays `Printing status is unknown. The printer can already
have printed this ticket.` It offers `Check status` and `Request reprint`.

One Odoo recovery dialog owns the flow. The addon does not open a second Inari
dialog for the same failure.

Drawer recovery offers Retry only before Device I/O. An uncertain opening keeps
payment complete and offers manager review for a new Drawer Intent.

Scale recovery offers Retry, permitted Manual Weight, or removal of the weighed
product. Scanner recovery opens product search and offers subscription Retry.
Neither flow changes its bound Device during the POS session.

No recovery action selects another Device automatically.

The interface does not use repeated connection notifications. State changes use
text, accessible status announcements, and color as a secondary signal.

A polite live region announces final print states and attention states. An
assertive alert occurs only after an operator action fails.

The interface does not announce Scale Readings, probe cycles, queue progress,
or repeated states.

Printing and keyboard operations have no entrance motion. Press and color
feedback uses 120 to 160 milliseconds.

The Devices panel opens from its trigger origin in 180 milliseconds. It closes
in 120 milliseconds. Content remains visible during both transitions.

Reduced-motion settings remove position motion. The interface uses no pulse,
bounce, or delayed content reveal.

The pairing entry action is `Connect this register`. The flow uses `Open
Device Center`, `Review access`, `Approve pairing`, and `Check devices`.

Operator text does not expose certificate, DPoP, nonce, or token terms. Pairing
errors state the failed step and one next action.

The POS Drain uses one persistent status region. It does not use a temporary
notification or an auto-dismissed dialog.

The scheduled state displays:

- `Maintenance starts at 14:00`
- `Finish the current payment. New payments stop when maintenance starts.`
- `Paid orders stay on this register until they sync.`
- `Acknowledge`.

The blocked state displays `Payments paused for maintenance`. It states that
the current order is safe and tells the operator not to reload.

The blocked state permits `Try sync now` and `View orders`. It disables new
payment lines, payment methods, validation, fast payment, and new Device Work.

In-flight states display `Synchronizing paid orders…` and `Update loading…`.
A failed load displays `Update did not load. Try again or contact your
administrator.`

The interface disables a submitted retry until the request finishes. Focus
returns to the same action after a failure.

The interface shows the absolute start time first. It updates relative time
once each minute. It has no second counter, flash, or pulse.

Scheduled, synchronization, and resume updates use a polite live region. The
payment block and serious synchronization failures use one assertive alert.

Disabled actions use native disabled state and visible reason text. Keyboard
focus stays predictable, and the barcode handler ignores an active drain
dialog.

The override dialog records the actor, time, reason, affected registers, and
paid unsynchronized order count. Only a System Administrator can submit it.

A stale POS displays `Your orders are safe. Keep this page open while the POS
update loads.` Payment remains blocked until the expected Release Set loads.

A recovery dialog puts initial focus on its heading. It gives no action an
implicit selection.

Escape returns to the receipt screen and keeps the unresolved state visible.
Escape does not record continuation. Closing restores focus to the action that
opened the dialog.

The first release supports Spanish and English. Operator controls have visible
focus, keyboard operation, 44 px touch targets, and complete translated
messages.

A Device Manager runs a Device Test for every bound Device Capability before
activation. The Agent uses a standard non-business payload for the test.

A printer test produces a standard receipt. It covers text, cut support,
barcode readability, QR-code readability, and declared Output Evidence.

A drawer test opens the drawer and asks the Device Manager to record the
physical result. A scale test uses stable zero and a certified reference
weight.

A scanner test reads one known code. It checks event ordering and duplicate
removal. The Agent offers tests only for capabilities that it supports.

Each observable check requires one controlled answer:

- `Correct`
- `Incorrect`
- `Test did not run`.

Agent evidence remains separate from the physical answer. A Device Test uses
no free-text result.

`Incorrect` produces `failed_contract`. `Test did not run` produces
`failed_environment`.

An environmental failure keeps an active Binding Revision. Its Device Purpose
stays blocked while Device Health is not `Ready`.

The same Device and Driver Profile resume automatically when Device Health
returns to `Ready`. The failed Device Test Result remains in history.

An identity, profile, binding, certification, or tested-capability change
requires a new Device Test.

The Binding Revision links its latest observed and latest passed Device Test
Results. The Device Test does not change Odoo receipt counters or store customer
data.

A Device Manager can export a Diagnostics Bundle for support. The bundle
contains contract versions, topology identifiers, certificate validity,
bindings, capabilities, recent state history, errors, and metrics.

The browser supplies content-free Odoo and Controller records to the Agent.
The Agent assembles the bundle and signs its manifest with the Agent identity.

The manifest identifies each source and includes a digest for every file.

The Agent treats browser-supplied records as untrusted data. It checks each
record against a size limit and source schema before bundle assembly.

Every exporter emits structured, allowlisted records. Each exporter declares
its schema, size limit, and excluded fields. The bundle contains no raw logs.

The manifest signature proves package integrity. Source labels state the trust
level of each included record. The manifest also records source counts,
omissions, and truncation.

An exporter failure produces a partial bundle when the Agent can sign the
manifest and include its core identity. The manifest records one stable error
for each omitted source.

Manifest-signing or core-identity failure stops the complete export.

The export is a ZIP with a signed manifest and structured data files. One
authorized download is available for each generated bundle.

The Agent form starts bundle generation and displays persistent progress. The
form lists each included data class before generation starts.

The actions use `Create diagnostics bundle`, `Preparing diagnostics…`, and
`Download bundle`. The form displays a support reference identity.

When the bundle is ready, the form displays one download action and its expiry.
The addon does not start a browser download automatically.

The expiry text is `Bundle expires after one download or 24 hours.`

The default Diagnostics Bundle range is 24 hours. A Device Manager can select
at most seven days.

The compressed size limit is 25 MiB. If records exceed the limit, the Agent
removes the oldest records and lists each omission in the signed manifest.

The Agent deletes the bundle after its first download or after 24 hours,
whichever occurs first.

The Diagnostics Bundle excludes Receipt Payloads, device values, credentials,
private keys, tokens, customer data, payment data, and barcode values.

## Performance and physical gate

The production performance targets are:

- local job acceptance within 300 ms at p95
- print start within one second when the Device is ready
- state update within one second after an Agent event
- Device Preflight within two seconds
- one scale reading every 500 ms.

The physical canary uses exact certified Windows and Linux combinations from
the Hardware Certification Matrix. It covers paper-out, cover-open,
disconnect, reconnect, cash drawer, cut, and lost-response cases.

Each Matrix Row gets one signed canary result with the Release Set, evidence,
pass criteria, actor, and approval. A failure blocks or revokes that row and
stops its rollout.

The integration publishes content-free metrics for:

- job acceptance, print start, state update, preflight, and scale latency
- Print Job outcomes and stable error codes
- queue job count and byte use
- Device Spool reservation, persistent use, temporary use, and free reserve
- Agent and Device reachability
- Client Grant renewal
- contract mismatch.

The metric label allowlist contains Organization, Site, device kind, Driver,
state, error code, and Contract Major. Labels exclude jobs, orders, users,
Device names, payload data, and barcode values.

The Odoo backend alerts a Device Manager for:

- contract mismatch
- certificate expiry
- sustained Agent or Device unreachability
- sustained queue saturation
- unresolved `outcome_unknown` states
- Client Grant renewal failure
- sustained performance-target breaches.

The first release uses fixed alert thresholds. Device Managers receive an
Odoo activity, and infrastructure operators receive an Alertmanager alert.

The fixed thresholds are:

- immediate alert for contract mismatch
- two minutes of Agent or bound Device unreachability during an open POS session
- at least 80% queue use for one minute
- at least 80% Device Spool use for one minute
- at least 90% Device Spool use immediately
- five minutes for an unresolved `outcome_unknown` Print Job
- three consecutive Client Grant renewal failures
- ten minutes of a breached p95 performance target.

Each condition and resource pair has one alert identity. A later observation
updates the existing Odoo activity and Alertmanager alert.

Five healthy minutes close the alert and its Odoo activity. A recurring
condition reopens the same alert identity.

Device Spool status is `healthy` below 80%, `near_limit` from 80%, `limited`
from 90%, or `blocked` after a storage or reconciliation failure.

The `near_limit` state clears below 75%. Device Center displays used bytes,
limits, and the oldest blocking job without displaying document content.

A Device Manager can create a reasoned Site maintenance window for at most
four hours. The window suppresses availability, queue, and performance alerts.

Security, certificate, and contract alerts remain active. The Controller audit
records the maintenance author, reason, start, and end.

The POS displays only a failure that affects the current operator action. It
does not display fleet or idle-device alerts.

## Agent driver architecture

The Device Driver Interface exposes:

- `discover()`
- `capabilities(device)`
- `execute(device, work, context)`
- `request_stop(execution_id)`
- `health(device)`.

The Interface is asynchronous. Blocking operating-system and hardware calls
run inside supervised Driver workers.

An execution result contains execution identity, Device, operation, outcome,
Output Evidence, Driver job identity, timestamps, byte count, and stable error.

The result excludes raw Driver text.

Device Spool encryption ends when the Agent submits content to the Windows or
CUPS Platform Backend. An operating-system spool can persist plaintext.

The first release prohibits a remote CUPS spool. The Agent must run on the CUPS
host, and that host must pass encrypted-storage attestation.

Production requires encrypted Agent Host storage, content-free platform job
names, disabled completed-job retention, disabled crash dumps for the Agent and
its workers, and bounded operating-system spool cleanup. The Agent creates no
extra plaintext temporary file for platform submission.

The Platform Backend checks these controls at startup. Local CUPS preflight
accepts an approved Unix socket or loopback transport. It rejects every remote
server URI before `Accepted`.

The Platform Backend binds encrypted-storage evidence to the active spool
path, volume identity, encryption state, and current boot. Missing or stale
evidence blocks content on that queue.

Completed operating-system spool files must disappear immediately. Failed,
canceled, or uncertain spool files must disappear within 15 minutes of the
result. A cleanup failure blocks new content on that queue and raises a
security alert.

The Agent stores each Platform Job Identity. A Windows identity contains the
queue, job value, submission time, and Inari document name.

A CUPS identity contains the server URI, printer URI, job URI, job value,
document format, and submission time.

Successful Windows `WritePrinter` and `EndDocPrinter` calls give `spooler`
evidence for receipt and label Drivers. The Driver checks every byte count.

The Windows Report PDF Platform Backend uses GDI. Successful `StartDoc`, page
calls, and `EndDoc` give `spooler` evidence.

Windows `JOB_STATUS_COMPLETE` gives at most `transport` evidence. An
unqualified `JOB_STATUS_PRINTED` state also gives at most `transport` evidence.

Only a signed Driver Profile with tested `TrueEndOfJob` behavior can give
`device` evidence on Windows.

A successful terminal CUPS job gives `spooler` evidence. A
`queued-in-device` reason can give `transport` evidence.

Direct final-media counters can give `device` evidence after physical
qualification. Generic CUPS completion cannot give `device` evidence.

A missing platform job never proves success. A short write or status loss
after platform submission produces `outcome_unknown`.

Cancellation after Driver execution produces `outcome_unknown` unless the
Driver proves zero output. An error after partial progress has the same result.

`request_stop` returns `stopped_before_output`, `stop_requested`, `unsupported`,
or `device_unreachable`. The runtime maps every uncertain result to
`outcome_unknown`.

Device Health uses `ready`, `busy`, `attention`, `unavailable`, or `unknown`.
It includes a stable reason code and `observed_at`.

The Agent accepts Driver health events when the Driver provides them. It also
probes Devices every five seconds during an active paired subscription.

Without an active subscription, the Agent probes every 30 seconds. Three
missed intervals make the observation stale.

The Agent does not probe an Exclusive Device during active execution.

Windows spooler and CUPS submission remain inside Platform Backends. Raw
socket, USB, and later device protocols remain inside Drivers. The runtime does
not call transport-shaped printer methods.

The Agent runs Driver execution in supervised worker processes. The Agent
process retains Device queues, Print Jobs, and state authority.

Each Driver package has one discovery worker. Each active Device has one
execution worker.

An unused discovered Device has no execution worker. A Device worker failure
does not stop another Device that uses the same Driver package.

Execution uses a two-phase start handshake. The worker requests permission to
start. The Agent commits the first Device I/O marker and `in_progress` state in
one transaction immediately before it grants permission.

The marker is the durable irreversible-execution boundary. It authorizes the
first operation that can change a Device, transport, or platform spool. It does
not prove that physical output occurred.

The worker starts its first Device I/O only after that grant. If the worker
exits before the grant, the Agent retries the operation internally. The retry
keeps the Print Intent, Print Job, Device, Binding Revision, deadline, Payload
Fingerprint, and Receipt Payload.

A second safe worker exit produces `failed`. A worker exit after the marker
produces `outcome_unknown`. The Agent never retries unknown work automatically.

Five worker exits within five minutes open the Driver circuit. The Agent marks
the affected Devices unavailable and restarts the worker with bounded
exponential backoff.

After an Agent restart, work before the start grant returns to its FIFO queue.
The Agent asks the Driver to reconcile work after the grant.

Without stronger Driver proof, that work becomes `outcome_unknown`. The Agent
does not print it automatically.

The Agent and each worker use private inherited operating-system pipes. The
control channel uses length-prefixed JSON from the Pydantic contract models.

A separate bounded channel carries binary data. A startup nonce binds the
worker instance to its parent.

Both channels apply strict size and deadline limits. Workers expose no network
listener or Driver-defined RPC operation.

The worker sandbox denies access to the Agent database, protected keys,
configuration, and unrelated files. It permits only the private IPC channels
and declared Device resources.

One deep Agent Resource Broker Module checks each resource request against the
Device identity and Driver Profile. It supplies only a connected socket,
Device handle, or bounded spooler operation.

A resource grant belongs to one execution and its remaining deadline. The
Resource Broker revokes every grant at terminal state or worker exit.

The Resource Broker can reconnect only during the same execution and only to
the checked destination.

A network grant permits only the endpoint and resolved addresses in the tested
Driver Profile. The Broker rejects unbound, loopback, link-local, metadata,
multicast, and unspecified addresses.

Private addresses require an exact Device Binding and passed Device Test. The
Broker pins the resolved set and rejects DNS rebinding.

An undeclared resource request fails before Device I/O with public code
`permission_denied`. Restricted diagnostics record `resource_denied`.

The Agent quarantines that Driver digest and raises an immediate security
alert.

The Agent does not retry the affected Device Work. Unrelated Driver digests
remain active.

A Driver worker has no general network or filesystem access. It cannot create
child processes or gain privileges. Each Driver worker has a 128 MiB memory
limit.

Linux uses `no_new_privs`, seccomp, Landlock, and cgroup v2 controls. Windows
uses a restricted token and Job Object.

Agent shutdown first stops new acceptance and leaves queued work durable. It
gives active work its remaining deadline, capped at 60 seconds.

A forced stop after Device I/O produces `outcome_unknown` without stronger
proof. The new Agent becomes ready only after its workers and Driver Profiles
pass startup checks.

The Agent and each worker use one exact Contract Major. A mismatch blocks
readiness. The release does not run mixed worker contracts.

The operation limits are:

- 60 seconds for printing
- five seconds for a Drawer Intent
- two seconds for a scale read
- 30 seconds for a Device Test.

A Driver can reduce its limit and cannot increase it. A worker timeout after
Device execution starts produces `outcome_unknown` without stronger evidence.

Device Work addresses a stable `device_id` only. Operating-system names remain
display and diagnostics data.

Device identity prefers hardware serial evidence, then operating-system
identity, then a configured port. The Agent persists aliases independently of
the Driver key and display name.

The Agent retains an unavailable Device for 90 days. A true identity match
restores its prior `device_id`.

A new hardware identity on the same port requires Device Manager approval. It
does not inherit the prior Binding Revision automatically.

The Local Agent Interface has no default-printer or printer-name selection.

Local Odoo Client Grants permit these operations:

- receipt-image submission
- cash-drawer actions
- scale reads
- scanner subscriptions
- Device Tests.

The grants deny raw printer bytes, text, HTML, arbitrary structured receipts,
and undeclared device commands.

The Contract Major migration removes transport-shaped `PrinterDriver` methods,
printer-name and default-printer selection, combined drawer fields, and obsolete
wrappers. All internal callers move in the same change.

Driver code ships only inside the signed Agent artifact. The Agent does not
download Driver code or reload it at runtime.

A staged Agent rollout supplies Driver canaries. Signed Driver Profiles remain
separate data and can change under their profile lifecycle.

The signed Agent artifact contains a Driver manifest. It lists each Driver
identity, code digest, operating-system target, Contract Major, and supported
profile-schema range.

The Agent and Controller report the active Driver digest. Agent readiness
blocks a Driver that is absent from the manifest or has a mismatched digest.

A signature or manifest-digest mismatch blocks complete Agent readiness. A
correctly signed optional Driver load failure affects only its Devices and
reports degraded readiness.

A physical canary runs before deployment to one Site. That Site runs for one
hour before fleet rollout.

The physical lab gate covers each changed Driver and hardware family. The Site
soak covers only the combinations present at that Site.

The release cannot include an untested changed combination.

The rollout stops after a new security failure, Contract failure, worker
circuit, or `outcome_unknown`. It also stops when the Device failure rate
exceeds 1%.

## Odoo module architecture

The core `inari_devices` addon depends on `base`, `point_of_sale`, and `mail`.
The addon receives Community `iot_base` indirectly through its direct
dependency on `point_of_sale`. The addon does not depend directly on `iot`,
`iot_base`, `pos_iot`, `stock`, or another Enterprise module.

The first Conformance Target is Community 19. The installation-state guard
blocks Enterprise `iot` or `pos_iot` in `installed`, `to install`, or `to
upgrade`.

The guard remains active while either module is `to remove`. It permits Inari
installation only after removal completes. It also blocks later Enterprise IoT
activation while Inari is installed.

Enterprise use requires a licensed source tree, test database, and passed
conformance suite.

The same signed artifact contains the optional `inari_devices_stock` addon.
This addon depends on `base`, `inari_devices`, and `stock`.

Both manifests use the `LGPL-3` license and Odoo 19 module versions.

Stock-specific models, assets, and tests stay in `inari_devices_stock`. A
database without `stock` installs only the core addon.

Both manifests set `auto_install` to false. The Release Set selects the stock
addon explicitly.

Installation creates the roles, scheduled jobs, views, and assets. A guarded
addon removal check blocks removal of the core addon until Decommission
finishes.

Device Center owns Agent enrollment, local certificate trust, service
readiness, operating-system service control, and local repair. Odoo owns Client
Pairing, Device Bindings, permissions, workflow routing, and recovery.

Decommission supports Site and Organization scopes. Addon removal requires one
database-wide Decommission Run that includes every active Organization and
Site.

Each Decommission Run stores its scope, phase, actor, start time, result,
evidence identities, failure code, and Retry history. Each phase is idempotent.

The phases are:

1. Start the POS Drain and block new Device Work.
2. Drain accepted work and reconcile unknown work.
3. Create and check the Compliance Export and pre-removal backup.
4. Revoke Client Grants, workload credentials, and Agent dispatch authority.
5. Purge each reachable Device Spool.
6. Deactivate Binding Revisions and Report Bindings.
7. Record the completed scope.

If an Agent is physically unavailable, permanent Agent Quarantine can replace
Device Spool purge. The Decommission Run stores its absence, inventory,
revocation, and duplicate-output evidence.

A short-lived Decommission purge authority survives phase 4. It binds one run,
Agent, Organization, and Site. It permits only Device Spool purge and expires
after phase 5. It cannot authorize Device Work.

The runbook removes `inari_devices_stock` before `inari_devices`. A failed phase
keeps its scope disabled and records `decommission_incomplete` with one Retry
action.

The addon removal guard makes no network request and starts no asynchronous
work. It checks one committed database-wide completion record and stops removal
when that record is absent.

Every installed view, action, scheduled job, and control is functional. The
addon contains no placeholder action, empty menu, dead control, or future-only
route.

CI generates an inventory of each installed menu, view, action, control,
route, client action, server action, and scheduled job.

Every inventory entry requires a test identity. A missing test identity fails
CI.

The addon installs one Inari Devices app. Its Devices view is the landing
action.

The app contains Devices, Bindings, Agents, Print Audit, and Alerts. An
administrator-only Configuration section contains Organization Projections,
Site Projections, and workload settings.

Guided flows own Device Binding, Device Test, activation, Report Binding, Site
selection, Reprint Approval, and Decommission. Raw records remain read-only
outside these controlled actions.

Each step defines its loading, permission, prerequisite, success, and failure
state. A stale concurrent activation returns to the changed assignment and
never activates an obsolete Binding Revision.

Each control either performs its action or displays a complete prerequisite
state. An unavailable Agent or Controller does not leave an unresponsive
control.

Before a tested Binding Revision becomes active, every device action provides
a complete setup state. Device routing remains disabled.

One deep frontend Module owns Agent trust, transport, contract negotiation,
submission, events, and Job Reconciliation. Odoo workflow code does not call
the Agent Endpoint directly.

The Device Adapter Interface exposes three operations:

- `preflight(requirements)` returns readiness for the requested capabilities
- `submit(device_work)` returns the authoritative work identity and state
- `subscribe(selection, listener)` delivers state changes for selected work.

The `InariReceiptPrinter` Device Adapter serves the primary receipt printer and
preparation printers through their existing Odoo seams. It is independent from
Odoo proxy printer state.

Narrow patches carry Submission Context through `PosPrinterService.print()`,
`PosPrinterService.printHtml()`, the receipt caller, and
`PosStore.printOrderChanges()`.

The caller creates the immutable Submission Context before Odoo renders the
JPEG. Each `InariPrinter` queue entry stores the rendered element and its exact
context together.

`InariPrinter.printReceipt(element, submission_context)` creates one immutable
queue item. Queue drain submits that item directly. Mutable printer or service
state never stores the current Submission Context.

IndexedDB stores each pending content-free Submission Context by Print Origin,
preparation segment, Binding Revision, and Copy Ordinal. A reload or Retry
cannot create a different context for the same Print Intent.

The frontend service creates one Agent channel for each exact Agent and Client
Grant scope. It creates one serialized printer queue for each Binding Revision
and Device. Odoo proxy connection ordering cannot replace or drain these
queues.

The server adds an authoritative Binding Revision projection to the exact
`pos.printer` record. It does not add a `printer_type` value or replace Odoo's
native printer object. A preparation printer without that projection keeps its
Native Device Path unchanged.

Odoo continues to own category filtering, order-change calculation, receipt
segment order, and `OrderChangeReceipt` rendering. The addon patches
`generateOrderChange()` and `generateReceiptsDataToPrint()` only to carry the
immutable order-change and segment identity. It patches
`printOrderChanges()` only when the selected `pos.printer` has an authoritative
Inari Binding Revision.

`PosStore.printOrderChanges()` creates one stable preparation segment identity
for every `receiptsData` item. It passes the exact Submission Context to
`InariReceiptPrinter.printReceipt()`.

Preparation delivery state is stored per order, segment, Device, and Print
Intent. `lastPrints` and `updateLastOrderChange()` advance only through work
whose required targets are accepted or explicitly dismissed. Retry selects
only unresolved targets.

Both paths report Odoo success only after the Agent returns `Accepted`.
`RetryPrintPopup` remains active for Native Device Paths only. An Inari failure
always enters the Inari recovery flow.

Every active Inari path sets `webPrintFallback=false`. Missing or unavailable
Inari Devices return a controlled recovery result and never call `printWeb()`.

The Device Adapter returns the Odoo fields `successful`, `message`, `canRetry`,
`errorCode`, and `warningCode`. It also returns `inari=true`, the Inari state,
and available Print Intent or Managed Work identities.

Agent acceptance returns `successful=true`. Every earlier Inari failure returns
`successful=false`, `canRetry=false`, and registers recovery before it returns.
The `inari` branch bypasses `RetryPrintPopup` in `PosPrinterService.printHtml()`,
`PosStore.printChanges()`, and `sendOrderInPreparation()`.

The global recovery panel persists across receipt-screen navigation and an
unawaited `afterOrderValidation()` print promise. The Odoo receipt counter
changes only after Inari acceptance.

One content-free IndexedDB recovery task exists before submission starts. Its
states include `submission_pending`, `pending_agent`, `accepted`, `failed`, and
`resolved`. The recovery service owns de-duplication and registers failures
inside the Device Adapter.

Cold reload first reconciles any stored Print Intent or Managed Work. A
`submission_pending` task can re-render only when its business source and
content revision still match. Otherwise it records `content_unavailable` and
offers a new explicit print or dismissal. It never resubmits unknown content.

An accepted POS Print Intent creates one content-free counter ledger entry.
Odoo updates `Copies requested` exactly once from this ledger. A failed
database write remains pending and repairs during reconciliation. A unique
Print Intent constraint serializes updates from multiple tabs.

All Inari POS Print Origins, including the bill action, use this accepted
counter rule. Native Device Paths keep Odoo's existing counter behavior.

A narrow `InariHardwareAdapter` serves only the bound cash drawer. A separate
`InariDeviceInputAdapter` owns Certified Scale acceptance and Barcode Event
de-duplication. Both Device Adapters call the deep frontend Module.

The scale seam wraps the Odoo scale service and screen. It preserves Odoo tare
behavior and accepts only signed decimal Scale Readings with a current
Certification Record.

The input Adapter keeps scale mantissas as `BigInt` values. It requires two
fresh stable readings within one resolution, rejects invalid range and unit
states, and converts the accepted decimal only at the Odoo scale-service seam.
Closing the scale screen releases the Scale Lease. Transport or lease loss
invalidates the accepted reading at once.

The scanner seam owns one Agent subscription and sends checked Barcode Events
through the native Odoo barcode-reader seam. Keyboard scanning remains a
Native Device Path.

Scale and scanner Bindings share one browser stream only when their complete
paired Agent scope matches. Bindings for separate Agents use separate streams.

The drawer seam replaces only bound `HardwareProxy.openCashbox()` calls. It
also makes `PosStore.openCashbox()` return the hardware promise. Unbound calls
use the native Odoo path. A bound call awaits one separate Drawer Intent and
never falls back to a native pulse. Odoo records a manual employee action only
after the Agent reports `succeeded`.

Manual Weight has a separate permission and a content-free audit record. The
Device Adapter delegates all unbound behavior to the existing Odoo
Implementation.

An Odoo Report Binding assigns one `ir.actions.report` record to an active
Device Binding Revision. It records one Report Route and the required print
operation.

Each Report Binding selects a Site-scoped Binding Revision. Report PDFs use
`report_pdf`, and stock labels use `stock_label`.

Only one active Report Binding exists for one company, Site, report action,
and Report Route.

A manual action completes Site Resolution before Device Preflight. It opens a
Site-selection wizard when record context cannot resolve one exact Site.

The Site resolver matrix uses these sources:

- a POS order uses its POS configuration
- a stock picking uses its picking type and warehouse mapping
- a package, move, or reception label uses its related picking
- a product or lot label uses its active operation context
- a generic manual report uses a registered resolver or the Site-selection
  wizard.

Odoo owns each explicit Site mapping. Mixed-Site records split into stable Site
groups before rendering. An unresolved group fails without a default Site.

A marked automatic flow without one exact Site stops before Print Intent
creation. It records a controlled routing failure and never guesses or selects
a default Site.

This stop applies to that Site group only. Routing, render, and Device Preflight
failures become handled document results. Later Site groups continue and enter
the same persistent summary.

Each active Report Binding creates one contextual `ir.actions.client`. Its
`inari_report_print` tag appears as `Print with Inari` in the normal Odoo Print
menu.

Each generated report action carries `context.inari_managed_report=true` and
`context.inari_report_binding_id`. Automatic producers add the same marker and
binding identity.

Activation creates or updates one contextual action. Deactivation archives the
action and marks the Report Binding `Needs attention`.

The versioned secure request contains the Report Binding, expected Binding
Revision, report action, source model, ordered source identifiers, Site, report
type, copy count, and controlled wizard data.

Each report action defines field types, nullability, size limits, and the
canonical form for its wizard data. The server-generated marker and Report
Binding must match the contextual action and request. A stale or mismatched
marker fails before rendering.

The browser does not supply Idempotency Keys, Copy Ordinals,
rendered-document indexes, sequence identities, or marker authority. The
backend allocates these values after validation and rendering.

One secure backend method checks the binding, report action, source records,
company, Site, groups, record rules, report marker, and active Binding Revision.
It then renders the Report PDF or Label Document through the normal Odoo report
engine.

The browser never supplies rendered bytes, a target Device, or unrestricted
report options to this method.

The addon registers one Device Adapter at the `ir.actions.report handlers`
seam. The Device Adapter handles only a report action with an explicit Inari
marker and calls the secure backend method.

The Device Adapter returns a false value only for an unmarked report. Odoo then
keeps its normal report download and browser-print Implementation.

Every marked action returns a truthy handled result. A missing binding,
unresolved Site, render failure, or failed Device Preflight uses a controlled
result and never starts native report download.

The versioned handled result contains `handled=true`, `status`,
`report_sequence_id`, and document rows. Each row contains its Site,
`message_key`, permitted action, and every available Print Intent, Managed
Work, or Print Job identity.

Automatic stock report and label actions use Inari only when an active
automatic Report Binding and explicit marker exist. A `qweb-pdf` report becomes
a Report PDF.

An approved Community stock `qweb-text` report becomes a ZPL Label Document
only after template qualification.

The Inari Stock Addon owns marked automatic sequences from
`_get_autoprint_report_actions()` and `_post_put_in_pack_hook()`. It groups
their source records by exact Site and sends each document through the shared
secure backend method.

One Site-specific rendered PDF is one Print Intent. The PDF can contain
multiple source records and pages. Each requested copy creates a separate
Print Intent.

The addon splits each ZPL envelope at its checked `^XA` and `^XZ` boundary.
Each envelope creates one Label Document and one Print Intent.

The handler and stock action sequences continue after each handled Device
failure. They never reverse a completed payment or stock operation.

Odoo renders both document types before it submits Managed Device Work. The
Agent does not fetch `/report/download` or another authenticated Odoo URL.

After Print Intent creation, the Device Adapter never returns false, reroutes,
or starts an implicit browser print. It records recovery and returns a handled
result.

An automatic Inari report failure does not raise an RPC error after a payment
or stock transaction. It cannot change the completed business state.

For a failure after Print Intent creation, the report Device Adapter records
the content-free Print Intent and its failure. It returns a handled result,
and Odoo continues the automatic report sequence.

Marked actions force `close_on_report_download=false`. The sequence aggregator
runs Odoo `anotherAction`, then opens one persistent summary for all Site
groups.

The summary includes accepted, pending, and failed documents. Each failed or
pending document keeps its permitted recovery actions.

One `inari.report.sequence` record owns the sequence identity, source action,
actor, company, and creation time. Its document rows link Site, Print Origin,
Print Intent, Managed Work, and Print Job when each identity exists.

The sequence identity is idempotent across action retries. Job Reconciliation
updates pending rows. The record remains available for 90 days and follows the
same company and role rules as Print Audit.

The summary states `Operation complete. 3 documents sent. 1 document failed
to print.` It lists the document, Device, stable reason, and permitted action.

A Retry first runs Job Reconciliation for the Idempotency Key. If the
Controller has no record, Odoo renders the report again.

The Retry compares the new Payload Fingerprint with the stored value. A match
keeps the Print Intent. A mismatch requires a new Print Intent.

The Document Imaging Module checks and prepares every public print operation
before a Driver receives a physical artifact. Drivers remain focused on
physical output.

The `report_pdf` Interface has one Windows Platform Backend and one Linux
Platform Backend. The operating-system Implementations stay behind the same
Interface.

The Windows Platform Backend uses a signed and pinned
`inari-pdf-renderer.exe` built on PDFium. Its build disables V8 JavaScript,
XFA, and machine-time access.

The Document Imaging supervisor starts the renderer with a restricted token
and Windows Job Object. The renderer has no network access, printer access, or
child-process permission.

The renderer streams one BGRA page through bounded local IPC. A GDI component
in the same Windows Platform Backend uses `StartDoc`, page calls, and `EndDoc`.

The `StartDoc` result enters the Platform Job Identity. The Release Set pins
the renderer digest. Print Job provenance records each embedded font digest.

The Linux Platform Backend submits a checked PDF through typed CUPS calls. It
sends an explicit `application/pdf` format and stores the complete IPP job
identity.

The Linux Platform Backend requires CUPS to advertise PDF support and accept
jobs. It does not parse `lp`, `lpstat`, or another localized command output.

Device Preflight checks `application/pdf`, media, resolution, color mode,
queue state, and job acceptance. It compares the capability-snapshot digest
with the tested Driver Profile.

The Platform Job Identity records CUPS and libcups versions, scheduler and
printer URIs, printer UUID, job URI, job value, and capability digest.

SumatraPDF, Ghostscript, and AGPL MuPDF are not production Agent dependencies.

Every Report PDF first enters a disposable `qpdf` preflight worker. The worker
uses pikepdf to inspect the source bytes in memory. Its qpdf syntax check must
return no warnings or errors before work can continue.

The worker also inspects the structured qpdf object model. Syntax success does
not replace the document policy.

The complete qpdf syntax, object, resource, and font policy runs before
`Accepted`. A rejection releases its Spool Reservation, creates no Print Job or
Platform Job Identity, and causes no Driver I/O.

The preflight worker rejects:

- encryption
- JavaScript and automatic actions
- launch, submit, import, and remote actions
- attachments and embedded files
- annotations
- AcroForm and XFA
- rich media, 3D content, audio, and video.

The first Report PDF limits are:

- 10 MiB input
- 50 pages
- 50,000 objects
- 64 levels of object nesting
- 64 MiB for one decoded stream
- 256 MiB of decoded streams for one job
- 17 by 17 inches for one page
- 150, 203, or 300 DPI from the Driver Profile
- 24 million pixels for one page
- 200 million pixels for one job
- 384 MiB qpdf worker memory
- 20 seconds of CPU time
- 30 seconds of wall time.

The 17-inch geometry limit and 24-million-pixel limit are independent. A page
must pass both.

The qpdf and PDFium workers are separate from Driver workers. The PDFium worker
has a 256 MiB memory limit. The Windows Platform Backend has a 160 MiB memory
limit. The renderer-to-backend pipeline holds one BGRA page buffer in total.

Each worker uses a fixed locale, timezone, color space, render flags, and white
background. It has no network or system-font access.

The worker uses a valid `CropBox`, then `MediaBox`. It applies rotation once,
flattens alpha onto white, and excludes annotations.

Contract Major 1 requires every referenced font to be embedded. The renderer
has no font fallback. A missing font or glyph fails document processing before
`Accepted`.

qpdf creates no derived artifact. Windows streams one page and persists no
rasterized report. Linux submits the checked source PDF.

Each Print Job stores source, policy, renderer, font, profile, capability, and
Release Set digests. It stores the derived artifact kind, size, and digest when
a Platform Backend creates one.

The Agent advertises `report_pdf` only when preflight isolation, Platform
Backend startup, capability checks, and the physical Device Test all pass.

The same source PDF and pinned Windows inputs produce identical page bitmaps.
The contract does not promise identical spool bytes or identical Linux output.

Cross-platform acceptance requires matching physical geometry and document
content.

The Linux Driver Profile pins the CUPS filter, CUPS backend, queue, media,
resolution, and capability digests. A change returns `capability_changed`
before Device I/O and requires a new Binding Revision and Device Test.

For a Label Document, the Module accepts only ZPL Contract Major 1. An approved
Report Binding pins the report, template, command profile, layout profile, and
template digest.

The binding validates this exact chain before rendering:

```text
report action -> template digest -> layout profile -> Hardware Certification Matrix Row
```

An action, template, layout, or Matrix Row mismatch stops before Agent
acceptance.

Contract Major 1 permits only these six registered Community 19 report actions:

- `stock.label_product_product`
- `stock.label_lot_template`
- `stock.label_package_template`
- `stock.label_package_history_template`
- `stock.label_packaging_barcode`
- `stock.label_picking_type`.

The unregistered `stock.label_transfer_template_view_zpl` view is outside the
contract.

The Release Set owns each approved effective template digest. A changed view
marks its Report Binding `Needs attention` before another label can start.

The Inari Stock Addon hardens approved views with one ZPL field-data escape
helper. Static and rendered-fixture checks cover every dynamic value.

Every approved envelope contains mandatory `^CI28`. Every `^A0N` command
contains explicit height and width. Coordinates, fields, fonts, barcodes, and
envelope counts fit the exact layout profile. The addon checks requested
quantities before rendering.

The structural parser accepts only `^XA`, `^XZ`, `^CI28`, `^FO`, `^FT`,
`^A0N`, `^FD`, `^FS`, `^FH`, `^BY`, `^BCN`, and `^BXN`.

The parser rejects commands outside a label envelope. It rejects `^PQ`, prefix
changes, stored objects, files, network control, firmware, configuration,
diagnostics, RFID, SGD, ZBI, and arbitrary binary data.

Each dynamic field uses canonical `^FH` escaping with `_` as the indicator.
The helper escapes literal `_` as `_5F`.

Every quality-200 `^BX` command declares backslash as its Data Matrix escape
character. The helper first applies Data Matrix barcode escaping, including a
doubled literal backslash, and then applies `^FH` byte escaping.

The parser rejects malformed UTF-8, unsupported controls, malformed escapes,
and field or barcode overflow. The addon never removes, replaces, or truncates
an invalid business value.

Invalid field data fails before `Accepted` with `label_data_invalid`. A
post-render parser does not make an unknown template trusted.

Product labels use these exact 203 DPI profiles:

- `odoo_stock_product_zpl_203_normal_v1` for 2.25 by 1.25 inch media
- `odoo_stock_product_zpl_203_small_v1` for 1.25 by 1 inch media
- `odoo_stock_product_zpl_203_alternative_v1` for 2 by 1 inch media
- `odoo_stock_product_zpl_203_jewelry_v1` for 2.2 by 0.5 inch media.

Each active profile also records printable width, label length, origin, margins,
orientation, and report-action mapping in integer dots. The certified hardware
and media determine these values.

A profile with no exact Hardware Certification Matrix entry remains inactive.
The release does not derive physical support from inch dimensions alone.

Larger stock labels use the separate exact
`odoo_stock_zpl_203_4x6_v1` profile. It requires 203 DPI, 812 printable dots,
1,218 printable dots of label length, and 4 by 6 inch media.

A rendered Odoo label report accepts at most 2 MiB and 500 complete label
envelopes. After structural validation, the addon splits the report at exact
`^XA` and `^XZ` boundaries before it creates Print Intents.

One Label Document contains exactly one envelope. It creates one Print Intent,
Managed Work record, and Print Job for one physical label. The rendered-document
index and copy ordinal preserve its position in the source report.

A Label Document cannot change media, density, scaling, or copy count.

Each ZPL Driver Profile pins manufacturer, model, firmware version and build,
language mode, fonts, media sensing, and a complete device-configuration digest.
Unknown or changed identity marks the Binding Revision `Needs attention` and
returns `capability_changed`.

ZPL-only covers the six approved Community 19 report actions. Other report
actions and printer languages remain Native Device Paths.

For a Receipt Image, the Module orients, scales, converts to grayscale, and
dithers the image once for its Driver Profile. Odoo remains the receipt-layout
authority.

Before `Accepted`, the Module accepts `image/jpeg` only and checks
the MIME type and JPEG magic bytes. The binary input limit is 2 MiB.

The image limit is 32 megapixels and 32,768 pixels for each dimension. The
Module removes metadata after it applies the image orientation.

The same JPEG, Driver Profile, and Document Imaging Module version produce
identical raster bytes. Each Print Job and Print Audit Record stores the
imaging version and profile digest.

The Agent encrypts the original JPEG and derived raster in its private spool.
Internal retries reuse the derived raster.

Each Print Job receives a random AES-256-GCM data key. A master key from
`ProtectedSecretStore` protects that data key.

Each master key has a version. New Print Jobs use the newest version.

A background operation atomically rewraps data keys with the newest master
key. It does not decrypt or encrypt the receipt artifact again.

The Agent deletes an old master key only after no stored data key references
it.

Authenticated data binds each ciphertext to its Print Job, Print Intent, and
artifact type. Each encrypted artifact uses a unique nonce.

Terminal retention rules delete both files and the wrapped data key. This
deletion makes retained ciphertext unusable.

Production stops accepting content-bearing Device Work when protected
operating-system storage is unavailable. The Agent never writes spool content
to logs or a Diagnostics Bundle.

If a referenced spool master key is unavailable, the Agent blocks new
content-bearing work. Pre-I/O work fails with public code
`service_unavailable`.

Restricted diagnostics record `spool_key_unavailable`. Active work becomes
`outcome_unknown`, and the Agent raises a security alert.

The Agent commits the terminal state before it deletes unreadable ciphertext.
The product has no receipt-key escrow or payload recovery path.

An Agent Administrator uses Device Center and local operating-system
authentication to acknowledge and repair protected storage. Odoo roles cannot
reset a local key.

The Agent creates a new master key only after the protected store passes its
self-test.

The new key permits new Device Work. It cannot recover old ciphertext. The
security alert remains open until local repair and terminal-state work are
complete.

The Agent rotates spool master keys every 90 days and immediately after
suspected exposure. An interrupted data-key rewrap resumes idempotently.

The Driver owns printable width, DPI, raster mode, cut support, drawer pulse,
and Output Evidence in its Driver Profile.

A Device Manager selects only options declared safe in that profile. Odoo
cannot send raw printer bytes or arbitrary device commands.

A Binding Revision selects cut mode, feed lines, and dither mode from the
Driver Profile. The resolved values enter Device Work and its Payload
Fingerprint.

The operator receives no per-print physical controls.

Device Preflight supplies the Driver printable width to Odoo. Odoo renders the
receipt at that width.

The Document Imaging Module preserves aspect ratio. It can downscale or center
the image. It never crops or upscales the receipt.

Threshold dithering is the default. A tested Driver Profile can select
Floyd-Steinberg dithering.

Every Device Test includes barcode and QR-code readability for the selected
dither mode.

Cash-drawer actions use a separate idempotent Drawer Intent. Receipt printing
never adds an implicit drawer pulse. The active Binding Revision authorizes
the `open_cash_drawer` operation.

A Drawer Intent request contains `contract_major`, `drawer_intent_id`,
`binding_revision_id`, `device_id`, `pos_session_id`, `action_sequence`, and
`reason`. The reason is `payment` or `manual_open`. The Agent derives the Odoo
database, Organization, Site, POS configuration, Paired Client, and actor from
the accepted Client Grant.

The Agent stores each Drawer Intent for 90 days before Device I/O. It commits
an I/O marker before the pulse. An exact replay never creates a second pulse.
A pre-I/O failure can Retry the same Drawer Intent. A timeout or exception
after the marker becomes `outcome_unknown` and cannot Retry automatically. A
deliberate repeated opening creates a new Drawer Intent.

The implementation removes the existing `PrintJob.open_drawer` composite
option. It does not keep a compatibility path for combined receipt and drawer
work.

`pos.config` exposes computed active Binding Revision fields for the receipt
printer, cash drawer, scale, and scanner. These fields contain no copied Device
data.

`pos.printer` exposes its computed active Binding Revision without changing
`printer_type`. Managers edit all assignments through the Device Binding view
and revision workflow.

Device Preflight compares the server addon build identifier with the bundled
JavaScript identifier. A mismatch requests one hard reload and preserves
IndexedDB.

The addon blocks Inari Device Work if the mismatch remains after the reload.

The addon does not emulate the Odoo `/hw_proxy` HTTP Interface. Native Device
Paths keep their existing Odoo Implementations.

The Local Agent Interface contains these versioned endpoints:

- `POST /v1/preflight`
- `POST /v1/device-work`
- `GET /v1/jobs/{job_id}`
- `POST /v1/jobs/query`
- `POST /v1/drawer-intents`
- `POST /v1/drawer-intents/query`
- `GET /v1/events`.

`POST /v1/jobs/query` accepts 1 to 100 Print Intent IDs. The Agent derives the
Organization, Site, POS Configuration, and Paired Client from the accepted
Client Grant. It returns public Print Job snapshots in request order, missing
Print Intent IDs, and a scope-specific high-water mark. An out-of-scope Print
Intent is reported as missing. The request and response contain no Device Work,
Receipt Payload, or raw Driver output.

`POST /v1/drawer-intents` requires `device_work:drawer`. The
`Idempotency-Key` header must equal `drawer_intent_id`.
`POST /v1/drawer-intents/query` requires `jobs:read` and accepts 1 to 100
Drawer Intent IDs. Both endpoints derive their scope from the Client Grant.
The query returns records in request order and reports out-of-scope identities
as missing. Public responses contain no tenant or actor data.

The POS browser requests the exact canonical union of permissions for its
active bindings on one Agent. Receipt and preparation bindings add
`device_work:receipt_image` and `jobs:read`. A drawer binding adds
`device_work:drawer` and `jobs:read`. Scale and scanner bindings add their
device-read permission and `events:read`.

Client Pairing uses a separate privileged Interface under `/pairing/v1/`.
Device Work credentials cannot call that Interface.

The browser Client Pairing Interface contains these endpoints:

- `POST /pairing/v1/requests`
- `GET /pairing/v1/requests/{request_id}`
- `POST /pairing/v1/requests/{request_id}/cancel`
- `POST /pairing/v1/requests/{request_id}/admit`
- `POST /pairing/v1/client-grants/renew`.

Device Center uses its separate application identity for:

- `GET /pairing/v1/requests/{request_id}/review`
- `POST /pairing/v1/requests/{request_id}/decision`.

Browser calls use exact-origin key proof and no authorization header. Device
Center review and decision calls require its local administrative credential.

Versioned Python protocol models are the contract source. The build publishes
committed OpenAPI 3.1, JSON Schema, generated client, and contract-fixture
artifacts.

CI regenerates all contract artifacts and fails on a diff. The compatibility
comparison requires a Contract Major increase for a breaking change.

Each persistent schema or semantic change uses a versioned Odoo migration
script. Migration tests use historical fixtures and include a rollback test.

Runtime code does not retain compatibility parsing for migrated records.

A production migration starts a maintenance window and stops Odoo traffic. The
operator checks a pre-migration backup before migration.

Production uses CloudNativePG WAL archiving and scheduled base backups in
locked EU dual-region Google Cloud Storage. The recovery-point target is five
minutes, and backup retention is 14 days.

A fresh custom-format `pg_dump` provides the portable pre-migration backup.
The gate records its SHA-256 digest and reads its catalog.

The gate restores the dump into an isolated PostgreSQL instance of the same
major version. It runs the migration and acceptance suite against that
restored database.

The isolated restore runs in an ephemeral namespace with no ingress and a
deny-by-default network policy. It uses encrypted storage and short-lived
credentials.

The gate deletes the namespace and its volumes within one hour. Logs and
Diagnostics Bundles exclude restored business values.

The team runs a complete recovery drill each month. A post-migration backup
starts after the production gate succeeds.

The rollout runs the migration and fresh-database acceptance suite before it
reopens traffic. A failure before reopening restores the backup and prior
artifacts.

After traffic reopens, recovery uses a forward fix. It does not restore an old
database over new business transactions.

The planned-downtime target is 15 minutes. Each release sets its rollback point
from a production-size rehearsal and measured restore time.

The migration gate blocks when:

- WAL archive lag exceeds five minutes
- the latest base backup is older than 24 hours
- the latest recovery drill is older than 35 days
- the fresh logical dump cannot restore
- the isolated acceptance suite fails.

Each scheduled job uses a single-flight lock, company-scoped cursor, bounded
batch, and idempotent writes. It stores the last success and stable error.

After three consecutive failures, the job updates one manager activity. It
does not create duplicate notifications. A successful run closes the activity.

A daily company-scoped job deletes expired terminal Print Audit Records,
content-free job metadata, inactive test history past its retention period,
and expired diagnostics metadata. It uses bounded batches and the same
scheduled-job controls.

Cleanup preserves active Binding Revisions and each Device Test Result that an
active revision needs. The first release has no legal-hold Interface.

A tenant that needs longer retention exports the records to its compliance
system.

A System Administrator can create a date-bounded Compliance Export. It
contains structured NDJSON records and a signed SHA-256 manifest.

Odoo signs the manifest with a database-scoped Ed25519 Compliance Key through
OpenBao Transit. The Controller publishes the attested public key with its
database and Organization scope.

Compliance public-key history has no time limit. Private key material remains
inside OpenBao.

The export contains no Receipt Payload content. Odoo provides one authorized
download. The first release stores no external-destination credentials and
sends no automatic export.

One compressed export volume can contain at most 100 MiB. A larger export uses
deterministic volumes under one signed root manifest.

The exporter never truncates records. One download session can fetch all
volumes. Odoo deletes generated files after completion or 24 hours.

Device Work with binary content uses `multipart/form-data`. One part contains
the canonical JSON envelope, and one part contains the exact binary payload.

Preflight and Print Job queries use JSON. Receipt Payloads do not use Base64.

The envelope uses RFC 8785 JSON Canonicalization Scheme after Device Capability
schema normalization. The Payload Fingerprint hashes the exact binary part
separately.

The Local Agent Interface submission returns `202` with a new accepted Print
Job. An exact local replay returns `200` with the existing Print Job.

The Managed Workload Interface submission returns `202 pending_agent` with a
new `managed_work_id`. An exact managed replay returns `200` with the existing
Managed Work and its current state.

Both Interfaces return:

- `409` for an Idempotency Key and Payload Fingerprint conflict
- `413` when the decoded operation exceeds its content limit
- `422` for invalid or unsupported Device Work
- `429` when a queue or Device Spool quota is full
- `507` when the protected filesystem reserve is unavailable.

HTTP errors use RFC 9457 Problem Details. Each response contains the standard
`type`, `title`, `status`, `detail`, and `instance` members. `type` is the
stable `urn:inari:problem:v1:{error_code}` URI. `title` is a fixed safe English
summary. `detail` is safe operator text. `instance` is the request correlation
URN.

The `error_code`, `message_key`, `retryable`, `correlation_id`, and controlled
`details` members are Problem Details extensions. The HTTP status equals the
`status` member.

Problem Details exclude raw Driver text and Receipt Payload data.

A Client Grant can query Print Jobs from the same Organization, POS
configuration, and paired client. A Device Manager scope permits wider Site
queries.

One batch query contains at most 100 Print Job identifiers.

The Agent State Envelope uses compact JWS with EdDSA over RFC 8785 JSON. Its
protected header contains the Agent `kid`.

Odoo checks the signature through the Agent JWK Projection and current
certificate status.

Each pull request installs the addon into a fresh Odoo database. The acceptance
suite opens every permitted menu and view.

The build generates a complete installed-surface inventory. Each menu, view,
action, control, route, client action, server action, and scheduled job has a
test identity.

The suite runs every action and scheduled job with controlled Device Adapters.
It completes Client Pairing, Binding Revision activation, Device Tests, ticket
printing, recovery, and Diagnostics Bundle generation.

The suite fails for a dead control, missing action, placeholder route, or
unhandled prerequisite.

Each pull request also runs protocol tests, Python tests, Odoo ORM security
tests, HOOT tests, POS tours, and the fake Agent suite.

The addon matrix installs `inari_devices` without `stock`. It also installs
`inari_devices` with `inari_devices_stock` and `stock`.

Report tests cover manual actions, automatic stock reports, package labels,
POS stock reports, unmarked browser downloads, access rules, and stale
Bindings.

They prove that binding, Site, preflight, and render failures create no Print
Intent. They also prove Site grouping, handled-sequence continuation, exact
managed replay, fingerprint conflicts, deadline expiry, and cancellation races.

POS tours prove that an automatic report failure leaves the paid order paid.
Stock tests prove that the same failure leaves the stock operation complete.

POS tours also prove active-Binding-Revision checks for primary and preparation
printers. They prove that an Odoo proxy reconnect cannot replace
`hardwareProxy.printer`, that each Print Intent requests one physical copy,
and that concurrent Copy Ordinal allocation is transactional.

Transport tests cover lease expiry, suspended-tab lease loss, stale fencing
generation rejection, and successor reconciliation through its high-water
mark before state events resume.

The Agent suite uses malformed-PDF and hostile-ZPL corpora. PDF tests cover
rejection before `Accepted`, reservation cleanup, no Driver I/O, encryption,
active content, objects, nesting, streams, pages, independent geometry and
pixel limits, embedded fonts, worker memory, timeouts, and termination.

ZPL tests cover the six action allowlist, excluded transfer view, exact media
profiles, every allowed command, every prohibited class, mandatory `^CI28`,
full font dimensions, field-data injection, Data Matrix escape order, literal
escape characters, alternate delimiters, controls, overflow, label counts,
geometry, firmware and configuration drift, fuzz input, and resource limits.

Physical tests cover Windows PDFium, Linux CUPS PDF, queue removal, status
loss, cancellation, partial output, and qualified Output Evidence.

The physical matrix also covers Windows and Linux receipts, preparation,
drawers, Certified Scales, scanners, ZPL labels, paper-out, cover-open, power
loss, reconnect, and short writes.

Cross-path tests cover Local Device Work, Managed Device Work, offline use,
Recovery Fence, Dispatch Epoch, Agent Quarantine, and duplicate suppression.

Security tests cover Pairing Assertions, DPoP replay, tenant isolation, HPKE,
Zenoh keyspaces, sequence fencing, egress policy, roles, and content redaction.

Recovery tests cover Admission Epoch row-lock races, signed five-minute
checkpoints, OpenBao snapshot rotation, KMS outage, Agent absence, both soak
canary boundaries, permanent Agent Quarantine approval, and decommission
restart after each phase.

A release runs migration, physical canary, POS asset-cache, staged-rollout,
and rollback tests.

## Implementation sequence

The implementation grows through working vertical layers:

1. Remove obsolete public print paths and the conflicting
   `docs/odoo-print-behaviours.md` contract. Correct the protocol security
   seams.
2. Add the encrypted Device Spool, quotas, idempotency, and restart recovery.
3. Complete physical Receipt Image printing on Windows and Linux.
4. Build the core addon, Client Pairing, POS ticket UX, and fake Agent suite.
5. Add preparation printers, drawers, Certified Scales, and scanners.
6. Add Report PDF and the safe ZPL Label Document contract.
7. Add the Inari Stock Addon and automatic report flows.
8. Add managed delivery and production Controller infrastructure.
9. Run all physical, recovery, security, upgrade, and release gates.

Each layer works end to end before work starts on the next layer. A later layer
cannot leave an installed surface incomplete.

## Decisions

- [Use Inari-native hybrid device integration](./adr/0001-use-inari-native-hybrid-device-integration.md)
- [Own the Odoo addon in Inari](./adr/0002-own-the-odoo-addon-in-inari.md)
- [Align Odoo companies with Inari organizations](./adr/0003-align-odoo-companies-with-inari-organizations.md)
- [Use OIDC for the Odoo workload](./adr/0004-use-oidc-for-the-odoo-workload.md)
- [Treat durable acceptance as Odoo print success](./adr/0005-treat-durable-acceptance-as-odoo-print-success.md)
- [Use Odoo-rendered receipt images](./adr/0006-use-odoo-rendered-receipt-images.md)
- [Use versioned Print Intent identity](./adr/0007-use-versioned-print-intent-identity.md)
- [Use trusted local HTTPS for POS device work](./adr/0008-use-trusted-local-https-for-pos-device-work.md)
- [Pair each POS browser with a client key](./adr/0009-pair-each-pos-browser-with-a-client-key.md)
- [Make the Agent the Print Job authority](./adr/0010-make-the-agent-the-print-job-authority.md)
- [Require Certified Scales for price calculation](./adr/0011-require-certified-scales-for-price-calculation.md)
- [Hide scheduler states behind the Print Job Interface](./adr/0012-hide-scheduler-states-behind-the-print-job-interface.md)
- [Require local print idempotency](./adr/0013-require-local-print-idempotency.md)
- [Limit offline client trust](./adr/0014-limit-offline-client-trust.md)
- [Store only Inari projections in Odoo](./adr/0015-store-only-inari-projections-in-odoo.md)
- [Use one deep POS device module](./adr/0016-use-one-deep-pos-device-module.md)
- [Use immutable binding revisions](./adr/0017-use-immutable-binding-revisions.md)
- [Use capability-shaped device drivers](./adr/0018-use-capability-shaped-device-drivers.md)
- [Isolate driver execution](./adr/0019-isolate-driver-execution.md)
- [Use the Managed Workload Interface and reconciled webhooks](./adr/0020-use-a-workload-interface-and-reconciled-webhooks.md)
- [Keep addon artifacts separate](./adr/0021-keep-addon-artifacts-separate.md)
- [Ship Driver code with the Agent artifact](./adr/0022-ship-driver-code-with-the-agent-artifact.md)
- [Migrate Odoo before reopening traffic](./adr/0023-migrate-odoo-before-reopening-traffic.md)
- [Broker every Driver resource](./adr/0024-broker-every-driver-resource.md)
- [Deploy one signed Release Set](./adr/0025-deploy-one-signed-release-set.md)
- [Require off-site WAL recovery for Odoo](./adr/0026-require-off-site-wal-recovery-for-odoo.md)
- [Deploy the first Controller through MZE infrastructure](./adr/0027-deploy-the-first-controller-through-mze-infrastructure.md)
- [Store pending payloads in PostgreSQL](./adr/0028-store-pending-payloads-in-postgresql.md)
- [Sign compliance exports with a database identity](./adr/0029-sign-compliance-exports-with-a-database-identity.md)
- [Block managed production on a single host](./adr/0030-block-managed-production-on-a-single-host.md)
- [State the managed-payload backup exposure](./adr/0031-state-the-managed-payload-backup-exposure.md)
- [Drain POS before maintenance](./adr/0032-drain-pos-before-maintenance.md)
- [Bind reports explicitly](./adr/0033-bind-reports-explicitly.md)
- [Use explicit document operations](./adr/0034-use-explicit-document-operations.md)
- [Fence and reconcile Controller recovery](./adr/0035-fence-and-reconcile-controller-recovery.md)
- [Use Odoo report extension seams](./adr/0036-use-odoo-report-extension-seams.md)
- [Isolate platform-specific PDF printing](./adr/0037-isolate-platform-specific-pdf-printing.md)
- [Store Device content in an encrypted spool](./adr/0038-store-device-content-in-an-encrypted-spool.md)
- [Isolate and encrypt managed Agent delivery](./adr/0039-isolate-and-encrypt-managed-agent-delivery.md)
- [Use independent production recovery keys](./adr/0040-use-independent-production-recovery-keys.md)
- [Model Managed Work separately from Print Jobs](./adr/0041-model-managed-work-separately-from-print-jobs.md)
- [Separate signing identities by trust boundary](./adr/0042-separate-signing-identities-by-trust-boundary.md)
- [Use durable scoped Decommission Runs](./adr/0043-use-durable-scoped-decommission-runs.md)
