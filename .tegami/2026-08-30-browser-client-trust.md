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

### Add browser Client Trust

The Agent now pairs an Odoo browser with an exact Agent Endpoint and business scope.

Client Grants use Ed25519 access tokens, browser-key DPoP proofs, durable nonces, and immediate revocation checks.
