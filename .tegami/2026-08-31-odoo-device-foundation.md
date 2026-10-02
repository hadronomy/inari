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

### Add the Odoo 19 device foundation

Odoo can now project Inari organizations, sites, agents, devices, and
capabilities. Device managers can create immutable bindings, approve tested
revisions, and inspect content-free print records from Odoo.
