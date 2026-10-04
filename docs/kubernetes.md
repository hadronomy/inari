# Run Inari on Kubernetes

This guide is for the team that owns the cluster and the services around it.
An Inari production deployment assumes that PostgreSQL, OIDC, step-ca, ingress,
and secret delivery already have clear operators.

The chart installs two workloads:

- `inari-server`, a stateless Axum/Leptos controller;
- `inari-router`, a StatefulSet of Supervisors that own stock Zenoh processes.

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
   Inari roles. Set `identity.oidc.issuerUrl` to the exact discovery `issuer`
   value. Preserve its trailing slash when present.
3. A step-ca JWK provisioner and its encrypted signing key. The controller uses
   it only to mint short-lived, CSR-bound agent tokens.
4. A controller client certificate for Zenoh.
5. A separate Router certificate with server and client usage that covers the client Service, every
   StatefulSet pod DNS name, and any external name used by agents.
6. A dedicated management CA, separate Router server and Controller client
   certificates, and an Ed25519 policy key pair.
7. An HTTP ingress and a separate TCP path for Zenoh TLS.
8. A CNI that enforces NetworkPolicy if policy is enabled.

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
| `zenoh.tls.routerSecret` | `ca.crt`, `tls.crt`, `tls.key` | Router server, mesh, and probe identity |
| `zenoh.management.controllerSecret` | `ca.crt`, `tls.crt`, `tls.key` | Dedicated Controller management client identity |
| `zenoh.management.routerSecret` | `ca.crt`, `tls.crt`, `tls.key` | Dedicated Router management server identity |
| `managedGateway.routerPolicy.signingKey` | `signing-key.pem` | Ed25519 PKCS#8 PEM policy signing key |
| `zenoh.signingPublicKey` | `signing-key.hex` | Matching public key, 64 hexadecimal characters |
| `managedGateway.dispatch.signingKey` | `signing-key.der` | Ed25519 PKCS#8 DER key for Dispatch Envelopes |
| `managedGateway.payloadProtection.caCertificateSecret` | configured key | Optional PEM CA bundle for OpenBao |

Create them with the organization’s secret controller—External Secrets,
SOPS, Sealed Secrets, or a CSI provider are all reasonable. Never put secret
values in `--set`; Helm persists release values in the cluster.

The controller and router are different principals and must not share a private
key.

The generated Zenoh policy reserves certificate common names `inari-controller`
and `inari-router` for these principals. Set
`managedGateway.routerPolicy.trustedPeerCommonNames` to the exact common names
in your certificates. The router SANs still need to cover its DNS endpoints.
The CA must never issue these reserved common names to an Agent.

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
- `database.caCertificateSecret` for a private PostgreSQL CA;
- `managedGateway.controllerInstanceId`;
- `managedGateway.dataPlane.publicEndpoints`;
- step-ca identity and certificate settings;
- ingress, Zenoh Service exposure, and NetworkPolicy rules;
- immutable image digests required by your release policy.

The public Zenoh endpoint is returned to agents during enrollment. It must
match the actual TCP route and the router certificate; Kubernetes cannot infer
it from a cloud load balancer.

Some OIDC providers include additional audiences in ID tokens. ZITADEL includes
the application project ID. Put each trusted identifier in
`identity.oidc.additionalIdTokenAudiences` in Helm values, or
`identity.oidc.additional_id_token_audiences` in the Controller TOML configuration.
The default is an empty list. The token must still include the Controller client ID.
For multiple audiences,
its `azp` claim must identify that client. The Controller rejects other audiences
and checks the token signature, exact issuer, expiry, and login nonce.
This setting does not change the Managed Workload Interface audience.

The chart pins a compatible Controller image by digest. An image override must
understand the configuration that the chart emits. Set `image.digest` to select
another immutable image. To use an explicit tag, clear `image.digest` and set
`image.tag`. An empty image selection is rejected.

Validate changes from the repository before installing:

```sh
mise install
mise exec -- just check-kubernetes
```

That gate runs Helm and chart-testing lint, JSON Schema negative cases, renders
against supported Kubernetes versions, validates with Kubeconform and
KubeLinter, inflates the Kustomize overlay, and checks the packaged chart.

### Enable managed report and label work

Managed dispatch is disabled until its security dependencies are provisioned.
Create the Dispatch Envelope signing-key Secret and an OpenBao Transit key.
Then configure:

```yaml
managedGateway:
  dispatch:
    enabled: true
    epoch: 1
    signingKeyId: production-dispatch-1
    signingKey:
      name: inari-managed-dispatch
      key: signing-key.der
  payloadProtection:
    address: https://openbao.example.com
    kubernetesRole: inari-controller
    serviceAccountTokenAudience: openbao
    transitKeyName: inari-managed-payload
```

