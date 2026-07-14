# Release process

Inari uses [Tegami](https://tegami.fuma-nama.dev) to keep versioning and
publication in one recoverable workflow. Bun manages the release workspace;
Tegami itself runs on the Node.js version pinned in [`mise.toml`](../mise.toml).
The release packages are built with tsdown before Node loads them, so CI uses
the same package exports as any other workspace consumer.

## Release units

There are two independently releasable groups:

| Group | Contents | Version policy |
| --- | --- | --- |
| `edge` | agent, Device Center, brand package, and Windows MSIX | synchronized version, bump, and Git tag |
| `controller-chart` | Helm chart | independent version and Git tag |

The Python packages are source inputs for the edge bundle. They are never
published to PyPI. The public edge artifact is the signed **Inari Device
Center** MSIX, which contains both the tray application and the agent service.

## Describe a change

Every releasable change starts with a pending changelog under `.tegami/`.
The interactive command chooses packages changed in the working tree and writes
the correctly shaped entry:

```sh
mise exec -- bun run release -- changelog
```

Use `group:edge` when the agent, tray, brand assets, and installer need to move
together. Use `group:controller-chart` for chart-only changes. A focused package
ID is appropriate only when its release group does not need a synchronized
bump. [`AGENTS.md`](../AGENTS.md) contains the canonical frontmatter examples.

Preview the resulting versions before opening a pull request:

```sh
mise exec -- bun run release:preview
```

## What happens on `main`

The [release workflow](../.github/workflows/release.yaml) follows Tegami's
two-stage lifecycle:

1. When no publish lock is present, Tegami opens or updates the **Version
   Packages** pull request. That pull request consumes pending changelogs,
   updates package versions and changelogs, and writes the publish lock.
2. Merging the Version Packages pull request triggers the workflow again. CI
   validates the repository, builds any required Windows bundle, and publishes
   exactly the packages recorded in the lock.
3. Tegami verifies the remote state before publishing. Completed chart pushes,
   GitHub releases, and release assets are treated as complete; missing or
   mismatched work is retried.

Pull requests run Tegami's unprivileged preview workflow. A separate
`workflow_run` job downloads only the generated Markdown artifact and posts the
comment with trusted repository code. Pull-request code never receives a token
with write access. GitHub suppresses ordinary pull-request events created by
its workflow token, so the version job explicitly dispatches the same read-only
preview and Kubernetes checks against the generated branch.

## Local maintainer commands

```sh
mise exec -- just check-release       # format, lint, type-check, test, and build
mise exec -- bun run release:preview  # inspect pending version changes
mise exec -- bun run release:status   # inspect the current publish plan
mise exec -- bun run release:dry-run  # execute publish planning without writes
```

Do not edit `.tegami/publish-lock.yaml` or generated package changelogs by hand.
When publication fails, leave the lock intact, correct the underlying
credential or remote-state problem, and rerun the Release workflow. The Helm
and MSIX plugins are designed to resume from immutable remote state.

## Helm publication

The chart is packaged from [`deploy/helm/inari`](../deploy/helm/inari), pushed
to `oci://ghcr.io/hadronomy/charts`, and signed at its immutable OCI digest with
keyless Cosign. Local preflight never contacts the registry. Remote resolution
happens only while Tegami determines publish status, which keeps ordinary
development and pull-request previews credential-free.

Chart validation remains independent from publication. Helm linting and
rendering, JSON Schema validation, Kustomize, Kubeconform, KubeLinter, and the
kind API-server exercise must pass before the publish job can run.

## Windows publication

The Windows job runs in the protected `windows-release` environment and expects
these secrets:

- `WINDOWS_SIGNING_PFX_BASE64`
- `WINDOWS_SIGNING_PFX_PASSWORD`
- `WINDOWS_CODE_SIGNING_ROOT_CERT_BASE64`

The job signs Inari's Device Center and service executables, then signs the
MSIX whose block map protects the complete packaged payload. It verifies each
resulting signature, emits an SPDX SBOM, and records SHA-256 checksums. GitHub
artifact attestations provide build
provenance in addition to Authenticode; they do not replace Windows code-signing
trust.

The self-managed alpha signing hierarchy does not currently depend on an
external timestamp authority. Consequently, Windows signatures remain valid
only during the publisher certificate's validity period. Production releases
must adopt Microsoft Trusted Signing or an independently operated RFC 3161
authority before relying on signatures beyond that period.

Provisioning and rotation are described in
[`windows-code-signing.md`](windows-code-signing.md). Installation and
enterprise trust deployment are described in [`windows.md`](windows.md).

## Dependency policy

Tegami and `@tegami/pip` are exact-pinned while the custom plugins depend on
their current extension interfaces. Bun's lockfile is committed. Upgrade the
pins deliberately, read the upstream changes, run the plugin tests and preview,
and confirm both interrupted-publish recovery paths before merging.

The custom Helm and MSIX plugins can be retired only when an upstream package
provides the same manifest preservation, remote status checks, signing, and
retry semantics. A shorter dependency list is valuable only when it preserves
the operational contract.
