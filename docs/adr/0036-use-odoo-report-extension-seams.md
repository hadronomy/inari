---
status: accepted
---

# Use Odoo report extension seams

The core addon generates contextual `ir.actions.client` records for active
Report Bindings. One Device Adapter uses the `ir.actions.report handlers`
registry for explicitly marked report actions.

Each server-generated action sets `context.inari_managed_report=true` and
`context.inari_report_binding_id`. The binding identity must match the
contextual action and secure request. A stale or mismatched marker fails before
rendering.

The versioned secure request contains the binding, expected Binding Revision,
action, model, ordered identifiers, Site, report type, copy count, and
controlled wizard data. Each action defines field types, size limits, and a
canonical form. The browser supplies no rendered bytes, Device, or unrestricted
options.

The signed artifact also contains the optional Inari Stock Addon. It owns the
stock model extensions and depends on `stock`.

Unmarked Odoo report actions keep their download and browser-print
Implementation. Every marked action returns a truthy handled result. The
versioned result identifies accepted, pending, or failed work and its recovery
record. Binding, Site Resolution, render, and Device Preflight failures never
fall through to browser printing.

Marked sequences set `close_on_report_download=false` and continue through
Odoo `anotherAction`. Each Site group records its result and later groups
continue. The persistent summary keeps accepted, pending, and failed results
visible.

The integration does not patch report rendering, report downloads, or global
action menus.