Bind the OpenBao Kubernetes role to the Controller ServiceAccount and namespace.
Its audience must match `serviceAccountTokenAudience`. The chart mounts a
short-lived projected token only when dispatch is enabled. General Kubernetes
API token mounting remains disabled. Configure the role's Transit permissions
as described in [Managed Payload storage](managed_payloads.md).

Set `payloadProtection.caCertificateSecret` when OpenBao uses a private CA.
Allow Controller egress to OpenBao through NetworkPolicy. The default policy
allows HTTPS on port 443; an internal endpoint on port 8200 needs an explicit
egress rule.

Keep the Dispatch Epoch consistent across Controller replicas. Recovery must
advance it through the coordinated recovery procedure. Changing the signing key
or its identifier also requires updated Agent enrollment trust.

The chart uses gateway protocol `2026-10-03` and a 16 MiB HTTP body limit.
The limit fits a 10 MiB Report PDF after base64 encoding. Set the ingress body
limit to at least 16 MiB as well. Enabling dispatch rejects smaller Controller
body limits. Report Bindings and tested Device Capabilities remain required.

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

The chart generates one Supervisor TOML file per StatefulSet ordinal. Each file
has a distinct 32-digit hexadecimal Router ID and excludes its own mesh address.
The Controller receives the same IDs and each pod's HTTPS management origin.
The shared `managedGateway.routerPolicy.fleetId` stays fixed across upgrades.

`<release>-zenoh` carries Controller and Agent data-plane traffic.
`<release>-zenoh-headless` carries mesh traffic and management requests.
It publishes unready pod addresses so the first policy can arrive before data
readiness. Management startup and liveness probes do not depend on a policy.
Readiness requires the data-plane listener.

`ClusterIP` is the default. Agents outside the cluster need a private TCP path.
Add source ranges and matching NetworkPolicy rules before exposing the Service.
The public Service never exposes management.

Routers accept complete signed policy over dedicated management mTLS. Each
Agent certificate receives only its own namespace. Permission changes close
established links before acknowledgment. A signed lifetime refresh preserves
links when authority and TLS material stay unchanged. Invalid TLS material,
certificate expiry, or policy expiry closes the data plane.
Signed Agent State Envelopes authenticate Print Job observations separately.

The chart requires an Inari Router image and persistent policy storage.
Custom static ACL ConfigMaps and ephemeral policy storage are unavailable.
Every Router keeps its highest accepted generation in a private `policy`
directory below its PVC mount. The generated runtime file includes private TLS
material. Protect PVC backups as credentials, and retain the PVCs across Pod
replacement. PostgreSQL generations must never move behind this stored state.

The dedicated management CA is separate from the data-plane CA. The management
Controller certificate uses the exact `zenoh.management.controllerCommonName`
and client certificate usage. This name cannot be a data-plane peer or Agent.
Router management certificates cover every headless pod DNS name.
Router data-plane certificates require server and client usage for mesh and
readiness connections. No Router mounts a Controller private key.

### Rotate the policy signing key

Managed Controller deployments use `Recreate` to stop old replicas before new
replicas allocate generations. This creates an API outage during an upgrade.
Local Device Work can continue.

The Router verifier accepts one public key. Its persisted policy is signed by
that key. Changing the key in place makes startup fail signature validation.
A rolling key rotation cannot use overlapping signing keys.

Rotation uses a replacement fleet with a new `fleetId`, new workload names,
and fresh Router PVCs. Stop all old Controller and Router pods first. Keep the
same Organization and PostgreSQL database so generation allocation continues.
Retain the old PVCs offline for audit and recovery. Never mount them in the new
fleet or restore an older PostgreSQL generation.

Provision the matching private and public key Secrets for the replacement
fleet. Its management and data-plane certificates must cover the new pod DNS
names. The new Controller config includes every replacement Router origin.
It allocates a higher signed generation before managed admission can resume.
Move the public TCP path and HTTP ingress only after all Routers and the
Controller become ready. Keep the old fleet stopped after this transfer.

This operation requires a coordinated maintenance window and a reviewed
Release Set. Routine TLS certificate rotation uses the mounted certificate
files and does not replace the policy signing key or fleet.

## Network policy

Default policy allows same-namespace access to the controller and router,
Controller egress to DNS, HTTPS, PostgreSQL, Zenoh, and Router management, mesh traffic,
and migration egress to DNS and PostgreSQL. Only Controller pods can reach
Router management under the default policy.

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
required Zenoh connectivity, and complete Router acknowledgment are dependencies. Readiness is already
the correct place for them; do not weaken liveness.

### Routers do not become ready

Check Secret keys, certificate usage and SANs, both CA chains, pod DNS, policy
signing keys, and NetworkPolicy. Every ordinal must appear in the Controller
management origins. Each Router excludes itself from mesh endpoints. An open
management listener does not prove current data-plane authority.

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
