---
status: accepted
---

# Store only Inari projections in Odoo

Odoo stores read-only Projections of Organizations, Sites, Agents, Devices,
capabilities, and Managed Work. Odoo owns Device Bindings, Device Test Results,
and Print Audit Records.

Inari owns live device state, Print Jobs, events, credentials, and Receipt
Payloads. Odoo does not store authoritative Print Job state.

This ownership prevents two systems from claiming authority for one device or
Print Job. Job Reconciliation reads current state from the Agent.

Odoo stores Organization, Site, Agent, Device, Device Capability, and Managed
Work Projections. It owns the Device Binding, Device Test Result, and Print
Audit models.

Odoo updates Projections from the Controller every five minutes through an
incremental cursor or ETag. A Device Manager can also start a refresh. The POS
reads live operational state from the Agent.

Each Projection stores an immutable Controller UUID. Odoo archives absent
records and retains records that Device Bindings or Print Audit Records still
reference.

Device Operators use controlled POS actions. Device Managers manage Site-scoped
bindings and Device Tests. The sync identity writes Projections, and System
Administrators manage workload identity.

Operators receive current-session bindings and audit records only. Managers
receive permitted Site records. Administrators receive allowed company
records. The sync identity has no interactive login.
