---
status: accepted
---

# Use the Managed Workload Interface and reconciled webhooks

Odoo submits Managed Device Work through the versioned HTTPS Managed Workload
Interface protected by OIDC. Each Odoo company uses one Organization Workload
Identity.

The Controller checks the database, company, Organization, Site, and Agent on
every request. Odoo does not use Zenoh or Controller transport models.

The Controller sends signed authoritative-state webhooks for fast updates.
They include `Accepted`, `In Progress`, and terminal states.

Odoo also runs five-minute cursor Job Reconciliation. This combination gives
fast status without making webhook delivery the only recovery path.

Odoo registers one fixed HTTPS route through an authenticated challenge.
Controller signatures protect deliveries. Organization-scoped cursors retain
90 days of history and fail explicitly after expiry.

The Controller can store Managed Device Work before Agent delivery. A marked
Odoo action observes Agent acceptance for at most 10 seconds.

If work remains pending, Odoo returns a handled `Waiting for Agent` result. It
does not report success or start browser printing. A global recovery panel
reconciles the work after the observation window.

The Controller encrypts each pending payload with AES-256-GCM. OpenBao Transit
protects its data key. The Controller deletes payload content after Agent
`Accepted`, cancellation, or expiry.

One environment-scoped Transit key wraps Managed Payload data keys. It rotates
every 90 days. Plaintext key backup and normal named-key deletion stay
disabled.

The first release stores encrypted payloads in a separate PostgreSQL table.
Pre-existing delivery records store identifiers, lifecycle state, and dispatch
metadata only. Bounded operation-specific payloads do not require a new object
store.

Receipt Images and Label Documents accept at most 2 MiB. Report PDFs accept at
most 10 MiB. Per-Agent and per-Organization byte quotas still apply.

The Controller derives immutable `expires_at` from operation policy at
Controller Admission. Odoo does not supply it. Preparation expires after 60
seconds, and receipts or reports expire after five minutes.

An OpenBao failure rejects new content work and pauses affected dispatch.
Content-free reads and webhooks stay active. The Controller keeps no plaintext
data key after dispatch.

One PostgreSQL transaction deletes the wrapped data key and live ciphertext
row. WAL and backups retain encrypted pages during the 14-day Backup Exposure
Window.

Per-Agent and per-Organization limits reject excess work before acceptance.
Acceptance, cancellation, and expiry serialize on the Managed Work row. A
cancellation that wins deletes the payload. When acceptance wins, cancellation
uses the normal stop contract.

Direct cancellation exists only in `pending_agent`. After dispatch starts, a
cancel request stops future attempts and requests signed Agent evidence. The
Controller records `canceled` only after proof that acceptance did not occur.
Otherwise it records `recovery_uncertain` until signed evidence resolves it.

An expired cursor starts bounded reconciliation of nonterminal work and
records changed during the 90-day history window. Projection recovery uses a
separate snapshot.

The Controller uses RFC 9421 HTTP Message Signatures and a content digest.
Signed fields bind the method, target URI, key, timestamp, delivery, audience,
database, and Organization. Each retry keeps the delivery identity and uses a
fresh timestamp and signature.

Odoo accepts a five-minute timestamp window and keeps delivery identities for
90 days. The Controller publishes an Ed25519 JWK set.

Odoo refreshes the set hourly and once for an unknown `kid`. Signing-key
overlap lasts 24 hours. Public-key history lasts 90 days.

Odoo displays pending work as `Waiting for Agent`. It creates one manager
activity after two minutes or half the work lifetime, whichever occurs first.

An automatic sequence records pending work and continues. Its persistent
summary reports accepted, pending, and failed work.
