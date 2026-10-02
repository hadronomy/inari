---
status: accepted
---

# Limit offline client trust

An enrolled Agent permits new Client Pairing for 24 hours after its last
Controller policy sync. Existing Client Grants can renew offline for seven
days. The Agent requires Controller access after either limit.

This rule preserves bounded local operation without turning a stale Policy
Snapshot into permanent authority. New Agent enrollment always requires the
Controller.

An accepted Print Job continues after the limit. The limit affects new Client
Pairing and Client Grant renewal only.
