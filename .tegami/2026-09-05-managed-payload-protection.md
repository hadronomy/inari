---
packages:
  "group:edge": patch
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
