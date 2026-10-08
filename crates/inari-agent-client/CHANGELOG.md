## inari-agent-client@1.20.0-alpha.15

### Show Spanish test receipt instructions

Odoo now loads the Spanish test receipt dialog, browser approval instructions,
and printer selection errors. The connection button also uses translated text.

### Open the Odoo test receipt dialog

The Test receipts button now opens the dialog without an Owl error. The selected
printer stays selected when Odoo supplies numeric Device record IDs.

### Open browser approval from test receipts

Test receipts now shows a link to Device Center and the Pairing Request ID. The dialog explains how to review the request, compare the phrase, and approve the browser on the computer connected to the printer.

## inari-agent-client@1.20.0-alpha.14

### Preserve Windows print queue completion evidence

Complete Windows RAW receipt and label submissions now report confirmed spooler
evidence. The Agent ledger and Controller preserve the reported evidence level.
This result means that Windows accepted the complete document into its print
queue. It does not prove physical output.

Deploy the matching Controller image from the same Release Set with this Agent.

Partial writes, failed completion calls, and worker timeouts retain an unknown
outcome. They do not trigger another physical submission.

### Honor the Device selection in native Setup

The Agent publishes only the Devices selected for the current Controller.
Unselected Devices remain available in the local catalog. New Controller Device
actions and Managed Device Work require the current selection. Accepted retries
retain their result after selection changes. A new invitation clears the selection.

### Finish Agent restarts after a reset connection

The Agent limits its wait for HTTP connections during shutdown to five seconds.
A reset Windows connection can no longer block the runtime restart indefinitely.
The Agent completes application cleanup before it starts the next runtime.

### Keep the Setup review and actions reachable

Setup now scrolls the full invitation review at the minimum window size.
Short forms stay centered. The review labels identify the Controller and its
address before you connect this computer.

### Publish selected Devices to the Controller

The Agent publishes transport and identity digests for selected Devices.
The Controller maintains each Agent's Device Projection from validated snapshots.
Older snapshots and exact retries cannot replace current Device observations.
Separate Agents can use the same host-local Device identifier without a collision.
Credential retirement clears the Agent's Projection.
Discovery does not grant Managed Device Work capabilities.

Upgrade every Agent before deploying the Controller.
Older Agents lack required inventory fields and cannot enroll or publish snapshots
to the new Controller.

Stop old Controller replicas before the Device schema migration.
The Helm migration hook runs before the Deployment update.
Keep old replicas at zero until the migration completes.
The migration clears derived Device rows and preserves publication history.
A rollback requires a matching Recovery Point and Release Set.

### Keep Device Center connected to the Agent

Device Center uses the authenticated native event stream for live Device and
Device Work updates. The stream keeps the trusted local HTTPS connection and
checks authentication throughout the connection. Browser Device Streams retain
their signed, scoped contract.

### Keep Device Center connected after secure setup

Device Center selects its TLS provider for the local event stream and uses the
operating system's certificate verifier. A build with multiple TLS providers
can connect without a process-wide provider setting.

Overview no longer shows a successful setup message as an Agent connection
error.

### Keep small Windows icons transparent

The smallest Windows icons now keep transparent corners. Each executable icon
size uses the same rendering as the package assets.

### Print the complete diagnostic receipt

The receipt diagnostic now prints the saved pattern with Spanish accents,
Code 128, a QR code, feed, and partial cut. Live Controller commands reach the
Agent without a restart. The diagnostic does not activate a Device Binding.

Receipt images are centered within the print width. The print command advances
the complete receipt to the cutter before the partial cut.

### Execute receipt Device Tests through the Local Agent Interface

Device Managers can send the fixed receipt pattern through an approved Device
and Binding Revision. The Agent records one execution per Test ID and signs
the result after text, accents, Code 128, QR, and feed-and-cut checks.

Retries return the existing result. Uncertain output and Agent restarts do not
send another receipt. A passed result remains separate from Binding activation.
The Windows Agent package includes the fixed pattern.

When the worker cannot stop, the Device stays reserved until Agent Administrator
repair.

The reservation covers worker preparation, including preparation that reaches
the execution deadline. An expired Test cannot start Device I/O. A repeated
execution cannot start another worker for the same Test.

Test IDs remain addressable by the query and physical-answer routes.
Both OpenAPI contracts declare the required `Idempotency-Key` header.

### Show the current Agent connection at startup

Device Center now shows the current Agent connection when its window opens.
A fast local connection no longer leaves the window at Opening the local
connection. If the event stream closes, Device Center shows Reconnecting
while it waits for the next connection attempt.

### Start isolated printer workers in the Windows package

