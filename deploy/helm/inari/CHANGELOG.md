## inari@0.3.3

### Consume enrollment invitations atomically

The Controller now verifies an invitation, mints the one-time CA token, stores
the Agent, and consumes the invitation in one database transaction. A token or
storage failure leaves the invitation ready for the same Agent to try again.

When an Agent does not receive its enrollment response, it can send the same
request again with the same invitation. The Controller returns a new one-time
token and the original enrollment time. It does not create a second enrollment.
A changed request returns a conflict, and another Agent cannot use the
invitation.

The migration marks each `claimed` invitation as `failed`. Create a new
invitation for each of those Agents after you upgrade. Enrollment and Zenoh stay
disabled by default.

Status updates mark the current enrollment invitation online. An older invitation
cannot keep the current invitation available for certificate-token retries.

### Add signed Router policy supervision

The Router Supervisor verifies a complete, short-lived Controller policy before
it starts stock Zenoh. Agent certificates receive permissions only in their
exact namespaces. Policy replacement closes existing links, and policy expiry
stops the Router.

The management API requires a dedicated Controller certificate and a separate
policy signature. Durable generations reject older policies after restart.
Controller reconciliation and deployment remain required before managed
enrollment can enter use.

### Bind enrollment certificates to the Agent Identity

Enrollment rejects Agent identifiers and certificate requests that do not match
the protected Ed25519 identity. Certificate issuance and renewal reject incorrect
subjects, missing SANs, additional names, and duplicate names.
The Agent validates the full certificate chain against its pinned CA root and
stores the verified intermediate chain for mutual TLS. A CA response cannot
replace the pinned root.
Root bootstrap accepts exactly one pinned CA. Certificate requests trust only
that CA and require client key usage for TLS signatures. The Controller requires
a SHA-256 CA pin in step-ca mode.

The chart now keeps enrollment and Zenoh disabled by default. Provision the CA
and exact per-Agent Router policy before enabling them.

Certificate names now come from the Agent Identity. Remove
`managedGateway.certificate.stepCa.authorizedSans` from Helm values and
`step_ca_authorized_sans` from a Controller configuration before upgrading an
existing step-ca deployment.

Installed certificates now receive the same trust and identity checks before use.
The Agent repairs a cached CA against its pin and replaces obsolete certificate
names when fresh enrollment provides a one-time token. The protected key stays
unchanged. All CA endpoints require HTTPS.

Managed publication and Device Work now stop when certificate validation fails.
The Agent closes an existing session and reports the certificate error in setup.
Controller HTTPS uses separate trust before enrollment. A new invitation replaces
cached enrollment and old configured credentials without replacing the Agent key.
Concurrent enrollment calls share one request and preserve newer invitations.

Root bootstrap reads Smallstep's JSON response. Issuance and renewal use the
canonical Smallstep endpoints and retain the complete intermediate chain.

Upgrade the Agent and Controller together for gateway protocol `2026-10-03`
before enabling enrollment. Enrollment no longer sends an installed certificate,
and the Controller migration removes its unused stored certificate column.

The Controller migration removes an Agent column that old replicas still select.
Keep managed enrollment and Zenoh disabled until all Controller replicas use the
new release. A binary rollback across this migration is not supported. Recover
with the matching database backup and Release Set.

### Keep concurrent Controller documents responsive

The Controller discovers its fixed web routes once before it serves documents.
Concurrent Router construction can no longer interrupt Resource loading in an
active document stream.

### Deploy signed Router admission

The chart connects the Controller to each Router Supervisor through dedicated
management mTLS. Each Router keeps its signed policy generation on a persistent
volume. Permission changes close old connections before acknowledgment.

Lifetime refreshes preserve connections when permissions and TLS credentials
stay the same. Certificate rotation loads new credentials. Invalid credentials
and certificate expiry close Router admission until the credentials recover.

The Supervisor image runs as a nonroot user with a read-only root filesystem.
Publication requires management acknowledgment and policy expiry checks in the
built image. Published images include provenance, an SBOM, and a digest signature.

