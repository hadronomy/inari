---
status: accepted
---

# Deploy the first Controller through MZE infrastructure

The first MZE Controller deployment uses the signed Inari Controller chart in a
separate namespace managed by `mze-infra`.

Local Device Work does not depend on this cluster. The Controller retains its
multi-tenant architecture for a later dedicated control plane.

The present single-host cluster is staging for Managed Device Work. Managed
production requires three independent failure domains and three K3s servers.

It also requires hard anti-affinity, separate storage failure domains, three
PostgreSQL instances, three OpenBao replicas, three Controller replicas, and
three Zenoh routers. CloudNativePG uses synchronous quorum across the three
failure domains. OpenBao uses a three-member Raft cluster.

Controller, Zenoh, OpenBao, and CloudNativePG disruption budgets set
`minAvailable: 2`.

Encrypted backups use a separate provider or region. Production gates include
one-host and one-storage-domain failure tests.

Recovery runs operation-specific canaries at the start and end of a one-hour
soak. It includes one Retry and one Agent reconnect.

Managed production stays blocked while static sealing, a single replica,
local-only backup, or a missing required disruption budget remains active.
