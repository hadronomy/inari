---
status: accepted
---

# Use versioned Print Intent identity

A Print Intent has one discriminated Print Origin. A POS origin contains the
database, POS configuration, session, offline order UUID, and optional server
order identity. It also contains the document kind and content revision.
Preparation output adds the segment kind, index, and revision.

A report origin contains the database, company, Site, Report Binding, Report
Route, report action, model, and ordered record identities. It also contains
the controlled wizard digest and rendered-document index.

The identifier derives from the format version, canonical Print Origin,
Binding Revision, Device, and copy ordinal. Odoo allocates the copy ordinal in
the same transaction that creates the Print Intent.

A Retry keeps this identifier. A Reprint allocates a new copy ordinal and
creates a new identifier. One Print Intent always means one physical copy.

A Reprint requires one time-limited Reprint Approval. It binds the Device
Manager, Print Intent, Device, Binding Revision, reason, and new Copy Ordinal.
The approval permits one Reprint and expires after 15 minutes. The system
permits no standing or post-hoc approval.
