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

### Retire Device authority signers without changing their identity

Authority bundles permit signer retirement and reject signer reactivation. Device observations use the verified Driver Profile for the requested Binding Revision, including Devices that expose more than one capability.