The packaged Agent starts its printer workers through the frozen executable.
Device Tests and queued receipts can reach the printer without blocking the
Agent during worker startup. Windows builds now verify this startup path with
synthetic content and no Device I/O permit.

### Pair browsers through the configured HTTPS Agent Endpoint

Browser pairing and Device Work now use the configured HTTPS hostname when
the local socket address is an IP. The Agent retains the Host, listener port,
TLS scheme, and DPoP checks.

Odoo can read the DPoP challenge headers and retry with the required nonce.

### Connect the Odoo Devices list to the Controller

Administrators can connect one Odoo company to its Controller Organization and
synchronize its Sites, Agents, and Devices. Scheduled synchronization preserves
the last complete inventory if authentication or the Controller fails. Device
discovery does not activate a receipt binding or replace a Device Test.

### Show Odoo receipts in Device Center Activity

Activity now includes receipt Print Jobs from the protected Agent queue.
It shows the Device, Odoo source, current state, and Output Evidence.
Receipt history returns after reconnection and updates while the window stays open.
Outcome Unknown asks the operator to check physical output before a Reprint.
The history contains no receipt content.

Upgrade the Agent and Device Center together for the native monitor contract.

### Accept receipts after a signed authority update

Receipt admission now accepts unchanged signed device records that a later
authority bundle retains. It checks each record digest and binding activation
against the signed manifest. Records withdrawn by a later bundle cannot
authorize receipt output.

### Print both test receipts from Odoo

Device Managers can open Test receipts from the Devices toolbar, a printer row,
or the printer page. Choose the INARI check, the full MIZONA sample, or both.
The dialog shows the target printer, secure pairing, and each job's result.
Pending jobs keep their identity when the dialog reopens. Test prints also
appear in Device Center Activity.
Completed tests can be cleared to start a new test. An unknown result needs a
physical printer check first. Preparation printers use their authorized scope.

## inari-agent-client@1.20.0-alpha.13

### Restore pairing between Device Center and the agent

Device Center identifies itself to the agent over the pairing pipe by the token the pipe hands back, rather than by opening its own process. The agent runs as LocalService and holds no rights over a process owned by the signed-in user, so the old check was refused before it could be answered and every installation reported the agent as unreachable while it was running normally.

### Report the real Windows service state

The agent service state now comes from the Service Control Manager instead of the English words in `sc.exe` output. On a Windows installed in another language a running service read as stopped, and Support offered to start a service that had never stopped.

### Build the Windows release on any signed host

The release build resolves the HLSL shader compiler from the installed Windows kits rather than depending on one hardcoded SDK version, freezes the agent against a system Python, and rejects payload binaries whose signature table points past the end of the file. Windows rejects such a package as a whole and names no file, so the build now names it first.

### Polish the shell: hover, alerts, and the titlebar

Hovering now eases instead of snapping. Every wash the Device Center paints —
rail items, device rows, attention rows, the health chip, and the window
caption buttons — fades over 150 ms, the duration the web and the desktops
around it have trained everyone to expect. Moving the pointer fast across the
interface leaves no chunky trail: each wash is a pure function of the clock,
so a dropped frame lands on the exact right position instead of losing
motion, and reversing mid-fade continues from wherever the pointer left it.

The alert's pixel cascade now meets the card's rounded corners the way the
card's own background does. The wall used to drop whole cells around the arc,
which cut a second, coarser corner into the surface; it now fades each cell by
how much of it the corner covers, and the end rows of a full-bleed list carry
the card's curve so a hovered or selected row can no longer square the corners
off the card holding it.

The brand mark in the titlebar starts on the rail's own inset on Windows and
Linux, so it sits on the same vertical line as the navigation below it instead
of pressing against the window edge.

### Dragging no longer crashes the application

Dragging any window could take the whole application down. The caption press
was answered with a synchronous system call that started the move loop inside
the event dispatch; the loop then delivered a pending task into an app that
was mid-borrow, and the borrow won. The press is now queued for the next
turn of the event loop, so the move starts on a clean stack. Panics also
reach the log file now, with their message and location, instead of
vanishing into a windowless process.

### Scrollbars you can see and grab

Every scrolling surface — the five screens, the dev previews, and the
enrollment window — now carries an overlay scrollbar: a thumb-only bar inset
from the panel edge, shown while scrolling or hovering and faded after idle,
draggable, widening on hover, and honouring the OS auto-hide preference. The
scroll position also survives re-renders, where before a page reset could
lose it.

### The gate: the path carries its state