Managed Controller upgrades stop old replicas before new replicas allocate
policy generations. Signing-key replacement requires a new fleet and retained
offline state. Static Router ACL ConfigMaps and ephemeral policy storage are
removed. The chart pins matching signed Controller and Router images.

### Require complete Router acknowledgment for managed admission

The Controller now reconciles signed Agent policies across every configured
Router. Enrollment, Managed Work admission, and dispatch require current
acknowledgment. PostgreSQL allocates generations across Controller replicas and
invalidates admission when Agent credentials change.

Enrollment managers can retire an Agent's credentials through the scoped API.
Retirement preserves key history and returns success after every Router
acknowledges removal. Readiness reports an unavailable or expired Router policy.

Configure dedicated management mTLS and the policy signing key before enabling
managed enrollment. Deploy the Router Supervisor and matching Controller together.

Management certificate rotation replaces the client and its connection pool.
Invalid credentials close admission until the files recover. Policies tolerate
five seconds of Router clock lag. Command enqueue rechecks admission in the
database transaction before commit.

## inari@0.3.2

### Configure trusted OIDC ID token audiences

Set `identity.oidc.additionalIdTokenAudiences` when your identity provider
includes a trusted project identifier in ID tokens. The Controller still
requires its client ID and checks the authorized party for multiple audiences.

The chart pins a compatible Controller image by digest. Custom image selections
must use an explicit digest or tag; the chart rejects an empty selection.

## inari@0.3.1

### Trust a private PostgreSQL CA during deployment

The chart can mount an existing database CA Secret in the Controller and its
migration Job. Use `database.caCertificateSecret` with a PostgreSQL URL that
requires `sslmode=verify-full` and points `sslrootcert` to the mounted CA.

## inari@0.3.0

### Say where each configuration setting came from

`inari-server config explain` now names the layer behind every setting that is
not at its built-in default, and the file path or the environment that supplied
it.

```
2 of 75 settings are overridden:
  organization.name  /etc/inari/inari-server.toml
  server.bind        environment
```

The command previously printed the precedence order and the list of files it
had read. That answered which layers exist, not which one won a given setting,
so the effect of a stray environment variable had to be worked out by hand.
The report is read from the values the loader actually merged, so it cannot
describe a precedence the loader no longer applies.

### Accept an explicit --redact

`config print-effective` now takes `--redact` alongside `--no-redact`. Output
is redacted unless `--no-redact` is given, exactly as before; the new flag
states that intent rather than relying on the default.

### Restrict managed Zenoh authority

The Controller access rules now permit managed command delivery, history
replies, and Agent observations with access control enabled.

Generated router configuration reserves command and storage-receipt authority
for Controller and router certificates. Before upgrading, set
`zenoh.config.accessControl.trustedPeerCommonNames` to their exact certificate
common names. The defaults are `inari-controller` and `inari-router`. Reserve
these names in the CA policy so Agents cannot obtain them.

### Configure managed report dispatch on Kubernetes

The chart now provides Dispatch Envelope signing-key mounts and OpenBao payload
protection settings. Managed dispatch uses a projected ServiceAccount token with
an explicit audience. An optional Secret supplies the OpenBao CA bundle.

Enable `managedGateway.dispatch.enabled` after provisioning the signing key and
OpenBao role. The chart now uses gateway protocol `2026-09-06` and a 16 MiB HTTP
body limit for base64-encoded Report PDFs. Configure the ingress body limit to
match.

### Publish the Controller runtime

Main releases now publish a signed Controller image with its matching browser
assets. Deployments can pin the image digest and verify its source identity.

### Retire Agent verification keys when enrollment rotates them

Retired keys can replay stored observations for reconciliation. They cannot authorize new observations. A database migration retires obsolete keys and requires new enrollment when historical keys are ambiguous. Managed Work preserves the exact signed idempotency key. Controller dispatch permission follows the Helm dispatch configuration.

## inari@0.2.1

### Publish the controller chart through Tegami

The Inari Helm chart now publishes to GHCR under its own version lifecycle. Each immutable OCI artifact is signed with keyless Cosign, verified before completion, and safe to resume after an interrupted release.
