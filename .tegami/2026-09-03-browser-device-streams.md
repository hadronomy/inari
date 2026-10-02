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

### Add secure browser Device Streams

The Odoo POS browser can now hold and renew Agent Device Stream leases. It
verifies signed Scale Readings and Barcode Events, coordinates open tabs, and
reconciles device state before it accepts live input.
