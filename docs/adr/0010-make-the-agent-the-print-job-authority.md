---
status: accepted
---

# Make the Agent the Print Job authority

The Agent is the authority for Print Job state. POS IndexedDB stores recent
Print Intent identifiers and cached states for offline operator use.

Agent events provide fast updates, while the job interface provides Job
Reconciliation after event loss or an uncertain response. Odoo receives
content-free Print Audit Records after reconnection.

For Local Device Work, the Agent selects the execution deadline from policy
when it accepts the work. Managed Device Work keeps the Controller-assigned
deadline. The Agent uses a monotonic clock for enforcement and publishes UTC
timestamps. A Retry keeps the original deadline.

Each Agent event stream has a persistent sequence, and each changed resource
has a version. A gap, reversal, reconnection, or Transport Leader change starts
Job Reconciliation.

The Agent keeps content-free Print Job metadata for 90 days. A daily bounded
cleanup task deletes older metadata without delaying active Device Work.

The Agent signs a State Envelope for offline audit synchronization. Odoo uses
the Agent signature as execution evidence and the authenticated Odoo session
as actor evidence.

The envelope uses compact JWS with EdDSA over RFC 8785 JSON. Odoo resolves the
dedicated Agent State Envelope `kid` through its JWK Projection and current
certificate status.

The Controller retains Agent JWK and certificate history for 90 days. Each
envelope has a unique identity that Odoo binds to one audit event and Print
Intent.

Active reconciliation runs for 24 hours after `outcome_unknown`. Later signed
evidence can improve the current projection without erasing the unknown
observation, recovery action, or later Reprint from audit history.
