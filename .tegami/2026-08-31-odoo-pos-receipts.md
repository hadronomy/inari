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

### Route Odoo POS receipts through Inari

Odoo 19 can now keep an active Inari receipt binding authoritative from receipt rendering through durable Agent admission.

The browser retries one standard DPoP nonce challenge without changing the receipt identity. An unresolved Inari print stays in recovery and never falls through to native or browser printing.
