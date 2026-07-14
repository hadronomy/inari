# Inari Helm chart

This chart installs the Inari controller and a separate Zenoh router plane. It
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
- separate controller and router Zenoh certificates;
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
| OIDC client secret | `identity.oidc.clientSecret` | `client-secret` |
| step-ca provisioner key | `managedGateway.certificate.stepCa.signingKey` | `provisioner-key.pem` |
| Controller Zenoh identity | `zenoh.tls.controllerSecret` | `ca.crt`, `tls.crt`, `tls.key` |
| Router Zenoh identity | `zenoh.tls.routerSecret` | `ca.crt`, `tls.crt`, `tls.key` |

The router certificate must cover its client Service and every StatefulSet pod
name in the generated mesh. Keep the controller and router private keys
separate.

## Router configuration

The default JSON5 configuration uses router mode, deterministic pod IDs, an
explicit StatefulSet mesh, mutual TLS with hostname verification, certificate
expiry enforcement, a default-deny managed-keyspace ACL, and no admin space.

Use `zenoh.config.existingConfigMap` when another system owns router
configuration. The ConfigMap must contain `zenoh.config.key`, and its owner must
trigger rollouts when the content changes.

The normal Service carries controller and agent traffic. The headless Service
exists for mesh identity only. External agents need a private TCP endpoint, and
`managedGateway.dataPlane.publicEndpoints` must match that endpoint and its TLS
certificate.

## Network and workload security

NetworkPolicies are enabled by default. Add cluster-specific ingress and egress
through the focused `networkPolicy.*.additionalIngress` and
`additionalEgress` values, then review the rendered policy.

Workloads use non-root users, runtime-default seccomp, no privilege escalation,
dropped capabilities, read-only root filesystems, bounded temporary storage,
and no service-account token. The chart creates no RBAC because the application
does not call the Kubernetes API.

Pin production images with `image.digest`, `zenoh.image.digest`, and
`tests.image.digest` when your deployment policy requires immutable artifacts.

## Upgrades and removal

Database migrations are forward-only. A Helm rollback does not undo schema
history; use expand-and-contract changes and roll back only to a compatible
binary.

Kustomize users set `migrations.helmHook=false`, which renders an ordinary,
chart-versioned Job instead of a Helm hook.

Remove the release with:

```sh
helm uninstall inari --namespace inari
```

Uninstall leaves external Secrets, PostgreSQL data, and retained Zenoh volumes
under their existing owners.
