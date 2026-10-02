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

### Protect Managed Payload storage

The Controller encrypts stored Dispatch Envelopes with individual data keys
protected by OpenBao Transit. Command history retains only Managed Work
references. Acceptance, rejection, and expiry remove the stored content.

Retries now reject changes to the Device Work request, including its Device and
Print Origin. Exact retries retain their original identity during an OpenBao
outage.

The upgrade requires coordinated Controller downtime. It blocks while older work
remains pending and unexpired. Expired dispatches become Recovery Uncertain and
require reconciliation. The previous Controller binary cannot use the upgraded
schema.
