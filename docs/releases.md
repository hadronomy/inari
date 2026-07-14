# Releasing Inari

Inari uses [Tegami](https://tegami.fuma-nama.dev) to turn reviewed changelog
entries into versioned, recoverable releases. Bun manages the release-tool
workspace, while Tegami runs on the Node.js version pinned in `mise.toml`.

## Release groups

There are two release lines:

| Group | Ships | Versioning |
| --- | --- | --- |
| `edge` | Agent, Device Center, shared brand package, and Windows MSIX | One synchronized version and tag |
| `controller-chart` | Helm chart | Independent version and tag |

The Python packages are bundled into Device Center and are not published to
PyPI.

## Add a release note to the pull request

Every user-visible or operational change needs a pending Markdown file under
`.tegami/`. The easiest way to create one is:

```sh
mise exec -- bun run release -- changelog
```

Choose `group:edge` for changes that ship in the agent, tray, brand assets, or
MSIX. Choose `group:controller-chart` for chart-only changes. Write the note for
the person receiving the release: say what changed and why they will notice,
without narrating the implementation history.

Preview the version plan before opening the pull request:

```sh
mise exec -- bun run release:preview
```

Do not edit `.tegami/publish-lock.yaml` or package `CHANGELOG.md` files by hand.

## Version and publish

After an ordinary change reaches `main`, the release workflow opens or updates
the **Version Packages** pull request. That pull request consumes pending notes,
updates versions and changelogs, and records the exact publish plan.

Merging it starts publication. Tegami reads the lock, verifies remote state,
and resumes any incomplete work rather than creating duplicate tags or assets.
Leave the lock in place when a publish fails; fix the cause and rerun the
workflow.

Useful local commands:

```sh
mise exec -- just check-release
mise exec -- bun run release:preview
mise exec -- bun run release:status
mise exec -- bun run release:dry-run
```

Pull-request previews run without write credentials. A separate trusted
workflow posts the rendered plan back to the pull request.

## Helm releases

The Helm plugin packages `deploy/helm/inari`, pushes it to
`oci://ghcr.io/hadronomy/charts`, and signs the immutable OCI digest with
keyless Cosign. Kubernetes linting, schema checks, Kustomize rendering,
Kubeconform, KubeLinter, and the kind exercise run before publication and remain
independent from the registry push.

Cosign uses the GitHub Actions OIDC identity of the release workflow. There is
no long-lived signing key to provision. Verification binds the signature to the
repository, workflow file, branch, and public transparency-log entry.

## Windows releases

The protected Windows job builds one MSIX containing Device Center and the
agent service. It signs the Inari executables and package, validates the signing
chain with Windows machine stores, writes SHA-256 checksums, produces an SPDX
SBOM, and asks GitHub to attest the MSIX provenance.

Windows signing depends on the `windows-release` environment secrets described
in [the signing runbook](windows-code-signing.md). Certificate trust and
installation steps for operators live in [the Windows guide](windows.md).

## Updating release dependencies

Tegami and `@tegami/pip` are exact-pinned because the local Helm and MSIX
plugins depend on their extension interfaces. Review upstream changes before
updating them, refresh `bun.lock`, and run the plugin tests plus a full release
preview. Exercise interrupted-publish recovery whenever registry or asset-state
logic changes.
