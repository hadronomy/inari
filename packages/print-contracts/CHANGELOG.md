## inari-print-contracts@1.20.0-alpha.11

### Start the packaged Windows agent

The Windows package now includes every database migration that the agent needs
at startup. The service reports that it is running only after the API is ready.

### Drag Device Center from its title bar

The empty title bar area now starts the native Windows move action. Status and
window controls remain independent controls.

### Cleaner title bar drag wiring

Device Center declares its title bar drag region on every platform again instead of gating it per OS in the view. The Windows-only native drag workaround stays inside the platform module, where it stays until a gpui release ships fixed caption handling.

### Define the Odoo device integration

The architecture now defines the complete Odoo 19 device contract. It covers
POS tickets, reports, labels, scales, scanners, drawers, recovery, security,
release gates, and Decommission.

### Add browser Client Trust

The Agent now pairs an Odoo browser with an exact Agent Endpoint and business scope.

Client Grants use Ed25519 access tokens, browser-key DPoP proofs, durable nonces, and immediate revocation checks.

### Complete browser proof challenges

The Agent now issues browser-visible, single-use DPoP nonces through the standard protected-resource challenge flow.

### Add durable Device Work admission

The Agent now validates signed Device Capability records before it accepts print work. It stores encrypted receipt content and content-free authority proof for safe recovery.

### Execute admitted receipt work through the durable Device Spool

The Agent now rechecks the exact Client Grant and Device Capability before it permits printer I/O. It records an I/O marker first, runs printer submission in an isolated process, and reports uncertain output without an automatic retry.

Receipt images stay encrypted at rest. The Agent creates and reuses deterministic printer bytes without a plaintext temporary file.

### Route POS preparation tickets through Inari

Odoo preparation printers can now use an exact active Inari Binding Revision.
Odoo keeps its product-category routing and ticket rendering. Inari gives each
ticket segment a durable print identity and sends it through the bound Agent.

Preparation retries reuse the same Print Intent. Native Odoo preparation
printers continue to use their existing path.

### Add secure browser Client Pairing

The Agent now exposes the complete Client Pairing flow for Odoo POS browsers.
It requires exact-origin Ed25519 proof, Device Manager approval, a signed Odoo
Pairing Assertion, short-lived DPoP access tokens, and key-bound offline renewal.

### Pair each POS browser securely

The POS now guides an operator through Agent approval when a receipt printer
needs a Client Grant. It keeps the browser key non-exportable, resumes the same
ticket after approval, and renews short-lived access without exposing security
credentials to the operator.

Odoo now validates and signs each approved Pairing Request through an Ed25519
OpenBao Transit key. The private signing key stays outside Odoo.

### Add the Odoo 19 device foundation

Odoo can now project Inari organizations, sites, agents, devices, and
capabilities. Device managers can create immutable bindings, approve tested
revisions, and inspect content-free print records from Odoo.

### Route Odoo POS receipts through Inari

Odoo 19 can now keep an active Inari receipt binding authoritative from receipt rendering through durable Agent admission.

The browser retries one standard DPoP nonce challenge without changing the receipt identity. An unresolved Inari print stays in recovery and never falls through to native or browser printing.

### Recover interrupted POS prints safely

Odoo POS now keeps content-free print recovery tasks across reloads. Operators
can check Agent state, retry exact same-tab content, use browser print for a
customer receipt, or explicitly continue without a ticket. Preparation orders
do not advance until the Agent accepts the ticket or the operator dismisses the
recovery task.

### Reconcile local POS Print Jobs

POS browsers can now query their Agent for content-free Print Job status by
Print Intent ID. Agent scope checks hide all work owned by another pairing.

### Add safe cash-drawer intents

The local Agent now admits, executes, and reconciles durable cash-drawer intents. It prevents duplicate pulses and reports an uncertain outcome when a failure happens after device I/O starts.

### Route POS cash drawers through Inari

An active cash-drawer binding now replaces Odoo's drawer pulse with a durable Inari Drawer Intent. The POS prevents duplicate pulses, keeps native behavior for unbound drawers, and warns operators when the physical result needs manager review.

