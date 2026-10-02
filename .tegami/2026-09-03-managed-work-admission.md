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

### Add secure Managed Work admission

The Controller now accepts preflighted, encrypted Report PDF and Label Document
work from an authorized Odoo backend. Agents publish a separate encryption key
during enrollment, so report content stays encrypted outside the target Agent.
