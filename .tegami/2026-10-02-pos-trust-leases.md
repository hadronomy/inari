---
packages:
  "group:edge": patch
---

### Preserve browser trust and execution leases

A first protected request can receive a DPoP nonce challenge without an invented nonce. Client Grant renewal requires its original Client Pairing. Physical execution renews its lease during receipt preparation and worker startup.