The Overview gate's connectors are live wires now. While traffic passes,
discrete packets of light travel the line from this computer, through the
agent, out to the devices, flanked above and below by pixel sine traces at
different frequencies and amplitudes — an information field rippling around
the path, in the same staggered phase language as the alert's cascade. On
a caution tone the traffic corrupts: travelling tears cross the wire —
cells drop into dead air, packets lurch, the flanking traces jump out of
phase and flash the danger tone — a link that needs attention, not a
broken one. When the path fails, the wire goes still, the
cross speaks the danger tone, and one last packet leaves the source and dies
at the break: the attempt the wire made before it went down. With motion
off, the wire reads as the same path at rest.

### The credential field

The invitation field is a purpose-built instrument now, not a bordered
rectangle. Its edge rests on the hairline, warms to the vermilion while the
field holds focus, and a soft accent ring arrives over the same 150 ms every
other wash uses. A link that parses earns a quiet check on the trailing edge
— positive feedback only, never a scolding mid-type. A submitted link that
fails on the text hands the edge and the fill to the danger tone, eased in
like every other state, while the banner above carries the words; a network
failure with a well-formed link leaves the field alone. Enter submits from
the field, and the review action stays disabled while the field is empty.

### Support hands over the facts instead of displaying them

Support exists for a moment that is going badly, and the question is always
the same: what version, what address, what did it actually say. The screen
used to answer those and stop there, leaving the operator to retype an
endpoint and three lines of error text into a ticket out of a window they
could not select from.

Technical details is now a readout where every fact is one press from the
clipboard, and the whole set is one more. Pointing at a row lights it and
offers a copy; pressing it copies that value and marks the row with a tick
that leaves on its own. "Copy all details" puts the lot on the clipboard as
aligned plain text, stamped with the moment it was collected, so it pastes
into a ticket or a chat window already readable. The wrap points that keep a
long URL inside its card are painted and never copied — a link pasted into a
browser resolves.

Two facts joined the ones already there. Support reports the operating system
and architecture the build is running on, and the full path to the log folder:
the button beside it is the faster route for the person at the keyboard, and
the path is the only route for the administrator who is not.

The card itself reads as an instrument now. Labels sit in a fixed column so
every value starts on one edge, labels stay in the reading face while values
are set in the technical one, and nothing is ruled between the rows — the
pointer marks the row instead.

### A new technical face: Departure Mono

Every monospaced string in the application — identifiers, endpoints, paths,
and diagnostics — is now set in Departure Mono, embedded in the application
rather than borrowed from the system. The readouts look the same on Windows,
macOS, and Linux instead of inheriting three different faces.

It is a pixel face, so its size is not a free choice: the outlines sit on a
grid that lands on whole device pixels only at multiples of 11px, where the
advance measures exactly 7px and the cap exactly 8px. At that size it reads at
the same optical scale as the system mono it replaces, so the readouts got
sharper rather than smaller.

### Buttons that answer the pointer

Buttons are the application's own now. Their fill eases over the same 150 ms
as every other surface in the window instead of snapping in one frame, they
carry the pointer cursor, and their label warms as the pointer arrives. The
material matches the credential field's — a fill, an edge, and a pixel of
light along the top lip — and the fill is the only thing that moves: no lift,
no scale, and no shadow that a translucent window would show through as an
inner glow.

### Buttons that change their mind, in front of you

A copy button that answers with a tick was landing that tick in a single
frame — the change was over before the eye that caused it arrived. The glyph
and the label now cross over: the mark being replaced shrinks and fades while
the one arriving grows into its place, and the words leave upward and come back
from below.

The control never changes size doing it. The resting label holds the width, so
"Copy all details" becoming "Copied" leaves the two buttons beside it exactly
where they were, and the glyph stays beside its own word rather than stranded
across the gap the shorter label would open.

Both halves run on one duration, so the button reads as one thing changing its
mind rather than a mark and a word on separate clocks. Reversing halfway —
a second copy landing while the tick is still leaving — continues from where it
had got to instead of starting over.

### Connect Device Center to the Agent's HTTPS listener

Windows Device Center now reads the protected Agent Endpoint before it connects.
HTTPS requests and event subscriptions use the operating system's certificate
trust. Pairing secrets retain their separate, authenticated bootstrap operation.

Unpackaged Windows development runs use loopback bootstrap. Non-TLS discovery
uses the configured loopback host and port, including IPv6 listeners.

Before upgrading a Windows Agent with TLS, set `[api].endpoint` to its
certificate hostname and listener port. See the Windows installation guide.

### Approve Odoo browser access in Device Center

Open an Odoo Pairing Request link or paste its ID in Client Pairing. Review the
matching phrase, Odoo origin, Agent Endpoint, business scope, and requested
permissions before approving or denying access. The screen keeps the request
visible while it saves a decision and explains expired and completed requests.

