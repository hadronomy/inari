# Run Inari on Kubernetes

This guide is for the team that owns the cluster and the services around it.
An Inari production deployment assumes that PostgreSQL, OIDC, step-ca, ingress,
and secret delivery already have clear operators.

The chart installs two workloads:

- `inari-server`, a stateless Axum/Leptos controller;
- `zenohd`, a StatefulSet of routers with stable mesh identities.

They have separate Services, certificates, probes, rollout policies, and
NetworkPolicies. The controller connects to Zenoh as a client.

## Choose one deployment owner

Helm is the normal installation path. The repository also includes a Kustomize
overlay for GitOps systems that own plain rendered objects.

Do not point Helm and Kustomize at the same release. Helm hooks and declarative
apply have different migration lifecycles, and two owners will eventually fight
over Jobs, Deployments, and ConfigMaps.

## Prerequisites

Prepare these services and credentials before installing the chart:

1. A PostgreSQL database with backups, restore testing, TLS, and connection
   limits appropriate for the controller replica count.
2. An OIDC confidential client. Register
   `https://<controller-host>/auth/callback` exactly and map provider roles to
   Inari roles.
3. A step-ca JWK provisioner and its encrypted signing key. The controller uses
   it only to mint short-lived, CSR-bound agent tokens.
4. A controller client certificate for Zenoh.
5. A separate router certificate that covers the client Service, every
   StatefulSet pod DNS name, and any external name used by agents.
6. An HTTP ingress and a separate TCP path for Zenoh TLS.
7. A CNI that enforces NetworkPolicy if policy is enabled.

For a three-router release named `inari` in namespace `inari`, the router
certificate normally covers:

```text
inari-zenoh
inari-zenoh.inari.svc
inari-zenoh.inari.svc.cluster.local
inari-zenoh-0.inari-zenoh-headless.inari.svc.cluster.local
inari-zenoh-1.inari-zenoh-headless.inari.svc.cluster.local
inari-zenoh-2.inari-zenoh-headless.inari.svc.cluster.local
inari-zenoh.example.com
```

Adjust the names for your release, namespace, cluster domain, replica count,
and external endpoint. A missing SAN should fail readiness rather than be
worked around by disabling hostname verification.

## Create the Secrets

The chart references existing Secrets and mounts their values as files. It does
not copy credentials into values, ConfigMaps, release notes, or environment
variables.

| Values reference | Default keys | Contents |
| --- | --- | --- |
| `database.secret` | `url` | Complete PostgreSQL URL with TLS options |
| `identity.oidc.clientSecret` | `client-secret` | OIDC confidential-client secret |
| `managedGateway.certificate.stepCa.signingKey` | `provisioner-key.pem` | Encrypted step-ca provisioner key |
| `zenoh.tls.controllerSecret` | `ca.crt`, `tls.crt`, `tls.key` | Controller Zenoh client identity |
| `zenoh.tls.routerSecret` | `ca.crt`, `tls.crt`, `tls.key` | Router server and mesh identity |

Create them with the organization’s secret controller—External Secrets,
SOPS, Sealed Secrets, or a CSI provider are all reasonable. Never put secret
values in `--set`; Helm persists release values in the cluster.

The controller and router are different principals and must not share a private
key.

## Prepare values

