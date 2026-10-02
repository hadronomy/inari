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

### Execute admitted receipt work through the durable Device Spool

The Agent now rechecks the exact Client Grant and Device Capability before it permits printer I/O. It records an I/O marker first, runs printer submission in an isolated process, and reports uncertain output without an automatic retry.

Receipt images stay encrypted at rest. The Agent creates and reuses deterministic printer bytes without a plaintext temporary file.
