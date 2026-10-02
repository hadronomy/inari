# Inari device integration

Inari connects business applications to local devices through an Agent and a
Controller. This glossary defines the language for the Odoo integration.

## System boundaries

**Agent**:
The long-running Inari service that owns local devices and executes device
work. Local device work can continue without a Controller connection.
_Avoid_: IoT Box, hardware proxy, daemon

**Controller**:
The central Inari service that owns organizations, sites, policy, enrollment,
security audit records, and managed device work.
_Avoid_: Odoo server, gateway

**Device**:
A physical peripheral that an Agent identifies with a stable `device_id`.
_Avoid_: printer name, device IP

**Driver**:
The Agent component that implements the protocol of a device.
_Avoid_: adapter

**Driver Profile**:
An immutable contract that records safe physical behavior and certification
facts for one Driver and Device combination.
_Avoid_: printer settings, raw options

**Device Adapter**:
An application boundary that converts an Odoo device action to an Inari
contract while preserving the Odoo workflow.
_Avoid_: driver, IoT Box emulator

**Platform Backend**:
The operating-system-specific implementation that submits prepared output to
Windows or CUPS and reports its platform identity and evidence.
_Avoid_: Device Adapter, Driver Adapter

**Device Work**:
One requested device action, reading, or output that an application submits
through an Inari contract.
_Avoid_: command, payload, print job

**Device Capability**:
An Inari declaration that a Device and Driver can perform one class of Device
Work.
_Avoid_: feature, device type

**Device Health**:
The current Driver observation of Device readiness, with a stable state and
reason.
_Avoid_: connection state, device status

**Capability Negotiation**:
The comparison between required Device Work and a Driver Device Capability
contract.
_Avoid_: device detection, automatic downgrade

**Device Binding**:
An Odoo record that assigns an Inari Device to one Binding Scope and Device
Purpose.
_Avoid_: device mapping, printer IP

**Binding Scope**:
The exact POS configuration or Site in which a Device Binding can authorize
Device Work.
_Avoid_: global printer, default scope

**Report Binding**:
An Odoo record that assigns one report action to an active Device Binding
Revision and one Report Route.
_Avoid_: report printer, browser printer choice

**Report Route**:
The manual or automatic Odoo path that resolves one Site and selects one Report
Binding before it creates a Print Intent.
_Avoid_: print mode, fallback

**Device Purpose**:
The Odoo workflow role that a Device Binding assigns to one Device Capability.
_Avoid_: device type, use case

**Binding Revision**:
An immutable version of a logical Device Binding that preserves its Device,
Device Capability, Device Purpose, and Binding Scope for existing Device Work
and audit records.
_Avoid_: binding edit, configuration version

**Authorization Digest**:
The stable digest of the Client Grant permissions that authorize one local
browser transport context.
_Avoid_: token digest, Binding Revision

**Organization**:
The top-level Inari tenant boundary that corresponds to one Odoo company.
_Avoid_: account, customer, company

**Site**:
A physical store or warehouse within one Organization.
_Avoid_: location, branch

**Site Resolution**:
The deterministic assignment of one Odoo business source to exactly one Site
before managed report or label work starts.
_Avoid_: default Site, nearest Site, Site guess

**Agent Host**:
A computer that runs an Agent and can reach its assigned Devices. An Agent Host
can be a POS workstation or a shared edge computer.
_Avoid_: IoT Box, POS server

**Client Grant**:
The permission set that lets one operator session use specified Agent
capabilities.
_Avoid_: local token, shared credential

**Client Pairing**:
The approved association between one browser key, one Odoo origin, one POS
configuration, and its permitted Client Grants.
_Avoid_: login, token exchange

**Pairing Request**:
A time-limited request for a Device Manager to approve a Client Pairing.
_Avoid_: pending login, approval token

**Pairing Assertion**:
A signed Odoo statement that binds one Pairing Request to its browser key,
target Agent, database, company, Organization, Site, POS configuration, actor,
role, scopes, and expiry.
_Avoid_: pairing secret, browser claim

