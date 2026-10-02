---
status: accepted
---

# Hide scheduler states behind the Print Job Interface

The public Print Job Interface exposes `accepted`, `in_progress`,
`output_confirmed`, `failed`, `outcome_unknown`, `expired`, and `canceled`.
Scheduler states remain inside the Print Job Module.

This deep Module keeps retry and dispatch details local. Odoo receives stable
operator outcomes instead of learning the Agent scheduler implementation.

The public states use these transitions:

```text
accepted -> in_progress -> output_confirmed
accepted -> failed | expired | canceled
in_progress -> output_confirmed | failed | outcome_unknown
outcome_unknown -> output_confirmed | failed
```

The last transition applies only during the 24-hour active reconciliation
period. After that period, `outcome_unknown` is terminal.

The public state remains `accepted` through preparation. The Agent publishes
`in_progress` when it commits the first Device I/O marker.

The Agent actively reconciles an `outcome_unknown` Print Job for 24 hours. It
then stops polling, deletes its Receipt Payload, and keeps the terminal state.
The alert stays active until a Device Manager records a reasoned review.

The system accepts signed late evidence for 90 days. It marks the Projection
as `Later confirmed` or `Later failed`. It never removes the unknown interval,
review, or later Reprint from history.

Cancellation produces `canceled` only before the first Device I/O marker.
During `in_progress`, the Agent asks the Driver to stop and reports
`outcome_unknown` unless the Driver proves that no output occurred.

The public representation contains identities, state, state version,
lifecycle timestamps, expiry, retryability, a stable error, a message key, and
the contract version. It excludes payloads and raw Driver messages.

The representation includes Output Evidence with `device`, `spooler`, or
`transport`. Odoo maps each level to operator text that states the available
physical certainty.

The Driver contract assigns the maximum Output Evidence for each operation.
Odoo can use weaker operator wording and cannot upgrade the evidence.

Local Device Work and Managed Device Work enter one per-device FIFO queue. The
queue gives neither source priority. Deadlines remove stale work before Device
I/O.
