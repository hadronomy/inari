# Inari Helm chart

This chart installs the Inari controller and its managed Zenoh router plane.
The controller is a stateless Axum/Leptos workload backed by an externally
operated PostgreSQL database. Zenoh runs separately as a StatefulSet so its
network identity and rollout can be managed independently from the HTTP
application.

The chart does not install PostgreSQL, an identity provider, step-ca, or a
secret-management controller. Those are infrastructure dependencies with
their own availability, backup, and security lifecycles. Inari references
existing Secrets and refuses to place credentials in Helm values.

## Prerequisites

- Kubernetes 1.29 or newer
- Helm 3.14 or newer, or Helm 4
- an externally managed PostgreSQL database
- an OpenID Connect provider
- step-ca configured with a JWK provisioner
- controller and router certificates issued by a CA trusted by enrolled agents
- a NetworkPolicy-capable CNI when `networkPolicy.enabled=true`

## Install

Copy the default values and edit every value that names your organization,
public endpoints, identity provider, certificate authority, or existing
Secret:

```sh
helm show values oci://ghcr.io/hadronomy/charts/inari --version 0.2.0 > inari-values.yaml
helm upgrade --install inari oci://ghcr.io/hadronomy/charts/inari \
  --version 0.2.0 \
  --namespace inari \
  --create-namespace \
  --values inari-values.yaml \
  --atomic \
  --timeout 10m
```

The pre-install migration Job must reach PostgreSQL before the controller
rollout begins. An atomic install removes release-owned resources when that
Job fails, while the failed hook remains available long enough to inspect its
logs.

After installation:

```sh
kubectl --namespace inari get pods
helm test inari --namespace inari --logs
```

## Existing Secrets

Secret values never belong in a values file. The chart reads these keys:

| Purpose | Value | Default key |
|---|---|---|
| PostgreSQL URL | `database.secret` | `url` |
| OIDC client secret | `identity.oidc.clientSecret` | `client-secret` |
| step-ca provisioner key | `managedGateway.certificate.stepCa.signingKey` | `provisioner-key.pem` |
| Controller Zenoh client identity | `zenoh.tls.controllerSecret` | `ca.crt`, `tls.crt`, `tls.key` |
| Zenoh router identity | `zenoh.tls.routerSecret` | `ca.crt`, `tls.crt`, `tls.key` |

Use External Secrets Operator, Sealed Secrets, SOPS, or an equivalent
organization-owned mechanism to create them. Plaintext Secret manifests are
intentionally absent from this chart.

The router certificate must cover the client Service name and every StatefulSet
pod DNS name used by the generated router mesh. The operations guide lists the
exact names.

## Zenoh configuration

By default, the chart creates a JSON5 router configuration with:

- router mode and a stable per-pod Zenoh ID;
- a full mesh over StatefulSet DNS names;
- multicast discovery disabled;
- TLS with mutual authentication and hostname verification;
- automatic link closure when a peer certificate expires;
- a default-deny ACL allowing the managed Inari keyspace over authenticated
  TLS links only;
- admin space disabled.

Set `zenoh.config.existingConfigMap` when the organization owns a more specific
router configuration. The named ConfigMap must contain `zenoh.config.key`.
When external configuration changes, set `zenoh.podAnnotations` or another
rollout trigger because Helm cannot checksum content it does not own.

The regular Zenoh Service carries controller and agent traffic. The headless
Service exists only for stable router identities and mesh connections. For
external agents, choose a TCP-capable `LoadBalancer` Service or an equivalent
private network endpoint and keep `managedGateway.dataPlane.publicEndpoints`
aligned with that address.

## Network policy

NetworkPolicies are enabled by default. Same-namespace traffic can reach the
controller and router; controller egress is limited to DNS, HTTPS, PostgreSQL,
and the router workload. External ingress controllers, agent networks, and
organization-specific egress destinations must be added through the focused
`networkPolicy.*.additionalIngress` and `additionalEgress` lists.

These extension points accept native Kubernetes NetworkPolicy rules. They are
deliberately narrow escape hatches for topology that a portable chart cannot
infer. Review the rendered policy whenever they change.

## Upgrades and migrations

Helm owns database migration ordering by default. The fixed-name pre-upgrade
hook acquires Inari's PostgreSQL advisory lock and runs the embedded forward
migrator before any controller pod is replaced. Production controller pods
only verify schema currency; they never perform DDL.

Use expand-and-contract migrations across rolling releases. A Helm rollback
does not reverse database history. Restore a managed PostgreSQL backup for
physical recovery and deliver logical corrections as new forward migrations.

Kustomize consumers set `migrations.helmHook=false`. That produces an ordinary
chart-versioned Job because `kubectl apply` does not implement Helm hooks.

## Security posture

All workloads default to the Kubernetes Restricted Pod Security Standard:
non-root execution, runtime-default seccomp, no privilege escalation, dropped
Linux capabilities, read-only root filesystems, no service-account token, and
bounded writable temporary storage. The chart creates no Role or RoleBinding
because Inari does not call the Kubernetes API.

The controller and router use separate TLS Secrets. Do not reuse the router's
server private key as the controller client identity. Pin production images by
setting `image.digest`, `zenoh.image.digest`, and `tests.image.digest`.

## Uninstall

```sh
helm uninstall inari --namespace inari
```

Uninstalling Inari does not remove external Secrets, PostgreSQL data, or
retained Zenoh PVCs. This is intentional. Confirm that no edge agents still
depend on the controller before removing those resources.

For rollout, certificate, backup, troubleshooting, and Kustomize procedures,
read the [Kubernetes operations guide](../../../docs/kubernetes.md).