**Agent Endpoint**:
The authenticated local HTTPS address through which an operator session reaches
an Agent.
_Avoid_: Agent URL, device IP

**Policy Snapshot**:
The last Controller-approved Organization rules that an Agent stores for
bounded offline decisions.
_Avoid_: offline policy, cached permissions

**Organization Workload Identity**:
The identity that one Odoo company uses for Managed Device Work in its Inari
Organization.
_Avoid_: database service account, shared workload identity

**Local Agent Interface**:
The versioned HTTPS Interface that an operator session uses for Local Device
Work, state queries, and event subscriptions on one Agent.
_Avoid_: hardware proxy, Workload Interface

**Managed Workload Interface**:
The versioned HTTPS Interface that Odoo uses for Managed Device Work,
reconciliation, Projections, and webhook registration on the Controller.
_Avoid_: Local Agent Interface, Zenoh Interface

**Projection**:
An Odoo read-only record of Inari-owned data whose authority remains in the
Controller or Agent. This includes identity, capability, and Managed Work
state.
_Avoid_: copy, mirror, cache

**Shared Device**:
A Device that more than one POS configuration can use safely through one
Agent queue.
_Avoid_: common device, default device

**Exclusive Device**:
A Device that only one active POS configuration can use at a time.
_Avoid_: dedicated device, locked device

## Work paths

**Managed Work**:
The Controller record for one idempotent Managed Device Work submission before
and through Agent acceptance. It owns the deadline and links to the Print Job
after acceptance.
_Avoid_: print job, Controller command

**Controller Admission**:
The durable Controller event that creates one Managed Work record, assigns its
deadline, and accepts responsibility for dispatch.
_Avoid_: Agent Accepted, HTTP receipt

**Dispatch Envelope**:
The Controller-issued record that carries one accepted Managed Work item to its
exact target Agent.
_Avoid_: command, transport message, payload

**Local Device Work**:
Device work that an operator session submits to a reachable Agent. This work
does not depend on a Controller connection.
_Avoid_: browser printing, POS path

**Managed Device Work**:
Device work that a business application submits through the Controller to an
Agent.
_Avoid_: remote print, server path

**Pending Agent**:
Managed Device Work that the Controller stored but its target Agent has not
accepted. A Print Job does not exist at this stage.
_Avoid_: accepted, queued print

**Dispatching Managed Work**:
Managed Work whose Dispatch Envelope can reach the target Agent. A Print Job
does not exist until the Agent accepts it.
_Avoid_: in progress, printed

**Accepted Managed Work**:
Managed Work that links to one Agent-accepted Print Job.
_Avoid_: output confirmed, completed

**Rejected Managed Work**:
Managed Work that the Controller or Agent rejected before Agent acceptance.
_Avoid_: failed Print Job, canceled

**Canceled Managed Work**:
Managed Work with signed proof that Agent acceptance did not occur before
cancellation.
_Avoid_: canceled Print Job, recovery uncertain

**Expired Managed Work**:
Managed Work whose Controller deadline passed without timely Agent acceptance.
_Avoid_: expired Print Job, failed

**Managed Payload**:
The encrypted content that Pending Agent work needs until an Agent accepts,
cancels, rejects, expires, or enters Recovery Uncertain.
_Avoid_: controller command, print audit record

**Backup Exposure Window**:
The retention period in which matching PostgreSQL and OpenBao backups can
recover a deleted Managed Payload.
_Avoid_: live retention, payload lifetime

**Recovery Fence**:
The Controller state that stops managed submission and dispatch during backup
or recovery while Local Device Work continues.
_Avoid_: maintenance mode, Controller outage

**Admission Epoch**:
The monotonic value that orders Managed Work acceptance against a Recovery
Fence.
_Avoid_: Dispatch Epoch, request time

**Recovery Point**:
The approved identity of one fenced Controller recovery set: a matching
database backup, OpenBao snapshot, Release Set, schema revision, and Dispatch
Epoch.
_Avoid_: backup file, restore date

