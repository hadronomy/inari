---
status: accepted
---

# Use independent production recovery keys

Production uses Google Cloud KMS in the `europe` multi-region through Workload
Identity Federation. Separate keys provide OpenBao auto-unseal, backup
wrapping, and Ed25519 Recovery Point signing.

Locked `eur4` dual-region Cloud Storage holds encrypted artifacts and signed
manifests. Recovery keys remain outside the cluster and backup set.

Root-key recovery and destructive security actions require approval from two
people. Production has no static unseal fallback. A KMS outage blocks new
unseal while an already running OpenBao cluster remains active.

Each backup artifact is encrypted before local persistence. A Recovery Point
is incomplete when any artifact, checksum, encryption result, or upload is
missing.

The system creates a daily fenced base Recovery Point and signed WAL
checkpoints at least every five minutes. Every Transit key change creates a new
OpenBao snapshot. The fence ends after consistent capture, while encryption,
upload, and restore checks continue outside it.

Production runs three Controller replicas, three Zenoh routers, and a
three-member OpenBao Raft cluster. CloudNativePG runs three instances with
synchronous quorum across three failure domains.

Controller, Zenoh, OpenBao, and CloudNativePG use disruption budgets with
`minAvailable: 2`.
