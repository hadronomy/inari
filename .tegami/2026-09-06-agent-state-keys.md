---
packages:
  "group:edge": patch
---

### Register separate Agent State signing keys

Agents now keep a dedicated Ed25519 state-signing key in protected storage.
Enrollment registers its public key with the Controller. The Controller retains
previous public keys and rejects reuse across Agents or key purposes.

Upgrade the Agent and Controller together and apply the Controller migrations.
Enroll each Agent with a new invitation for gateway protocol `2026-09-06`.