**Recovery Checkpoint**:
An immutable and signed WAL position that limits data loss between daily
Recovery Points.
_Avoid_: Recovery Point, database backup

**Dispatch Epoch**:
The monotonic identity that lets an Agent reject managed dispatch from an old
or recovered Controller authority.
_Avoid_: Controller version, session ID

**Recovery Uncertain**:
Post-dispatch Managed Work whose Agent acceptance cannot be proved. Recovery
or a live cancellation race can produce this state. The Controller cannot
dispatch this work again.
_Avoid_: pending agent, failed

**Recovery Canary**:
Signed test Device Work that proves one active operation after Controller
recovery without using business content.
_Avoid_: live order, smoke test

**Agent Quarantine**:
The permanent exclusion of an untrusted Agent identity after physical inventory
and duplicate-output review.
_Avoid_: Agent deletion, automatic failover

**Rollback Anchor**:
TPM-backed Agent state that detects restoration of an older local database or
Device Spool.
_Avoid_: backup marker, Agent Boot Identity

**Native Device Path**:
An Odoo device integration that stays active because Inari does not own its
contract.
_Avoid_: unsupported workaround

**Workflow Compatibility**:
The promise that an existing Odoo workflow remains usable for a supported
Conformance Target. Inari handles only Devices with a tested Device Adapter.
_Avoid_: full IoT parity, all-device support

## Print lifecycle

**Print Intent**:
An Odoo request for one physical copy from one Print Origin and Device Purpose.
It keeps the same identity across retries.
_Avoid_: print request, print attempt

**Origin Submission Key**:
The stable identity that prevents two initial submissions from allocating
different Print Intents for the same Print Origin and target.
_Avoid_: Print Intent, Copy Ordinal, retry key

**Print Origin**:
The discriminated and immutable Odoo business context that identifies a POS or
report source for one Print Intent.
_Avoid_: source string, audit note

**Submission Context**:
The immutable POS context created before rendering that binds one device action
to its Print Origin, Binding Revision, actor, and copy ordinal.
_Avoid_: mutable order state, printer options

**Copy Ordinal**:
The number assigned to one physical copy within a document request.
_Avoid_: retry count, print count

**Drawer Intent**:
An Odoo request for one authorized physical cash-drawer opening. A Drawer
Intent keeps the same identity across retries.
_Avoid_: drawer command, drawer pulse

**Print Job**:
The durable Inari execution record for a Print Intent.
_Avoid_: print intent, receipt

**Idempotency Key**:
The stable and opaque identity that binds repeated submissions to one Print
Intent and one Print Job.
_Avoid_: job ID, retry ID

**Payload Fingerprint**:
The content-free digest that proves whether two submissions with one
Idempotency Key contain the same Device Work.
_Avoid_: content hash, idempotency key

**Platform Job Identity**:
The operating-system print identity that combines the spooler or server,
printer queue, job value, and submission time.
_Avoid_: print job, spooler ID

**Device Spool**:
The Agent-controlled storage for encrypted Device Work artifacts before
platform submission and during execution.
_Avoid_: platform spool, SQLite payload, temporary directory

**Spool Reservation**:
The durable Device Spool capacity that an Agent reserves before it accepts
content-bearing Device Work.
_Avoid_: free disk, queue bytes

**Receipt Payload**:
The document content that a Print Job needs to produce a receipt.
_Avoid_: audit record, job metadata

**Receipt Image**:
An Odoo-rendered JPEG that the Document Imaging Module converts for a receipt
printer.
_Avoid_: HTML receipt, raw receipt

**Report PDF**:
An Odoo-rendered PDF that the Document Imaging Module checks and prepares for
a tested Platform Backend.
_Avoid_: report URL, browser download

**Label Document**:
One validated printer-language envelope for one physical label, with a declared
language, media size, and DPI.
_Avoid_: raw bytes, text report

**Print Audit Record**:
The content-free history of a Print Intent, its target Device, its state, and
its operator context and recovery actions.
_Avoid_: receipt payload, print job

