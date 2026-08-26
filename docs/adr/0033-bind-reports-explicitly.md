---
status: accepted
---

# Bind reports explicitly

An Odoo Report Binding assigns one `ir.actions.report` action to an active
Device Binding Revision. It selects one manual or automatic Report Route.

A Device Binding uses a POS configuration or Site Binding Scope. Reports use
the `report_pdf` or `stock_label` Device Purpose. Each Report Binding selects a
Site-scoped Binding Revision.

Only one active Report Binding can exist for one company, Site, report action,
and Report Route. A manual flow resolves the Site before Device Preflight.

A marked flow completes deterministic Site Resolution before Device Preflight.
Odoo owns explicit mappings for POS, warehouse, picking, and operation
contexts. Mixed-Site records split before rendering. An unresolved group uses
a controlled wizard or records a handled failure without a default Site.

A stale Binding Revision marks the Report Binding `Needs attention`.

The addon adds `Print with Inari`. Unmarked report actions keep Odoo download
and browser-print actions. Automatic report and label workflows use Inari only
when an active automatic Report Binding exists.

Odoo renders each Report PDF or approved ZPL Label Document before it submits
Managed Device Work. The Agent never fetches an authenticated Odoo report URL.

One Site-specific rendered PDF creates one Print Intent, even when it contains
multiple records or pages. Each requested copy creates another Print Intent.
Each ZPL envelope creates one Label Document and one Print Intent.

Every marked Report PDF and Label Document uses Managed Device Work, including
a marked report started in a browser. One secure backend method validates and
renders each marked action.

Capability Negotiation preserves the Native Device Path for unmarked actions
until the selected Device has a tested Device Adapter. A marked action never
falls back to browser printing.

The core addon generates contextual Odoo client actions. One Device Adapter
uses the `ir.actions.report handlers` seam only for explicitly marked report
actions.

The optional Inari Stock Addon annotates stock automatic-report producers. The
same Device Adapter handles stock and POS automatic report sequences. The
addon groups documents by Site, continues after handled Device failures, and
shows one persistent summary with accepted, pending, and failed work.

An automatic report error records recovery and returns a handled result. It
does not change a completed payment or stock operation.

Before Print Intent creation, a missing binding, unresolved Site, or failed
Device Preflight stops the marked route. After creation, the Device Adapter
never returns false, reroutes, or starts an implicit browser print.
