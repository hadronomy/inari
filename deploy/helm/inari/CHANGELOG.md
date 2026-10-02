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