**Agent State Envelope**:
An Agent-signed record of authoritative Print Job state, Agent Boot Identity,
Dispatch Epoch, sequence, and Output Evidence.
_Avoid_: browser audit, event payload

**Agent Boot Identity**:
The unique identity of one Agent runtime start. It prevents a sequence from a
prior runtime from becoming current again.
_Avoid_: process ID, Agent ID

**Subscription Identity**:
The unique identity that binds ephemeral Device events to one authenticated
subscription.
_Avoid_: stream ID, browser tab ID

**Print Recovery Reason**:
A structured operator explanation for a Reprint or another print-recovery
action.
_Avoid_: comment, note, error message

**Retry**:
A repeated submission of the same Print Intent. A Retry must refer to the same
Print Job and must not request another physical copy.
_Avoid_: reprint

**Reprint**:
A new Print Intent that requests another physical copy after an operator
decision.
_Avoid_: retry

**Reprint Approval**:
One time-limited Device Manager authorization for one exact Reprint and its
duplicate-output risk.
_Avoid_: print permission, standing approval, post-hoc approval

**Accepted**:
The Agent stored the Print Job for execution. The state includes preparation
before Device I/O and does not prove physical output.
_Avoid_: printed, complete

**In Progress**:
The Agent started Device I/O for the Print Job and has no final outcome.
_Avoid_: dispatched, running, printing

**Output Confirmed**:
The Driver met its declared completion contract for the Print Job. Output
Evidence identifies the strength of that result.
_Avoid_: accepted, exactly once

**Output Evidence**:
The Driver classification of the strongest proof for completed Device Work.
The levels are device, spooler, and transport.
_Avoid_: print status, completion guarantee

**Outcome Unknown**:
The system cannot prove physical completion or failure for a Print Job after
Device I/O began.
_Avoid_: failed, printed

**Failed**:
The Agent proved that a Print Job did not complete successfully.
_Avoid_: outcome unknown, canceled

**Canceled**:
A Print Job state that means the Agent stopped work before Device execution
started.
_Avoid_: failed, expired

**Expired**:
A Print Job state that means the Agent did not start Device I/O before its
execution deadline. An Expired Print Job cannot start without a Reprint.
_Avoid_: failed, canceled, delayed

**Job Reconciliation**:
The recovery of current Print Job state from the Agent after event loss,
reconnection, or an uncertain response. It can update state without changing
audit history.
_Avoid_: retry, resubmit

**Contract Major**:
The compatibility identity that changes when an Inari Interface makes a
breaking change.
_Avoid_: addon version, protocol revision

## Device use

**Device Preflight**:
The POS readiness view for required Device Bindings, Agent trust, connection
state, and contract compatibility.
_Avoid_: health check, connection popup

**Needs Attention**:
A blocked integration state for a Binding Revision or Report Binding that no
longer satisfies its active contract.
_Avoid_: disconnected, inactive, failed

**Device Test**:
A Device Manager action that sends standard non-business Device Work before a
Device Binding enters use.
_Avoid_: test print, live order

**Device Test Result**:
An immutable record of one Device Test, its tested Binding Revision, checks,
evidence, outcome, and actor.
_Avoid_: latest test fields, device status

**Failed Environment**:
A Device Test outcome that means the surrounding conditions prevented a valid
result. It does not prove a contract error.
_Avoid_: unsupported device, failed contract

**Failed Contract**:
A Device Test outcome that proves the tested Device Capability does not meet
its Driver Profile or Device Purpose.
_Avoid_: temporary error, failed environment

**Certified Scale**:
A scale and Driver combination that has approval for price calculations in the
operating jurisdiction.
_Avoid_: supported scale, connected scale

**Scale Reading**:
An exact, sequenced measurement from a scale. It includes the unit, resolution,
stability, range state, observation time, and certification identity.
_Avoid_: floating-point weight, manual weight

**Barcode Source**:
The one active input path that sends barcode values to a POS configuration.
_Avoid_: scanner device, barcode reader

