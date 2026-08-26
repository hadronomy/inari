---
status: accepted
---

# Block managed production on a single host

The current MZE cluster has one host, one K3s server, local storage, one
PostgreSQL instance, and one OpenBao replica. It is staging for Managed Device
Work.

Local POS printing remains eligible for production after its physical gate
because Local Device Work does not depend on the cluster.

Managed production requires three independent failure domains and three K3s
server nodes. It also requires replicated storage and three CloudNativePG
instances.

Three OpenBao replicas protect secret availability. Three Controller replicas
and three Zenoh routers use topology spread and `minAvailable: 2` disruption
budgets.

Hard anti-affinity prevents two replicas from sharing one host or storage
failure domain. Backups use a separate provider or region.

The production gate tests loss of one host and one storage domain.
