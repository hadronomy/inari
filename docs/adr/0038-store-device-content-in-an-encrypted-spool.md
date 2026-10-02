---
status: accepted
---

# Store Device content in an encrypted spool

The Agent stores content-bearing Device Work in a protected Device Spool. Job
rows contain only content-free manifests and encrypted artifact references.

Spool admission uses `BEGIN IMMEDIATE`. One transaction reserves the queue
slot, original bytes, and persistent footprint before the Agent returns
`Accepted`. The persistent footprint includes decoded content, derived raster
content, and 64 KiB of overhead.

Before worker execution, another `BEGIN IMMEDIATE` transaction creates a
Temporary Work Reservation. The job waits or expires when temporary capacity
is unavailable. The two reservations never count the same bytes twice.

One Device has a 128 MiB Device Spool limit. One Agent has a 512 MiB limit.
Both limits count committed artifacts and outstanding reservations. Queue count
and original-byte limits remain independent.

Agent RSS, worker RSS, temporary disk, and operating-system spool use have
separate budgets. The SQLite and Device Spool volume has at least 4 GiB. The
Agent measures the reserve on that exact volume.

The Agent does not evict work or reroute a Print Intent when storage is full.

Startup reconciliation checks reservations, manifests, encrypted artifacts,
and temporary files before the Agent accepts content-bearing work.

Every failure path releases its owned reservation in an idempotent transaction.
Live reservations have an owner and deadline. Startup removes reservations
whose owner is no longer valid.

The Agent streams content into an encrypted temporary artifact, syncs the file,
renames it, and syncs the directory. It then commits the manifest and
`Accepted` state with SQLite FULL durability.

Startup deletes partial and orphan files. A missing referenced artifact fails
only its affected pre-I/O Print Job and raises an alert.

Production requires a TPM 2.0 Rollback Anchor. Each Agent start advances a TPM
monotonic generation and commits the matching SQLite root before readiness.

Missing TPM access or a generation mismatch blocks production and starts Agent
Quarantine. Development can use one explicit software anchor. Restored Agent
data always requires fresh enrollment.

Enrollment provisions one dedicated TPM NV index and binds its public evidence
to the Agent identity. A crash between TPM advance and SQLite commit also
requires fresh enrollment. Repair never rewinds the TPM generation.

Encryption ends at Windows or CUPS submission. The operating-system spool can
persist plaintext. Production uses encrypted host storage, content-free job
names, disabled completed-job retention, disabled crash dumps, and bounded
cleanup. The Agent creates no extra plaintext temporary file.

The first release permits CUPS only on the Agent Host. Preflight accepts an
approved local Unix socket or loopback transport and rejects every remote CUPS
server before `Accepted`.

The Platform Backend attests the filesystem that contains the active platform
spool. It binds evidence to the volume identity, spool path, encryption state,
and current boot. Missing or stale evidence blocks content on that queue.

Completed operating-system spool files disappear immediately. Failed,
canceled, or uncertain files disappear within 15 minutes of the terminal or
uncertain result. A cleanup failure blocks the queue and raises an alert.