### Add secure POS Device Streams

The Agent now provides signed, DPoP-protected Scale Reading and Barcode Event
streams. Agent-owned fencing leases prevent stale browser contexts from using
device input.

### Add secure browser Device Streams

The Odoo POS browser can now hold and renew Agent Device Stream leases. It
verifies signed Scale Readings and Barcode Events, coordinates open tabs, and
reconciles device state before it accepts live input.

### Add Certified Scale and scanner input

The Odoo POS now reads bound Certified Scales and Agent-hosted scanners through
authenticated Inari Device Streams. Scale acceptance uses exact decimals,
freshness and stability checks, and the active Certification Record. Barcode
Events use Odoo's native barcode handling after Inari removes duplicates.

### Add secure Managed Work admission

The Controller now accepts preflighted, encrypted Report PDF and Label Document
work from an authorized Odoo backend. Agents publish a separate encryption key
during enrollment, so report content stays encrypted outside the target Agent.

### Validate Managed Work acceptance identities

The Controller checks the signed Agent State Envelope against the stored Managed
Work, Print Intent, and Device. A mismatched receipt cannot link another Print
Job or delete protected content.

### Check PDF and label content before acceptance

Linux PDF preflight now uses a confined qpdf library worker with bounded input,
memory, CPU time, and output. It rejects active content and checks embedded
font programs, referenced glyphs, page geometry, and decoded streams.

Label Documents now use the shared ZPL Contract Major 1 parser. The Agent checks
the exact layout, field escaping, barcode capacity, and single-copy envelope.

### Protect Managed Payload storage

The Controller encrypts stored Dispatch Envelopes with individual data keys
protected by OpenBao Transit. Command history retains only Managed Work
references. Acceptance, rejection, and expiry remove the stored content.

Retries now reject changes to the Device Work request, including its Device and
Print Origin. Exact retries retain their original identity during an OpenBao
outage.

The upgrade requires coordinated Controller downtime. It blocks while older work
remains pending and unexpired. Expired dispatches become Recovery Uncertain and
require reconciliation. The previous Controller binary cannot use the upgraded
schema.

### Register separate Agent State signing keys

Agents now keep a dedicated Ed25519 state-signing key in protected storage.
Enrollment registers its public key with the Controller. The Controller retains
previous public keys and rejects reuse across Agents or key purposes.

Upgrade the Agent and Controller together and apply the Controller migrations.
Enroll each Agent with a new invitation for gateway protocol `2026-09-06`.

### Reconcile managed Print Jobs from signed Agent evidence

The Controller verifies and stores signed Agent State observations. Managed Work
queries now include the physical Print Job state and its original signed envelope.
Valid evidence can recover a lost acceptance reply and stop further dispatch.
Older events cannot replace terminal results.

### Bind managed dispatch to the complete Device Work

The Controller and Agent now verify the same fingerprint for the document,
Device, options, and work deadline. A shorter dispatch credential lifetime does
not shorten the Print Job deadline. Stop new admissions and let pending work
finish or expire before upgrading the Controller and Agent together.

### Publish signed managed Print Job observations

The Agent signs managed Print Job state from its durable journal. Pending
observations survive restart and retain each historical state. Managed replies
stay bound to their original Agent, Organization, and Site after enrollment changes.

### Retry Print Job observations until the Controller stores them

The Agent now requires a Controller storage receipt for signed Print Job
observations. A connection failure or lost reply keeps the same observation
pending for retry. The upgrade requeues previously sent observations once.

### Reconcile managed report Print Jobs

Print Job queries now read the Report Origin stored by managed admission.
They preserve ordered report record IDs and wizard input digests, and reject
an origin whose Organization, Site, or Managed Work does not match its Print Job.

### Reject incomplete Windows spool writes

Windows printing now checks the spool job identity and complete byte count.
If submission fails, the Agent attempts to abort the open spool job instead
of finalizing a partial document. The physical outcome remains uncertain after
an I/O attempt; the Agent does not automatically print it again.

### Restrict managed Zenoh authority

