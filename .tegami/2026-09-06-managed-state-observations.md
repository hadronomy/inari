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

### Publish signed managed Print Job observations

The Agent signs managed Print Job state from its durable journal. Pending
observations survive restart and retain each historical state. Managed replies
stay bound to their original Agent, Organization, and Site after enrollment changes.
