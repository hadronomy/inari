---
packages:
  "group:edge": patch
---

### Add secure browser Client Pairing

The Agent now exposes the complete Client Pairing flow for Odoo POS browsers.
It requires exact-origin Ed25519 proof, Device Manager approval, a signed Odoo
Pairing Assertion, short-lived DPoP access tokens, and key-bound offline renewal.
