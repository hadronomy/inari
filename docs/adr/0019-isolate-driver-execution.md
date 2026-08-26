---
status: accepted
---

# Isolate driver execution

The Agent runs Driver execution in supervised worker processes. The Agent
process retains Device queues, Print Jobs, state authority, and operation
deadlines.

This isolation contains a blocked platform call or crashed Driver worker. A
worker timeout after Device execution starts produces `outcome_unknown` unless
the Driver returns stronger evidence.

The worker marks the first Device I/O. A crash before this marker permits one
safe retry. A crash after this marker produces `outcome_unknown` and never
causes an automatic retry.

The retry keeps the Print Intent, Print Job, Device, Binding Revision, deadline,
Payload Fingerprint, and Receipt Payload.

Private inherited operating-system pipes carry bounded Pydantic messages and
binary data. Workers expose no network listener or Driver-defined RPC
operation.

Each Driver package has one discovery worker. Each active Device has one
execution worker. This structure isolates Device failures without creating
workers for unused Devices.

A two-phase start handshake commits the first Device I/O marker and
`in_progress` immediately before the worker receives permission. After a
restart, work past that marker becomes `outcome_unknown` without stronger
Driver proof.

The marker is the durable irreversible-execution boundary. It does not prove
physical output.

The worker sandbox denies the Agent database, protected keys, configuration,
and unrelated files. It permits only private IPC and declared Device resources.
A deep Resource Broker Module supplies only checked sockets, handles, and
spooler operations. Workers have no general network or filesystem access.

Workers cannot create child processes or gain privileges. Each has a 128 MiB
memory limit. Linux uses seccomp, Landlock, and cgroup v2. Windows uses a
restricted token and Job Object.

Shutdown stops acceptance, preserves queued work, and gives active work its
remaining deadline up to 60 seconds. The new Agent reports readiness only after
workers and Driver Profiles pass startup checks under one exact Contract Major.
