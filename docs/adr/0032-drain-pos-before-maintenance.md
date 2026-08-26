---
status: accepted
---

# Drain POS before maintenance

The addon starts a POS Drain five minutes before a breaking deployment stops
Odoo traffic. Odoo bus notification and polling carry the maintenance state.

An active payment can finish. The addon then blocks new payment validation and
keeps IndexedDB data intact. The release gate waits for acknowledgements from
recently active POS tabs and for zero paid unsynchronized orders.

A System Administrator can abort the release or override the drain with a
recorded reason. A POS Drain never closes the Odoo POS session.

The POS stores an integrity-protected maintenance schedule. An active tab sends
a heartbeat every 15 seconds and becomes unresponsive after two minutes.

An offline register enforces a received schedule. After reconnection, it
synchronizes paid orders before payment resumes.

The resume notice contains the Release Set identity. Stale POS assets keep
payment blocked while IndexedDB stays intact.