**Barcode Event**:
A sequenced barcode value that an Agent scanner sends through the Device
Adapter.
_Avoid_: keystroke, barcode log

**Certification Record**:
Controller-owned evidence that a scale and Driver combination has approval for
price calculations in one jurisdiction.
_Avoid_: scale setting, device support

**Hardware Certification Matrix**:
The approved set of exact Device, firmware, Driver, Platform Backend,
connection, media, and operating-system combinations.
_Avoid_: generic device support, protocol-family support

**Manual Weight**:
A weight that an operator enters through an authorized Odoo workflow without a
Certified Scale reading.
_Avoid_: scale reading, estimated weight

**Transport Leader**:
The one browser context that owns an Agent event stream for one Client Pairing,
Agent, POS configuration, and Client Grant authorization digest.
_Avoid_: primary tab, active POS

**Transport Leader Lease**:
The Agent-issued, time-limited right for one Transport Leader to own the event
stream for an exact Client Pairing, Agent, POS configuration, and Authorization
Digest.
_Avoid_: IndexedDB lock, BroadcastChannel election

**Scale Lease**:
The Agent-owned, time-limited right for one active POS scale screen to use an
Exclusive Device.
_Avoid_: browser lock, transport leader

## Operator roles

**Device Operator**:
An Odoo user who can use assigned Devices and recover permitted Device Work.
_Avoid_: cashier, device user

**Device Manager**:
An Odoo user who can pair, bind, diagnose, and do tests of Devices within
permitted Organizations and Sites.
_Avoid_: technician, POS manager

**System Administrator**:
An Odoo user who owns workload identity and system-wide integration policy.
_Avoid_: device manager, superuser

**Agent Administrator**:
An operating-system-authorized person who can repair protected Agent Host
storage through Device Center.
_Avoid_: system administrator, device manager

## Product

**Inari Odoo Addon**:
The reusable and unbranded Odoo product that provides Device Bindings, Device
Adapters, operator controls, and device-work status.
_Avoid_: MZE IoT addon, Odoo IoT clone

**Inari Stock Addon**:
The optional Odoo addon that connects stock report events to active Report
Bindings through the Inari Odoo Addon.
_Avoid_: stock printer patch, required stock dependency

**Decommission**:
The controlled removal of the Inari Odoo Addon after it removes the Inari Stock
Addon and drains Device Work. It resolves unknown outcomes, exports audit
records, revokes Client Grants, and purges Device Spool content.
_Avoid_: uninstall, disable addon

**Decommission Run**:
The durable record that tracks one Decommission scope, phase, actor, evidence,
failure, and final result.
_Avoid_: removal task, uninstall state, cleanup job

**Conformance Target**:
The Odoo edition and feature surface for which Inari workflows and Device
Adapters pass the contract suite.
_Avoid_: Enterprise parity, module dependency

**Diagnostics Bundle**:
A content-free support record of integration topology, contracts, device
state, and recent operational errors.
_Avoid_: log dump, receipt archive

**Release Set**:
The approved identity of all artifacts, contracts, schema revisions, and
assets that enter production together.
_Avoid_: addon version, deployment tag

**Release Readiness**:
The authenticated proof that Odoo loaded the Release Set, database revision,
required modules, and POS assets that the deployment expects.
_Avoid_: HTTP health, Pod readiness

**POS Drain**:
The maintenance state that lets active payments finish, blocks new payment
validation, and waits for paid unsynchronized orders before traffic stops.
_Avoid_: POS session close, maintenance page

**Document Imaging Module**:
The deep Agent Module that checks and prepares Receipt Images, Report PDFs,
and Label Documents before a Driver receives a physical artifact.
_Avoid_: printer driver, report renderer

**Compliance Export**:
A content-free package of audit records for retention outside Odoo.
_Avoid_: legal hold, database backup

**Compliance Key**:
The database-scoped signing identity that proves the source of a Compliance
Export.
_Avoid_: workload credential, artifact signing key
