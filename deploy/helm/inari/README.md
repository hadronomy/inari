# Inari Helm chart

This chart installs the Inari Controller and a separate signed Router plane. It
expects PostgreSQL, OIDC, step-ca, certificates, and secret delivery to be
provided by the cluster’s existing platform.

For production planning, upgrades, recovery, and troubleshooting, use the
[Kubernetes operations guide](../../../docs/kubernetes.md).

## Requirements

- Kubernetes 1.29 or newer;
- Helm 3.14 or Helm 4;
- externally managed PostgreSQL;
- an OIDC confidential client;
- a step-ca JWK provisioner;
- separate Controller and Router data-plane certificates;
- a dedicated management CA and separate management certificates;
- an Ed25519 policy key pair;
- persistent storage for every Router;
- a NetworkPolicy-capable CNI when policy is enabled.

## Install

Choose a version from the
[controller-chart releases](https://github.com/hadronomy/inari/releases):

```sh
export INARI_CHART_VERSION=<version>
helm show values oci://ghcr.io/hadronomy/charts/inari \
  --version "$INARI_CHART_VERSION" > inari-values.yaml

helm upgrade --install inari oci://ghcr.io/hadronomy/charts/inari \
  --version "$INARI_CHART_VERSION" \
  --namespace inari \
  --create-namespace \
  --values inari-values.yaml \
  --atomic \
  --timeout 10m
```

The pre-install or pre-upgrade Job must reach PostgreSQL. It takes the migration
advisory lock and applies the embedded schema before controller pods roll.

The chart starts with enrollment and Zenoh disabled. Keep them disabled until
the pinned CA and exact per-Agent Router policy are ready. Enabling mutual TLS
alone does not bind an Agent to its own namespace.

Check the result:

```sh
kubectl --namespace inari get pods
helm test inari --namespace inari --logs
```

## Secret references

Secret values do not belong in Helm values. Point the chart at existing Secrets:

| Purpose | Value | Default key |
| --- | --- | --- |
| PostgreSQL URL | `database.secret` | `url` |
| PostgreSQL CA | `database.caCertificateSecret` | Explicit key |
| OIDC client secret | `identity.oidc.clientSecret` | `client-secret` |
| step-ca provisioner key | `managedGateway.certificate.stepCa.signingKey` | `provisioner-key.pem` |
| Controller Zenoh identity | `zenoh.tls.controllerSecret` | `ca.crt`, `tls.crt`, `tls.key` |
| Router Zenoh identity | `zenoh.tls.routerSecret` | `ca.crt`, `tls.crt`, `tls.key` |
| Controller management identity | `zenoh.management.controllerSecret` | `ca.crt`, `tls.crt`, `tls.key` |
| Router management identity | `zenoh.management.routerSecret` | `ca.crt`, `tls.crt`, `tls.key` |
| Policy signing private key | `managedGateway.routerPolicy.signingKey` | `signing-key.pem` |
| Policy signing public key | `zenoh.signingPublicKey` | `signing-key.hex` |

Each identity uses a separate Secret and private key. The policy private key is
Ed25519 PKCS#8 PEM. Its matching public key contains 64 hexadecimal characters.
Only the Controller mounts the private key. Only Routers mount the public key.

The Router data-plane certificate covers the public Service and every pod DNS
name. It requires both server and client certificate usage for listeners,
mesh connections, and the authenticated readiness probe. Its common name and
the Controller data-plane common name appear in
`managedGateway.routerPolicy.trustedPeerCommonNames`. The CA reserves these
names and never issues them to Agents.

The dedicated management CA signs the Router server and Controller client
certificates. The Router management certificate covers every pod DNS name.
The Controller management certificate uses client certificate usage and the
exact `zenoh.management.controllerCommonName`. This name stays separate from
data-plane peers and Agent names. Router pods receive no Controller private key.

For a private PostgreSQL CA, set `database.caCertificateSecret` to its Secret
name and key. The chart mounts it in both the Controller and migration Job at
`/var/run/secrets/inari/database-ca/ca.crt`. Include `sslmode=verify-full` and
`sslrootcert=/var/run/secrets/inari/database-ca/ca.crt` in the PostgreSQL URL.
Helm hook Jobs need this chart value because Flux does not apply post renderers
to hooks in all supported versions.

## Router configuration

The chart starts `inari-router`, which supervises the packaged stock `zenohd`.
Each ordinal receives its own TOML file, deterministic Router ID, and mesh
endpoints. The Controller receives every matching management origin and ID.
The same `managedGateway.routerPolicy.fleetId` identifies both workloads.
Custom static Router ACLs are unavailable.

Management listens on a separate HTTPS port with dedicated mTLS. The headless
Service publishes unready pod addresses so the Controller can install the
first policy. Startup and liveness probes use management. Readiness uses the
data-plane listener, which stays closed without a current signed policy.
The public Service exposes only the data plane.

Each Router needs its own retained PVC. Its `policy` directory stores the highest
accepted generation and private TLS material in the generated runtime file.
The process creates this private directory below the volume mount because the
volume root belongs to Kubernetes. Protect volume backups as credentials.

Permission changes close old links before acknowledgment. A signed lifetime
refresh keeps established links when authority and TLS material stay unchanged.
TLS rotation reloads the certificate files and reopens the data plane after an
authenticated probe. Invalid certificates and expired policy close the data
plane while management remains available.

External Agents need a private TCP endpoint.
`managedGateway.dataPlane.publicEndpoints` must match that endpoint and its
certificate names.

## Network and workload security

NetworkPolicies are enabled by default. Add cluster-specific ingress and egress
through the focused `networkPolicy.*.additionalIngress` and
`additionalEgress` values, then review the rendered policy.

Workloads use non-root users, runtime-default seccomp, no privilege escalation,
dropped capabilities, read-only root filesystems, bounded temporary storage,
and no service-account token. The chart creates no RBAC because the application
does not call the Kubernetes API.

The default `image.digest` selects a signed Controller that understands this
chart's configuration. A custom Controller image must use an explicit digest or
tag. To select a tag, clear `image.digest` and set `image.tag`. The chart rejects
an empty selection.

The default `zenoh.image.digest` selects a signed Router Supervisor from the
same source as the Controller. To select a custom tag, clear
`zenoh.image.digest` and set `zenoh.image.tag`. Empty selection is rejected.
Stock `eclipse/zenoh` images cannot run the Supervisor configuration.
Production Release Sets pin the published digest. Test fixtures clear the
digest and use a verification tag that does not identify a published artifact.

## Upgrades and removal

Database migrations are forward-only. A Helm rollback does not undo schema
history; use expand-and-contract changes and roll back only to a compatible
binary.

Managed Controller upgrades use `Recreate`. All old Controller pods stop before
new pods allocate policy generations. This prevents competing configurations
from advancing generations during a rolling upgrade. The API is unavailable
until the new Controller becomes ready. Local Device Work can continue.

Policy signing-key rotation uses a replacement fleet. The verifier accepts one
public key, and retained policy cannot pass validation under a new key.
Keep old PVCs offline and preserve PostgreSQL generations. Read the
[rotation requirements](../../../docs/kubernetes.md#rotate-the-policy-signing-key)
before changing this key.

Kustomize users set `migrations.helmHook=false`, which renders an ordinary,
chart-versioned Job instead of a Helm hook.

Remove the release with:

```sh
helm uninstall inari --namespace inari
```

Uninstall leaves external Secrets, PostgreSQL data, and retained Zenoh volumes
under their existing owners.
