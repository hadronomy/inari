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

### Preserve receipt recovery across POS reloads

Completed receipt copies leave the active recovery list. A content-free journal preserves their Print Intent for 90 days. Recovery uses the original Agent channel after a Device Binding changes. Authorized clients can query a Print Job by its durable identifier.