Choose the chart version from the
[controller-chart releases](https://github.com/hadronomy/inari/releases), then
copy its defaults:

```sh
export INARI_CHART_VERSION=<version>
helm show values oci://ghcr.io/hadronomy/charts/inari \
  --version "$INARI_CHART_VERSION" > inari-values.yaml
```

At minimum, review:

- `organization`;
- `server.environment` and `server.publicUrl`;
- `identity.oidc` and its role mapping;
- every existing Secret name and key;
- `managedGateway.controllerInstanceId`;
- `managedGateway.dataPlane.publicEndpoints`;
- step-ca identity and certificate settings;
- ingress, Zenoh Service exposure, and NetworkPolicy rules;
- immutable image digests required by your release policy.

The public Zenoh endpoint is returned to agents during enrollment. It must
match the actual TCP route and the router certificate; Kubernetes cannot infer
it from a cloud load balancer.

Validate changes from the repository before installing:

```sh
mise install
mise exec -- just check-kubernetes
```

That gate runs Helm and chart-testing lint, JSON Schema negative cases, renders
against supported Kubernetes versions, validates with Kubeconform and
KubeLinter, inflates the Kustomize overlay, and checks the packaged chart.

When Docker is available, add the API-server exercise:

```sh
mise exec -- just check-kubernetes-server
```

## Install with Helm

Create the namespace and opt it into the Restricted Pod Security Standard:

```sh
kubectl create namespace inari
kubectl label namespace inari \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/enforce-version=latest \
  pod-security.kubernetes.io/audit=restricted \
  pod-security.kubernetes.io/audit-version=latest \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/warn-version=latest
```

Install atomically:

```sh
helm upgrade --install inari oci://ghcr.io/hadronomy/charts/inari \
  --version "$INARI_CHART_VERSION" \
  --namespace inari \
  --values inari-values.yaml \
  --atomic \
  --timeout 10m
```

The migration hook runs first. It takes a PostgreSQL advisory lock, applies the
embedded SeaORM migrations, and exits before controller pods roll. Production
pods set `database.migrate_on_startup=false` and refuse readiness if the schema
is behind.

Do not use `--no-hooks`; it removes the ordering guarantee between database and
application.

Verify the release:

```sh
kubectl --namespace inari get pods,svc,pdb,networkpolicy
kubectl --namespace inari rollout status deployment/inari
kubectl --namespace inari rollout status statefulset/inari-zenoh
helm test inari --namespace inari --logs
```

Finish with an OIDC sign-in, one invitation enrollment, and a Zenoh reconnect
smoke test.

## Install with Kustomize

Copy [`deploy/kustomize/inari`](../deploy/kustomize/inari) into the environment
repository or edit its values for local validation. Render and inspect the
result before applying it:

```sh
kustomize build --enable-helm deploy/kustomize/inari > inari-rendered.yaml
kubeconform -strict -summary inari-rendered.yaml
kubectl diff --filename inari-rendered.yaml
kubectl apply --server-side --filename inari-rendered.yaml
```

The overlay sets `migrations.helmHook=false`, so each chart version creates an
ordinary versioned migration Job. Concurrent Jobs remain safe because the
embedded migrator uses the same PostgreSQL advisory lock.

If your GitOps controller already has a Helm release abstraction, prefer that
over manually inflating the chart.

## Zenoh routing

The generated router configuration gives every StatefulSet pod a deterministic
ID and connects it to the stable pod DNS names. Multicast discovery is disabled.

`<release>-zenoh` serves controller and agent clients.
`<release>-zenoh-headless` exists only for router identity and mesh traffic.

`ClusterIP` is the safe default. Agents outside the cluster need a private
`LoadBalancer`, TCP proxy, or routed endpoint. Add source ranges and matching
NetworkPolicy rules before exposing the Service.

Set `zenoh.config.existingConfigMap` when another system owns the router JSON5.
The named ConfigMap must contain `zenoh.config.key`. Because the chart cannot
checksum external content, that owner must also trigger router rollouts.

## Network policy

Default policy allows same-namespace access to the controller and router,
controller egress to DNS, HTTPS, PostgreSQL, and Zenoh, router mesh traffic,
and migration egress to DNS and PostgreSQL.

It cannot guess the ingress-controller namespace, edge-agent CIDRs, or an
organization’s egress gateway. Add those paths through:

- `networkPolicy.controller.additionalIngress`;
- `networkPolicy.controller.additionalEgress`;
- `networkPolicy.zenoh.additionalIngress`;
- `networkPolicy.zenoh.additionalEgress`.

These values accept native Kubernetes policy rules. Review the rendered policy
as a security change. Portable NetworkPolicy cannot select an external service
by DNS name, so port-only egress is necessarily broad unless your cluster routes
it through a selectable gateway.

On a first Helm install, the pre-install migration Job can run before normal
release policies exist. Clusters that require isolation from the first packet
should pre-create a namespace policy or use the declarative Kustomize lifecycle.

## Health and rollout behavior

- `/healthz` answers whether the controller process and runtime are responsive.
- `/readyz` includes required PostgreSQL, identity, application-service, and
  Zenoh state.

A dependency outage removes a pod from Service endpoints without causing a
liveness restart loop. Disabled optional systems report `disabled`, not
`healthy`.

Zenoh probes its mutually authenticated TCP listener. Controller readiness and
release smoke tests provide the end-to-end session signal.

The controller uses surge-first rolling updates and a disruption budget. Zenoh
uses a StatefulSet, stable names, rolling updates, and its own disruption
budget. Revisit the PDB before reducing replicas; an impossible budget should
block voluntary disruption.

## Upgrade and recover

Read the release notes, render the new chart, and inspect the diff before every
upgrade:

```sh
helm template inari oci://ghcr.io/hadronomy/charts/inari \
  --version "$INARI_CHART_VERSION" \
  --namespace inari \
  --values inari-values.yaml > next.yaml
kubectl diff --namespace inari --filename next.yaml
```

Before a migration-bearing release, confirm a recent PostgreSQL backup and
restore exercise, migration network access, enough healthy replicas for the
roll, and an observed maintenance window.

Schema history is forward-only. Helm rollback changes Kubernetes objects; it
does not reverse database migrations. Roll back only to a binary compatible
with the schema already applied.

Inspect a failed migration with:

```sh
kubectl --namespace inari get jobs -l app.kubernetes.io/component=migration
kubectl --namespace inari logs job/inari-migrate
kubectl --namespace inari describe job/inari-migrate
```

Do not edit SeaORM’s migration table. Repair logic with a new migration or
restore PostgreSQL for physical recovery.

## Troubleshooting

### Migration cannot reach PostgreSQL

Check the Secret key, URL TLS parameters, DNS, database allowlists, and migration
egress. The application never prints the URL.

### Controller is healthy but not ready

Read `/readyz` and structured logs. Pending migrations, OIDC discovery, and
required Zenoh connectivity are the usual dependencies. Readiness is already
the correct place for them; do not weaken liveness.

### Routers do not become ready

Check Secret keys, certificate SANs, the CA chain, mesh DNS, ConfigMap key, and
NetworkPolicy. With generated configuration, confirm every StatefulSet ordinal
appears in the rendered connect endpoints.

### The UI works but agents cannot connect

HTTP ingress does not carry Zenoh. Check the TCP Service, public data-plane
endpoints, certificate names, load-balancer source ranges, and router policy.

### An ingress controller times out

Default policy permits same-namespace callers. Add the ingress controller’s
namespace selector to `networkPolicy.controller.additionalIngress`.

## Verify published charts

Tegami publishes the chart to GHCR and Cosign signs its immutable digest with
the GitHub release workflow’s OIDC identity:

```sh
cosign verify \
  --certificate-identity-regexp 'https://github.com/hadronomy/inari/.github/workflows/release\.yaml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/hadronomy/charts/inari@sha256:<digest>
```

Use the digest from the release notes. OCI tags are immutable; corrections ship
under a new chart version.
