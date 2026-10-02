---
status: accepted
---

# Store pending payloads in PostgreSQL

The first release stores each encrypted Managed Payload in a separate
PostgreSQL table. Pre-existing Controller delivery records store identifiers,
lifecycle state, and dispatch metadata only.

The 2 MiB receipt or label limit, 10 MiB PDF limit, and bounded queues make a
new object store unnecessary. One transaction deletes the wrapped key and
live ciphertext row.

WAL and backups retain encrypted pages until their 14-day expiry. The recovery
procedure never dispatches restored expired work.

Deletion is immediate in live systems and final after the 14-day Backup
Exposure Window. Backups stay encrypted, and each restore produces an audit
record.
