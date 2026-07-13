# Running Inari on Kubernetes

This guide describes the supported production deployment for an Inari
controller. It is written for the engineers who own the cluster, database,
identity provider, certificate authority, and private network—not only for the
person running `helm upgrade`.

The deployment has two independent workloads:

- `inari-server` is a stateless HTTP application. It serves the operator
  console, the typed Inari API, enrollment, and the Axum-native Zenoh HTTP
  compatibility surface. PostgreSQL holds controller state and OIDC sessions.
- `zenohd` is the managed data-plane router. It accepts mutually authenticated
  agent and controller connections and routes the Inari keyspace. It is not an
  HTTP sidecar and it does not share the controller process lifecycle.

PostgreSQL, the OpenID Connect provider, step-ca, and the external secret
controller remain organization-owned services. The chart does not install or
silently configure them.

## Supported installation models

Helm is the primary distribution and lifecycle owner. Use it unless your
deployment platform specifically requires Kustomize.

The repository also contains a Kustomize overlay at
[`deploy/kustomize/inari`](../deploy/kustomize/inari). It inflates the same
local chart and changes database migrations from Helm hooks into ordinary,
chart-versioned Jobs. This is necessary because `kubectl apply` does not
implement Helm hook ordering or deletion policies.

Do not point Helm and Kustomize at the same release. Two reconcilers owning the
same Deployments, Services, Jobs, and ConfigMaps will produce ambiguous
rollouts and unreliable migrations.

## Before the first install

Prepare these dependencies before creating the release:

1. A PostgreSQL database reachable from the target namespace. Backups,
   point-in-time recovery, maintenance windows, and connection limits belong
   to the database operator.
2. An OIDC confidential client. Register
   `https://<controller-host>/auth/callback` as an exact redirect URI and map
   provider claims to the Inari roles in the values file.
3. A step-ca JWK provisioner whose encrypted signing key is available through
   a Kubernetes Secret. The controller uses it only to mint short-lived,
   CSR-bound agent certificate tokens.
4. A TLS certificate for the controller's Zenoh client identity.
5. A separate TLS certificate for the Zenoh routers. It must be valid for the
   internal Service, the StatefulSet pod DNS names, and any external name used
   by agents.
6. A CNI that enforces `NetworkPolicy` when network isolation is enabled.
7. An ingress or private load balancer capable of carrying the required
   protocols. HTTP ingress and Zenoh TLS are separate endpoints.

For a release named `inari` in namespace `inari`, a three-router certificate
normally includes these DNS names:

```text
inari-zenoh
inari-zenoh.inari.svc
inari-zenoh.inari.svc.cluster.local
inari-zenoh-0.inari-zenoh-headless.inari.svc.cluster.local
inari-zenoh-1.inari-zenoh-headless.inari.svc.cluster.local
inari-zenoh-2.inari-zenoh-headless.inari.svc.cluster.local
inari-zenoh.example.com
```

Adjust the list for the release name, namespace, cluster domain, replica count,
and external endpoint. Zenoh verifies peer names during router-mesh
connections; an incomplete SAN set prevents readiness instead of weakening
hostname verification.

## Secret contract

The chart consumes existing Secrets. It never renders secret values into a
ConfigMap, Helm release record, Pod environment variable, log, or note.

| Secret reference | Required keys | Purpose |
|---|---|---|
| `database.secret` | `url` by default | PostgreSQL URL, including credentials and TLS parameters |
| `identity.oidc.clientSecret` | `client-secret` by default | OIDC confidential-client secret |
| `managedGateway.certificate.stepCa.signingKey` | `provisioner-key.pem` by default | Encrypted step-ca JWK provisioner key |
| `zenoh.tls.controllerSecret` | `ca.crt`, `tls.crt`, `tls.key` | Controller client identity and trust root |
| `zenoh.tls.routerSecret` | `ca.crt`, `tls.crt`, `tls.key` | Router server/mesh identity and trust root |

Use the organization's existing secret delivery system. External Secrets
Operator, SOPS, Sealed Secrets, and CSI secret providers are all reasonable,
provided the final files appear under the keys above. Do not pass secret values
with `--set`; Helm stores release values in the cluster.

Keep the controller and router private keys separate. They represent different
security principals even when the certificates chain to the same private CA.
Rotate a mounted Secret by updating it through its owner and changing
`controller.rolloutNonce` or the relevant pod annotation to make the new
identity take effect immediately.

## Preparing values

Start from the packaged defaults:

```sh
helm show values oci://ghcr.io/hadronomy/charts/inari --version 0.2.0 > inari-values.yaml
```

At minimum, replace:

- `organization.*`;
- `server.publicUrl`;
- `identity.oidc.*`;
- every existing Secret name and key;
- `managedGateway.controllerInstanceId`;
- `managedGateway.dataPlane.publicEndpoints`;
- all `managedGateway.certificate.stepCa.*` identity fields;
- ingress and Zenoh Service configuration;
- NetworkPolicy ingress and egress rules for the cluster topology;
- image digests for environments that require immutable artifacts.

Run the repository validator before installation:

```sh
mise install
just check-kubernetes
```

