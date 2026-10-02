## inari@1.20.0-alpha.11

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

### Preserve admitted document options

The Agent stores validated document options for execution after a restart.
Execution checks the stored options against their admitted digest before it
prepares output.

Drain active Device Work before this upgrade. The database migration preserves
historical outcomes and stops if active work still needs its missing options.

### Validate Managed Work acceptance identities

The Controller checks the signed Agent State Envelope against the stored Managed
Work, Print Intent, and Device. A mismatched receipt cannot link another Print
Job or delete protected content.

### Honor document resolution in CUPS

CUPS submission uses the admitted PDF resolution and requires the queue to
advertise that resolution and accept output. The Agent checks every submission
response and cancels incomplete CUPS jobs after an error.

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

### Install signed Device authority

Agent Administrators can import Controller-signed Device authority for an exact
Agent and Odoo scope. Installation verifies signatures and applies the complete
activation set in one transaction. It rejects revision rollback and preserves
Agent Quarantine.

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

## inari@1.20.0-alpha.10

### Fix Windows service startup and window controls

The Windows agent now starts when the service has no attached console. Device Center also shows its window controls and supports title bar dragging.

## inari@1.20.0-alpha.9

### Rebuild Device Center around a translucent shell

Device Center now opens with one glass tint across the window. The titlebar and
the navigation rail share this plane. The brand sits beside the native window
controls on their centerline. The content panel adds a thin tonal step, and
cards use quiet light or dark washes. The desktop remains visible through the
surfaces on macOS and Windows.

Translucency is a preference, not a fixed style. Support has a Display section
that switches the window to solid surfaces and stops the connection pulse and
the navigation slide. `INARI_MATERIAL=opaque` and `INARI_REDUCED_MOTION` apply
the same settings from launch. Linux keeps solid surfaces, because a blur
behind the window is not guaranteed there.

### Show the path from this computer to the devices

Overview opens with the connection path: this computer, the local agent, and
the devices it operates. A broken segment shows where the path stops, so the
first question an operator has is answered before they read anything else.

Items that need a person are now listed one by one with their state and what to
do, in place of a count that sent you to another screen to learn what it meant.

### Act on a problem from where you find it

Items in Needs attention are now controls. Selecting a device opens the device
directory with that device already selected, and selecting failed work opens
Activity. Reading a problem no longer means finding the same device again by
hand on another screen.

### Reach every control from the keyboard

The navigation rail, the agent health indicator, the device list, and the
attention items are all focus stops. Tab moves between them, Enter and Space
activate them, and Up and Down move the selection through the device list.

Focus rings follow the input device. They appear when you move with the
keyboard and stay hidden while you use the pointer, so selecting something with
the mouse no longer leaves an outline behind on it.

### Stop the repeated credential prompts

Device Center read this computer's stored identity on every attempt to reach
the agent. With the agent stopped, the reconnect loop turned that into a system
credential prompt every few seconds. The identity is now read once. A read that
fails is held, and Device Center reports that it stopped asking rather than
implying it is still trying. Select "Check again" in Support to allow access and
retry.

### Report agent health on every screen

The titlebar carries the agent state on all screens and opens Support when you
select it. A service that runs but does not answer now reports as "Not
responding" instead of "Running", and Support offers only the recovery action
that matches the current state.

### Make device and job states readable without color

Devices, jobs, and the agent service share one set of states. Each state has
its own label and its own icon, so the interface stays readable with
Differentiate Without Color, in high contrast, and in a grayscale screenshot.

Device kinds now have their own icons for printers, scales, and scanners.
Identifiers, endpoints, and error text are set in a monospace face.

### Start the Windows agent service

Windows can now start the packaged agent under the `LocalService` account. The
service uses production defaults when no custom config path exists.

## inari@1.20.0-alpha.8

### Fix Device Center setup after installation

Device Center now reaches the installed agent with valid request paths. Agent
outages show clear recovery steps and keep Support available.

### Improve Device Center clarity

The revised interface has consistent spacing, accessible light and dark color
tokens, clearer status messages, keyboard-ready navigation, and a readable
desktop typeface.

### Fix the macOS application icon

The macOS icon now uses the full canvas and lets the system apply its mask. The
mark also stays sharp at every bundle size.

## inari@1.20.0-alpha.7

### Make Device Center a native Rust application

Device Center and its tray now run on GPUI, with one coherent setup and
operations shell backed by a typed local-agent client. Setup resumes from the
agent’s durable checkpoint, invitation links are forwarded to the running
instance without touching disk, and local identity from earlier installations
continues to work. The new device directory makes hardware easy to search and
keeps stable integration identifiers close at hand.

The Windows package now combines the native Device Center with the existing
Python agent service. Device Center reports the service’s actual Windows state
and offers start or restart only when either action is useful. Closing or
quitting the client still leaves device work running in the background.

## inari@1.20.0-alpha.6

### Resume setup safely after an interruption

Inari Device Center now asks the local agent whether setup actually finished before opening the main window. Closing an invalid, failed, or interrupted invitation no longer skips first-time setup on the next launch. The assistant resumes at the saved step, offers a clean start-over path after a failure, and can finish setup before any devices are attached.

## inari@1.20.0-alpha.5

### Fix the Device Center icon on Windows

Device Center now keeps its intended transparent icon on the Windows taskbar
and Start menu instead of appearing inside a pale system-generated square.

Windows releases now publish provenance for every included file and bind the
installer to its SPDX SBOM with a GitHub attestation.

## inari@1.20.0-alpha.4

### Fix Windows installation and first launch

App Installer now presents a single, clear installation action. The Windows package also carries the TLS runtime that matches its embedded Python interpreter, preventing Device Center from failing on first launch.

## inari@1.20.0-alpha.3

### Keep published artifacts immutable

Completed release plans are now retired before another version is prepared, so
later changes can never rebuild an already published Device Center version.

### Fix Windows publisher trust

Windows installation now deploys the complete Inari signing chain and verifies
the MSIX through the same machine certificate stores used by App Installer.
The installation guide includes a direct recovery path when Windows shows an
unknown publisher.

## inari@1.20.0-alpha.2

### Introduce Inari Device Center for Windows

The first Windows distribution packages Device Center as the user-session tray application and the Inari agent as its own delayed-start service. The signed MSIX includes protocol activation, protected local pairing, native credential storage, canonical brand assets, checksums, an SBOM, and installation guidance for managed environments.

### Add recoverable Windows publication

Tegami now versions the complete edge distribution as one synchronized release. Signed Windows artifacts attach to the corresponding GitHub release with checksums and provenance, and interrupted uploads can safely resume from verified remote state.

### Refresh the security baseline

The edge distribution now ships with patched releases of its authentication, cryptography, HTTP, configuration, and internationalized-domain dependencies. The release test toolchain also uses the corrected temporary-directory handling in Pytest 9.

### Establish the Windows publisher identity

Inari Device Center packages now carry Pablo Hernández Jiménez as their
publisher identity. A publisher-owned code-signing root delegates to a
project-scoped Inari issuing authority, giving managed Windows deployments a
clear and truthful trust boundary without coupling the root identity to one
application.