The Controller access rules now permit managed command delivery, history
replies, and Agent observations with access control enabled.

Generated router configuration reserves command and storage-receipt authority
for Controller and router certificates. Before upgrading, set
`zenoh.config.accessControl.trustedPeerCommonNames` to their exact certificate
common names. The defaults are `inari-controller` and `inari-router`. Reserve
these names in the CA policy so Agents cannot obtain them.

### Require signed evidence for Managed Work acceptance

Unsigned command receipts no longer change Managed Work, stop dispatch, or
delete protected content. The Controller accepts managed Print Jobs only after
it verifies their Agent State Envelopes. An unsigned rejection keeps the work
unresolved until signed evidence arrives or its deadline requires Recovery
Uncertain.

### Add company-scoped Odoo Managed Work access

The Odoo backend can use short-lived OIDC tokens with company credentials stored
in OpenBao. Controller requests reject mismatched company scope and use bounded
HTTPS responses. Pairing Assertions share the same protected OpenBao transport.

The Controller adds a read-only Managed Work lookup by Idempotency Key. Odoo can
check an uncertain submission before it retries the original Print Intent.

### Validate report requests before preparation

Odoo rejects stale Report Bindings, changed Driver Profiles, inactive Sites,
and inaccessible source records before it creates a report sequence. Prepared
action tickets belong to one actor and company. Operators cannot rewrite their
stored authority fields.

### Restore Odoo 19 POS startup

The Inari POS addon now compiles correctly in the Odoo browser bundle and uses
the Odoo 19 RPC function. These changes prevent a blank POS screen and a missing
service error before the register opens.

### Keep one receipt identity across repeated clicks

Repeated receipt clicks now use the original Print Intent while that copy is
unresolved. POS reloads recover its identity from the local journal. A changed
render cannot allocate a second Origin Submission Key for the same receipt copy.

### Preserve encrypted print jobs on Windows

The Agent opens spool files in binary mode. Windows text conversion no longer
changes encrypted bytes or prevents receipt admission and recovery.

The Windows secret store also applies its service permissions through the
correct pywin32 API, so protected-key setup can complete.

### Protect Device observations

The Agent now signs current Device observations with a separate protected key.
It rejects receipt admission when Driver certification facts are unavailable.

### Package the Inari Odoo addon for production

The release workflow now publishes a signed Odoo addon artifact with its shared
print contracts and locked Python dependency.

### Declare compatibility in the signed Odoo artifact

The addon artifact includes a compatibility manifest for Odoo, Agent, Controller, shared contracts, and protocol versions. The shared-contract build backend uses an exact version.

### Retire Device authority signers without changing their identity

Authority bundles permit signer retirement and reject signer reactivation. Device observations use the verified Driver Profile for the requested Binding Revision, including Devices that expose more than one capability.

### Retire Agent verification keys when enrollment rotates them

Retired keys can replay stored observations for reconciliation. They cannot authorize new observations. A database migration retires obsolete keys and requires new enrollment when historical keys are ambiguous. Managed Work preserves the exact signed idempotency key. Controller dispatch permission follows the Helm dispatch configuration.

### Commit drawer intent identities before Device I/O

Cash drawer recovery waits for the browser storage transaction to commit. An Odoo upgrade preserves existing drawer capability and binding identities. Input streams reject requests until a production Driver supplies Scale Readings or Barcode Events.

### Preserve receipt recovery across POS reloads

Completed receipt copies leave the active recovery list. A content-free journal preserves their Print Intent for 90 days. Recovery uses the original Agent channel after a Device Binding changes. Authorized clients can query a Print Job by its durable identifier.

### Preserve browser trust and execution leases

A first protected request can receive a DPoP nonce challenge without an invented nonce. Client Grant renewal requires its original Client Pairing. Physical execution renews its lease during receipt preparation and worker startup.

### Report current Windows printer facts

The Agent reads the Windows queue port, driver, media width, and readiness on
each discovery pass. A missing or blocked queue cannot claim readiness.
Firmware remains unavailable until the Device reports it. The Agent does not
use the Windows driver version as Device firmware.
