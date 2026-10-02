---
packages:
  group:edge:
    replay:
      - exit-prerelease(cargo:inari-agent-client)
      - exit-prerelease(cargo:inari-device-center)
      - exit-prerelease(pip:inari)
      - exit-prerelease(pip:inari-brand)
      - exit-prerelease(pip:inari-print-contracts)
      - exit-prerelease(msix:inari-device-center)
---

### Add secure browser Client Pairing

The Agent now exposes the complete Client Pairing flow for Odoo POS browsers.
It requires exact-origin Ed25519 proof, Device Manager approval, a signed Odoo
Pairing Assertion, short-lived DPoP access tokens, and key-bound offline renewal.
