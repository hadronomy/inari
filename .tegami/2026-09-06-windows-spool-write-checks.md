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

### Reject incomplete Windows spool writes

Windows printing now checks the spool job identity and complete byte count.
If submission fails, the Agent attempts to abort the open spool job instead
of finalizing a partial document. The physical outcome remains uncertain after
an I/O attempt; the Agent does not automatically print it again.
