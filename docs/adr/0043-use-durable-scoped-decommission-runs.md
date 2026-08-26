---
status: accepted
---

# Use durable scoped Decommission Runs

Decommission uses durable Site and Organization runs. Addon removal requires
one completed database-wide Decommission Run that covers every active scope.

Each run stores its covered identities, phase, actor, start time, result,
evidence identities, failure code, Retry history, and completion time. Every
phase is idempotent and commits before the next phase starts.

The phases block new Device Work, drain and reconcile work, and check the
Compliance Export and backup. They then revoke dispatch authority, purge
reachable Device Spools, deactivate bindings, and record completion.

A short-lived Decommission purge authority survives dispatch revocation. It
binds one run, Agent, Organization, and Site. It permits only Device Spool purge
and expires when that purge ends. It cannot authorize Device Work.

Permanent Agent Quarantine can replace a purge for an unreachable Agent. The
run records signed absence, inventory, revocation, and duplicate-output
evidence. A Device Manager records the evidence. A System Administrator
approves permanent quarantine.

A failed phase keeps its scope disabled. The operator gets one controlled
Retry. The run cannot skip a failed phase or finish with active bindings,
unreconciled work, or reachable spool content.

The removal guard makes no network request and starts no asynchronous work. It
checks only the committed database-wide completion record.

The runbook removes the Inari Stock Addon before the core addon. Enterprise
IoT activation remains blocked until Inari removal completes. The Compliance
Export and backup preserve content-free evidence after addon table removal.