This gate performs strict chart linting, chart-testing lint, values-schema
negative testing, rendering against the oldest and current supported
Kubernetes versions, strict Kubeconform validation, KubeLinter policy checks,
Kustomize inflation, shell linting, workflow linting, YAML linting, and a
package round trip.

`just check-kubernetes-server` adds server-side dry-run validation against a
pinned Kubernetes node image in kind. It requires a working Docker daemon. The
API server catches admission and structural rules that JSON Schema validation
cannot model.

## Installing with Helm

Create and label the namespace before installing. Pod Security Admission is a
namespace concern and is intentionally not hidden inside the chart:

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

Install or upgrade atomically:

```sh
helm upgrade --install inari oci://ghcr.io/hadronomy/charts/inari \
  --version 0.2.0 \
  --namespace inari \
  --values inari-values.yaml \
  --atomic \
  --timeout 10m
```

Helm runs the fixed-name migration hook first. The Job takes a PostgreSQL
advisory lock, applies embedded forward migrations, and exits. Only after it
succeeds does Helm roll the controller. Application pods keep
`database.migrate_on_startup=false` and refuse readiness when schema history is
behind the binary.

Do not use `--no-hooks`. That bypasses the production database owner and can
roll an application that its database is not ready to serve.

## Installing with Kustomize

Edit [`deploy/kustomize/inari/values.yaml`](../deploy/kustomize/inari/values.yaml)
or copy the overlay into the environment repository, then render it explicitly
with Helm support enabled:

```sh
kustomize build --enable-helm deploy/kustomize/inari > inari-rendered.yaml
kubeconform -strict -summary -kubernetes-version 1.36.2 inari-rendered.yaml
kubectl diff --filename inari-rendered.yaml
kubectl apply --server-side --filename inari-rendered.yaml
```

The overlay sets `migrations.helmHook=false` and disables Helm tests. Each chart
version therefore renders a new migration Job. The operation remains safe when
multiple reconcilers race because the embedded migrator serializes with a
PostgreSQL advisory lock.

The overlay is a clean ownership alternative, not a post-render patch for an
existing Helm release. A GitOps controller that natively supports Helm should
prefer its Helm release abstraction and use Kustomize only as that controller's
supported post-render mechanism.

## Router topology and exposure

The generated Zenoh configuration gives every StatefulSet pod a deterministic
ID derived from the Helm release and pod ordinal. Routers connect to all stable
pod DNS names, providing an explicit mesh without multicast discovery.

Two Services are rendered:

- `<release>-zenoh` is the client-facing Service used by the controller and,
  when exposed, by agents.
- `<release>-zenoh-headless` publishes StatefulSet identities used only by the
  router mesh.

`zenoh.service.type=ClusterIP` is the safe default. Private agents outside the
cluster usually require an internal `LoadBalancer`, private TCP proxy, or
equivalent routed endpoint. Configure `loadBalancerSourceRanges` and the
matching NetworkPolicy ingress rules before changing the Service type.

The endpoint sent to enrolled agents comes from
`managedGateway.dataPlane.publicEndpoints`; Kubernetes cannot infer it from a
provider-specific load balancer. Keep that value aligned with the TLS
certificate and actual network path.

Set `zenoh.config.existingConfigMap` to delegate router configuration to the
organization. The ConfigMap must contain `zenoh.config.key`. The chart then
stops rendering and checksumming router configuration; the external owner must
trigger rollouts when its content changes.

## Network policy

The default policies allow:

- same-namespace HTTP access to the controller;
- controller access to DNS, HTTPS, PostgreSQL, and the Zenoh pods;
- same-namespace TLS access to Zenoh;
- router-to-router mesh traffic and DNS;
- migration egress to DNS and PostgreSQL, with no ingress.

They do not guess which namespace contains an ingress controller or which CIDR
contains edge agents. Add those paths using:

- `networkPolicy.controller.additionalIngress`;
- `networkPolicy.controller.additionalEgress`;
- `networkPolicy.zenoh.additionalIngress`;
- `networkPolicy.zenoh.additionalEgress`.

The values accept native `NetworkPolicyIngressRule` and
`NetworkPolicyEgressRule` objects. Treat each addition as a security change and
review the rendered manifest. Port-only egress rules for HTTPS and PostgreSQL
are intentionally coarse because portable Kubernetes policy cannot select an
external service by DNS name. Organizations with egress gateways should narrow
them to the gateway namespace or CIDR.

The pre-install migration hook can begin before ordinary release resources,
including its NetworkPolicy, exist on a first installation. The Job has no
service-account token, receives only the database Secret, and exits after the
migration. Clusters that require network isolation from the first packet should
pre-create an equivalent namespace policy or use the declarative Kustomize
mode.

## Health and rollout behavior

The controller probes have deliberately different meanings:

- `/healthz` is the startup and liveness signal. It answers whether the process
  and runtime are responsive.
- `/readyz` is the readiness signal. It includes required PostgreSQL,
  application-service, identity, and Zenoh state.

A dependency outage removes a controller pod from Service endpoints without
causing a liveness restart loop. Optional disabled subsystems report disabled,
not healthy.

