---
status: accepted
---

# Migrate Odoo before reopening traffic

A production schema change starts a maintenance window, stops Odoo traffic,
and checks a pre-migration backup. The rollout migrates the database and runs its
acceptance gate before it reopens traffic.

The addon starts a POS Drain five minutes before traffic stops. An active
payment can finish. New payment validation then stops. The gate waits for
active-tab acknowledgements and zero paid unsynchronized orders.

A System Administrator records a reason to override the drain. The deployment
does not close the Odoo POS session.

The gate restores a fresh custom-format `pg_dump` into an isolated PostgreSQL
instance of the same major version. It checks the dump digest and catalog
before restore.

The restore uses an ephemeral namespace, encrypted storage, short-lived
credentials, no ingress, and a deny-by-default network policy. The gate deletes
the namespace and volumes within one hour.

A failure before reopening restores the checked backup and prior artifacts. A
failure after reopening uses a forward fix because new business transactions
can already exist.

This sequence gives one clear database and artifact state. It avoids runtime
compatibility parsing and protects new sales from an old database restore.

The planned-downtime target is 15 minutes. Each release uses a production-size
rehearsal and measured restore time to set its rollback point.

Backup age, WAL lag, recovery-drill age, restore success, and acceptance
success are mandatory migration gates.
