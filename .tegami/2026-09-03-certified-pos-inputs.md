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

### Add Certified Scale and scanner input

The Odoo POS now reads bound Certified Scales and Agent-hosted scanners through
authenticated Inari Device Streams. Scale acceptance uses exact decimals,
freshness and stability checks, and the active Certification Record. Barcode
Events use Odoo's native barcode handling after Inari removes duplicates.