Enrollment links continue to open the separate Enrollment window. Windows
activation sends Client Pairing links to the review screen in the existing app.

### Connect Device Center to the local HTTPS Agent

Device Center uses loopback for local authentication and event streams. It keeps the Agent Endpoint hostname for TLS verification. LAN and overlay DNS no longer cause local authentication failures.

### Keep Linux controls visible during transitions

Linux draws control labels and icons without blur. The pinned renderer cannot
draw captured content, so these controls now keep their original content.

### Build signed Windows candidates before release

The release workflow can build a Windows candidate from a selected branch.
The candidate uses the protected signing environment and includes provenance,
checksums, and an SBOM. It does not publish packages or change release branches.

### Upgrade installed Windows candidates

Signed branch candidates can use a higher alpha sequence for an installed candidate upgrade. The sequence cannot precede the pending release version. Stable packages keep their independent versions.

### Bind enrollment certificates to the Agent Identity

Enrollment rejects Agent identifiers and certificate requests that do not match
the protected Ed25519 identity. Certificate issuance and renewal reject incorrect
subjects, missing SANs, additional names, and duplicate names.
The Agent validates the full certificate chain against its pinned CA root and
stores the verified intermediate chain for mutual TLS. A CA response cannot
replace the pinned root.
Root bootstrap accepts exactly one pinned CA. Certificate requests trust only
that CA and require client key usage for TLS signatures. The Controller requires
a SHA-256 CA pin in step-ca mode.

The chart now keeps enrollment and Zenoh disabled by default. Provision the CA
and exact per-Agent Router policy before enabling them.

Certificate names now come from the Agent Identity. Remove
`managedGateway.certificate.stepCa.authorizedSans` from Helm values and
`step_ca_authorized_sans` from a Controller configuration before upgrading an
existing step-ca deployment.

Installed certificates now receive the same trust and identity checks before use.
The Agent repairs a cached CA against its pin and replaces obsolete certificate
names when fresh enrollment provides a one-time token. The protected key stays
unchanged. All CA endpoints require HTTPS.

Managed publication and Device Work now stop when certificate validation fails.
The Agent closes an existing session and reports the certificate error in setup.
Controller HTTPS uses separate trust before enrollment. A new invitation replaces
cached enrollment and old configured credentials without replacing the Agent key.
Concurrent enrollment calls share one request and preserve newer invitations.

Root bootstrap reads Smallstep's JSON response. Issuance and renewal use the
canonical Smallstep endpoints and retain the complete intermediate chain.

Upgrade the Agent and Controller together for gateway protocol `2026-10-03`
before enabling enrollment. Enrollment no longer sends an installed certificate,
and the Controller migration removes its unused stored certificate column.

The Controller migration removes an Agent column that old replicas still select.
Keep managed enrollment and Zenoh disabled until all Controller replicas use the
new release. A binary rollback across this migration is not supported. Recover
with the matching database backup and Release Set.

### Keep concurrent Controller documents responsive

The Controller discovers its fixed web routes once before it serves documents.
Concurrent Router construction can no longer interrupt Resource loading in an
active document stream.

### Continue setup after an Agent restart

Device Center now provides a Restart Agent action when an invitation needs a
runtime restart. On Windows, the service host applies the saved setup through a
package-verified local request. Standard users need no service-control permission
for this action. Device Center then shows connection progress and Device selection.
Failed restarts remain available for retry. A connection check that stops offers
Check again.
Native service requests stop after ten seconds if the service does not reply.

Setup keeps Device access blocked until the Agent reports completion.
Setup completion refreshes the operations window and tray. Closing setup hides
the window, and the tray restores it with its current progress. Service actions
wait for completion and reject concurrent requests from another window.

### Explore components and inspect each window

Device Center includes the updated interface and brand assets. Debug builds
provide the component Bench and four inspector screens. Frame histories belong
to each window. Story controls stay in the Bench, and the outline preference
applies to all windows.

## inari-agent-client@1.20.0-alpha.12

### Preserve the configured OIDC issuer

The Controller keeps the exact OIDC issuer during configuration and discovery. Identity providers whose issuer omits a root trailing slash can start and authenticate without an issuer mismatch.

## inari-agent-client@1.20.0-alpha.11

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

## inari-agent-client@1.20.0-alpha.10

### Fix Windows service startup and window controls

The Windows agent now starts when the service has no attached console. Device Center also shows its window controls and supports title bar dragging.

## inari-agent-client@1.20.0-alpha.9

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

## inari-agent-client@1.20.0-alpha.8

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

## inari-agent-client@1.20.0-alpha.7

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
