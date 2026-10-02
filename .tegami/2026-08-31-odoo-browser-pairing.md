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

### Pair each POS browser securely

The POS now guides an operator through Agent approval when a receipt printer
needs a Client Grant. It keeps the browser key non-exportable, resumes the same
ticket after approval, and renews short-lived access without exposing security
credentials to the operator.

Odoo now validates and signs each approved Pairing Request through an Ed25519
OpenBao Transit key. The private signing key stays outside Odoo.
