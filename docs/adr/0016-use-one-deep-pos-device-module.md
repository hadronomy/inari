---
status: accepted
---

# Use one deep POS device module

One deep frontend Module owns Agent trust, transport, contract negotiation,
submission, events, and Job Reconciliation. Odoo workflow code does not call
the Agent Endpoint directly.

The Device Adapter Interface exposes `preflight(requirements)`,
`submit(device_work)`, and `subscribe(selection, listener)`.

This seam gives each Odoo workflow a small Interface while one implementation
contains the difficult browser and Agent behavior.

An `InariPrinter` Device Adapter extends Odoo `BasePrinter` for primary
receipts and preparation printers. A narrow `InariHardwareAdapter` serves bound
drawers, scales, and scanners.

The receipt caller creates an immutable Submission Context before rendering.
Narrow patches carry it through `PrinterService.printHtml()`,
`PosPrinterService.print()`, `PosPrinterService.printHtml()`, the receipt
caller, and `PosStore.printOrderChanges()`.

Each `InariPrinter` queue entry stores the rendered element and its exact
context together. IndexedDB stores content-free context by Print Origin,
preparation segment, Binding Revision, and Copy Ordinal. A reload or Retry
cannot change it.

`InariPrinter.printReceipt(element, submission_context)` queues one immutable
pair. Active Inari paths disable web-print fallback. An `inari` result branch
bypasses both native Retry popups and registers one persistent recovery task.

`InariPrinter` installs after POS data load and Odoo proxy connection
ordering. It installs only for an active Binding Revision. It remains active
when proxy flags are disabled.

The addon guards `HardwareProxy.connectToPrinter()` and printer-service resets.
It also guards the Epson assignment and reconnect print effect. These paths
cannot replace or drain an active `InariPrinter`.

Both Device Adapters use the deep frontend Module. The addon does not emulate
the Odoo `/hw_proxy` HTTP Interface.

The Local Agent Interface has versioned endpoints for preflight, submission,
single-job query, batch reconciliation, and events. Client Pairing uses a
separate privileged Interface.

The Transport Leader uses fetch-based SSE with DPoP for Agent events. Its
scope contains the Client Pairing, Agent, POS configuration, and Client Grant
authorization digest.

IndexedDB coordinates candidates, and BroadcastChannel carries hints. The Agent
issues a 10-second Transport Leader Lease. The leader renews it every three
seconds. A successor reconciles sequence before it processes input.

Other tabs receive content-free hints and use their own grants for actions.
Binary Device Work uses a canonical JSON envelope and a separate multipart
binary part.

The stream uses 15-second heartbeats and bounded jittered reconnection. Each
message carries the Transport Leader fencing generation. The Agent rejects a
stale holder and sends signed `lease_lost` evidence before teardown when the
transport permits it.

A new stream declares an immutable reconciliation high-water mark in `ready`.
The successor reconciles through that barrier before it processes later
events. A sequence gap uses the same barrier.

The envelope uses RFC 8785 JSON Canonicalization Scheme. HTTP errors use RFC
9457 Problem Details with stable, content-free fields.

`InariPrinter` joins the narrow `hardwareProxy.printer` and
`PosStore.createPrinter` seams. `InariHardwareAdapter` validates signed decimal
Scale Readings, owns the Agent scanner subscription, and awaits separate Drawer
Intents. Keyboard scanning stays native. Manual Weight has a separate
permission and audit record.

Barcode replay exists only during the active authenticated scanner
subscription and Transport Leader Lease. Lease expiry deletes unacknowledged
events. Scanner loss opens product search and recovery.

Scale gaps, lease loss, stale data, and Binding Revision changes invalidate a
reading. Two new stable readings are required before Odoo accepts weight.

A Drawer Intent keeps its idempotency record for 90 days. An I/O timeout ends
as `outcome_unknown`. Another opening requires Device Manager approval and a
new Drawer Intent.

The native `RetryPrintPopup` remains available only for Native Device Paths.
The Inari recovery panel persists across receipt navigation and an unawaited
`afterOrderValidation()` promise. Odoo updates its receipt counter only after
Agent acceptance.

Preparation state is per order, segment, Device, and Print Intent. Partial
success never marks an unresolved target as printed. An accepted counter ledger
repairs failed Odoo counter writes exactly once.

Versioned Python protocol models publish OpenAPI 3.1, JSON Schema, generated
client, and contract-fixture artifacts.

CI regenerates every contract artifact and fails on drift. A compatibility
comparison enforces the Contract Major rule.

Every installed Odoo view, action, scheduled job, and control is functional.
Device actions display a complete setup state before an active Binding Revision
exists.

The fresh-database acceptance suite opens every permitted surface and runs each
action. It fails for a dead control, placeholder route, or unhandled
prerequisite.

CI generates an inventory of every installed Odoo surface. Each menu, view,
action, control, route, and scheduled job requires a test identity.
