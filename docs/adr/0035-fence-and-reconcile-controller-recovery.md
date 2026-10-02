---
status: accepted
---

# Fence and reconcile Controller recovery

A Controller backup or recovery starts a Recovery Fence. The fence stops
managed submission and dispatch while Local Device Work continues.

The fence can remain active for at most 60 seconds. At that limit, the system
marks the Recovery Point failed, raises an alert, and releases the fence. A new
run starts the Retry.

One PostgreSQL row stores the fence state and Admission Epoch. Acceptance and
fence activation lock it with `SELECT FOR UPDATE`. A losing submission creates
no Managed Payload and returns `recovery_fence`.

One Recovery Point signing identity signs the PostgreSQL backup, WAL range,
OpenBao snapshot, Release Set, schema revision, and Dispatch Epoch. Immutable
signed Recovery Checkpoints run at least every five minutes.

The recovered Controller starts without dispatch credentials. It checks
payload fingerprints and reconciles signed Agent State Envelopes.

The Controller never sends accepted, terminal, expired, or Recovery Uncertain
work again. Managed dispatch resumes under a higher Dispatch Epoch after Agent
acknowledgements and Site-specific physical Recovery Canaries.

An unreachable Agent keeps only its Site blocked. A Device Manager records signed
absence, physical inventory, and duplicate-output evidence. A System
Administrator permanently approves Agent Quarantine and new Binding Revisions.

Operation-specific Recovery Canaries run at the start and end of the one-hour
soak. The gate requires one Retry and one Agent reconnect. Print operations
require exactly one physical output. Other operations require their own signed
evidence.

A failed canary blocks only its Site. Passed Sites can resume. The 60-minute
recovery time objective starts when the Site soak starts.
