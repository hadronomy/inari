---
status: accepted
---

# Treat durable acceptance as Odoo print success

The Odoo printer contract reports success after the Agent reaches `Accepted`
for an idempotent Print Intent. Odoo can then increment its receipt counter
while Inari tracks physical output separately.

Waiting for physical confirmation increases checkout latency and cannot prove
exactly-once output for every printer. A failure before `Accepted` starts the
operator recovery flow.

The Odoo receipt counter records accepted Print Intents. Inari views label this
value `Copies requested`, while Print Audit states describe output evidence.

An automatic report failure returns a handled device result. It does not raise
an RPC error into a completed payment or stock transaction.

A Retry reconciles the Idempotency Key before it renders again. A changed
Payload Fingerprint requires a new Print Intent.
