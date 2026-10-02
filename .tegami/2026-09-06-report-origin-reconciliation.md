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

### Reconcile managed report Print Jobs

Print Job queries now read the Report Origin stored by managed admission.
They preserve ordered report record IDs and wizard input digests, and reject
an origin whose Organization, Site, or Managed Work does not match its Print Job.