Zenoh uses TCP startup, readiness, and liveness probes on its mutually
authenticated listener. The probe establishes a socket but does not perform a
Zenoh session; release tests and controller readiness provide the end-to-end
signal.

The controller Deployment uses surge-first rolling updates and a disruption
budget. Zenoh uses a StatefulSet, stable pod names, a disruption budget, and
rolling updates. Review PDB values when reducing replicas: an impossible
budget correctly prevents voluntary disruption.

## Upgrades, rollback, and recovery

Read the release notes and chart changes before every upgrade. Render the old
and new values locally and inspect the diff:

```sh
helm template inari oci://ghcr.io/hadronomy/charts/inari \
  --version 0.2.0 \
  --namespace inari \
  --values inari-values.yaml > next.yaml
kubectl diff --namespace inari --filename next.yaml
```

Database changes are forward-only and use expand-and-contract discipline. A
Helm rollback changes Kubernetes objects; it does not reverse schema history.
Only roll back to a binary that is compatible with the already-applied schema.

Before a migration-bearing release:

1. Confirm a recent PostgreSQL backup and restore procedure.
2. Confirm the migration Job can reach the database.
3. Confirm disruption budgets leave enough healthy replicas.
4. Apply the release during an observed window.
5. Wait for both controller and router rollouts.
6. Run `helm test` and perform an enrollment/data-plane smoke test.

Inspect a failed migration before retrying:

```sh
kubectl --namespace inari get jobs -l app.kubernetes.io/component=migration
kubectl --namespace inari logs job/inari-migrate
kubectl --namespace inari describe job/inari-migrate
```

The next Helm attempt removes the previous fixed-name hook before creating a
new one. Do not manually edit SeaORM's migration history.

## Routine operations

Useful checks:

```sh
kubectl --namespace inari get deploy,statefulset,pods,svc,pdb,networkpolicy
kubectl --namespace inari rollout status deployment/inari
kubectl --namespace inari rollout status statefulset/inari-zenoh
kubectl --namespace inari logs deployment/inari --all-pods=true
helm test inari --namespace inari --logs
```

Run `inari-server database status` from an image matching the deployed release
when diagnosing schema readiness. Never use `config print-effective
--no-redact` in a support bundle or shared terminal transcript.

For certificate rotation, update the relevant Secret, force a controlled
rollout, and confirm that old sessions close at certificate expiry. For OIDC
rotation, update only the OIDC Secret and roll the controller. For PostgreSQL
credential rotation, coordinate the database and Secret updates so at least
one accepted credential remains valid throughout the rollout.

## Troubleshooting

### The migration Job cannot connect

Check the Secret key name, database TLS parameters, DNS, and egress policy.
The chart expects a complete PostgreSQL URL in the mounted file. The URL is
never printed by the application.

### Controller pods are healthy but not ready

Read `/readyz` and structured logs. The most common causes are pending database
migrations, invalid OIDC discovery, or unavailable Zenoh connectivity. Do not
weaken liveness probes to hide dependency failures; readiness is already the
correct control plane.

### Zenoh pods never become ready

Verify the router Secret keys, certificate SANs, CA chain, ConfigMap key, and
NetworkPolicy mesh egress. With generated configuration, inspect the rendered
ConfigMap and confirm every StatefulSet ordinal appears in `connect.endpoints`.

### The HTTP UI works but agents cannot connect

The HTTP ingress does not expose Zenoh. Check the TCP Service or private load
balancer, `managedGateway.dataPlane.publicEndpoints`, router certificate SANs,
source ranges, and Zenoh NetworkPolicy ingress.

### An ingress controller receives connection timeouts

The default policy permits same-namespace callers only. Add the ingress
controller namespace selector to
`networkPolicy.controller.additionalIngress`, as shown in the Kustomize
example.

## Publishing the chart

Chart releases use tags of the form `helm-v<chart-version>`. The release
workflow verifies the tag against `Chart.yaml`, packages the chart, pushes it
to `ghcr.io/<owner>/charts/inari`, and signs the OCI digest with keyless Cosign
using GitHub's OIDC identity.

Consumers can verify a release with the repository identity used by the
workflow:

```sh
cosign verify \
  --certificate-identity-regexp 'https://github.com/hadronomy/odoo-iot-agent/.github/workflows/helm-release.yaml@refs/.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/hadronomy/charts/inari@sha256:<digest>
```

OCI tags must remain immutable. Publish corrections under a new chart version.
After registering the OCI chart in Artifact Hub, publish its repository
metadata artifact to obtain verified-publisher status; do not invent a
`repositoryID` before Artifact Hub assigns one.

## Maintainer references

The distribution follows the upstream guidance for [Helm chart
structure](https://helm.sh/docs/topics/charts/), [chart
tests](https://helm.sh/docs/topics/chart_tests/), [OCI
registries](https://helm.sh/docs/topics/registries/), [Kustomize composition](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/),
and the Kubernetes [Restricted Pod Security
Standard](https://kubernetes.io/docs/concepts/security/pod-security-standards/).
Static manifest validation uses Kubeconform in strict mode; it is supplemented
by kind because JSON Schema cannot reproduce every API-server admission rule.
