---
status: accepted
---

# Require off-site WAL recovery for Odoo

Production Odoo uses CloudNativePG WAL archiving and scheduled base backups in
locked EU dual-region Google Cloud Storage. The recovery-point target is five
minutes, and retention is 14 days.

A daily logical dump alone can lose almost one day of transactions. WAL
recovery supplies a bounded recovery point, while a checked `pg_dump` remains
the portable pre-migration backup.

The team runs a complete recovery drill each month.

Controller Managed Work recovery serializes acceptance and Recovery Fence
activation with `SELECT FOR UPDATE` on one Admission Epoch row. A daily fenced
base Recovery Point includes an aligned OpenBao snapshot. Signed immutable WAL
checkpoints run at least every five minutes. Every Transit key change creates a
new OpenBao snapshot.
