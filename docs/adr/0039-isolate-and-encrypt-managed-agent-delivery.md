---
status: accepted
---

# Isolate and encrypt managed Agent delivery

Each Agent certificate has one exact Organization, Site, and Agent URI SAN.
One router ACL subject authorizes only its exact Zenoh keyspace. Controller and
router certificates use separate identities.

The Controller encrypts its signed dispatch envelope with RFC 9180 HPKE Base
mode. The suite is X25519, HKDF-SHA256, and AES-256-GCM. RFC 8785 metadata is
authenticated data.

The Agent uses a separate X25519 recipient key. It rotates every 30 days and
keeps the prior key for 15 minutes. Revocation rejects the prior `kid`
immediately.

The Agent atomically persists `(Dispatch Epoch, sequence)`. A gap blocks
execution and starts reconciliation. An exact replay returns its stored result.

Certificate revocation closes active sessions and removes keyspace access
within 60 seconds.
