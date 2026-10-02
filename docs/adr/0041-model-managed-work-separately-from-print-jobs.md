---
status: accepted
---

# Model Managed Work separately from Print Jobs

The Controller creates Managed Work before an Agent accepts it. No Print Job
exists while the Managed Work state is `pending_agent` or `dispatching`.

Agent acceptance links the Managed Work to the authoritative Print Job. The
Controller derives the Managed Work deadline from operation policy at
Controller Admission. Odoo does not supply it, and the Agent checks its exact
value.

The Controller keeps a unique `(Organization, Idempotency Key)` pair for 90
days. An exact Payload Fingerprint returns the existing record. A conflict
returns `409`.

The public Managed Work transitions are:

```text
pending_agent -> dispatching | rejected | canceled | expired
dispatching -> accepted | rejected | recovery_uncertain
recovery_uncertain -> accepted | canceled | expired
```

Agent acceptance, cancellation, and expiry lock the same Managed Work row.
Direct cancellation exists only in `pending_agent`.

After dispatch begins, cancellation stops later dispatch attempts and requests
signed Agent evidence. The Controller records `canceled` only after evidence
proves that no acceptance occurred. Uncertain evidence records
`recovery_uncertain`.

Signed late evidence resolves `recovery_uncertain` to `accepted`, `canceled`,
or `expired`. The Agent durable acceptance time decides deadline compliance.

Odoo observes acceptance for at most 10 seconds. Pending work remains handled
as `Waiting for Agent` and continues through global reconciliation.

Local Device Work has no Managed Work record. The Agent owns its deadline from
initial acceptance.
