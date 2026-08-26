---
status: accepted
---

# Require local print idempotency

Every Print Intent submission requires an `Idempotency-Key`. An identical
replay returns the existing Print Job, while a changed Payload Fingerprint
returns `409`.

The Agent keeps the content-free idempotency record for 90 days. A Reprint uses
a new Idempotency Key because it requests another physical copy.

The addon length-prefixes the stable Print Intent fields with a canonical
encoding. It hashes that encoding with SHA-256 and writes the opaque key as
`pi_v1_<base32-digest>`.

The Payload Fingerprint hashes the Contract Major, operation, Device, media
type, exact payload bytes, normalized options, and resolved deadline.

The Agent assigns a Local Device Work deadline. The Controller assigns a
Managed Device Work deadline, and the Agent checks it.

Credentials and transport data stay outside the digest.

A Retry uses the stored deadline before the Agent compares fingerprints. A
Retry cannot reset or extend that deadline.
