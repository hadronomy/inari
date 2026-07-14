# Windows signing runbook

This runbook is for maintainers of the Device Center release pipeline. End-user
installation is covered in [the Windows guide](windows.md).

Inari uses a private signing hierarchy for alpha releases. It is deliberately
separate from step-ca agent identities and from Kubernetes workload PKI. A key
that can publish Windows software must not also authorize devices or workloads.

## Certificate hierarchy

The hierarchy has three levels:

| Certificate | Subject | Lifetime and custody |
| --- | --- | --- |
| Publisher root | `CN=Pablo Hernández Jiménez Code Signing Root CA` | Long-lived, offline, publisher-owned |
| Inari issuer | `CN=Inari Code Signing Issuing CA` | Project-scoped, offline, path length zero |
| Publisher leaf | `CN=Pablo Hernández Jiménez` | One year, rotated through the release environment |

The leaf subject must exactly match the `Publisher` value in the MSIX manifest.
The public handle and Inari brand belong in filenames and display metadata, not
in the legal publisher subject.

The package signature contains the leaf. Managed Windows devices must also
receive the Inari issuer in the Local Machine intermediate store and the
publisher root in the Local Machine root store. SignTool’s `/ac` option is for
SPC cross-certificates; it is not a way to attach an ordinary private issuing
CA to an MSIX signature.

## Provision the hierarchy

Run the provisioning script on the signing workstation with the versions pinned
by Mise. Choose a new directory outside the repository and synchronized storage:

```sh
mise install step
mise exec -- scripts/provision-windows-signing.sh /secure/removable/inari-signing
```

The script creates encrypted root and issuer keys, a publisher key and
certificate, and `publisher.pfx`. Before copying anything elsewhere:

1. Inspect all three certificates with `step certificate inspect`.
2. Verify the leaf through the issuer and root with `openssl verify`.
3. Move `root.key` and `issuer.key` into separate offline storage.
4. Keep encrypted backups and a written recovery procedure.
5. Export only `publisher.pfx` and the public root certificate to the release
   setup.

Never commit private keys, PFX files, passwords, or secret encodings. Public
certificates and fingerprints become release assets only after the hierarchy is
in service.

## Configure GitHub Actions

Create a protected environment named `windows-release`. Restrict it to `main`
and require a maintainer to approve each deployment. Store these secrets:

- `WINDOWS_SIGNING_PFX_BASE64` — base64-encoded `publisher.pfx`;
- `WINDOWS_SIGNING_PFX_PASSWORD` — the PFX export password;
- `WINDOWS_CODE_SIGNING_ROOT_CERT_BASE64` — the public root certificate in DER
  or PEM form.

The PFX contains the leaf, its private key, and the public Inari issuer. It does
not contain either CA private key.

The Windows job validates the certificate constraints and chain without network
retrieval, signs the two Inari executables and the MSIX, and verifies each
signature against the supplied root and issuer. It then imports the two public
CA certificates into the same Local Machine stores used by App Installer,
checks the MSIX with Windows Authenticode, and removes any certificates it added.
That final test catches the exact trust path users depend on.

Every release includes the root, issuer, SHA-256 fingerprints, package
checksums, an SPDX SBOM, and GitHub provenance. These artifacts make a release
auditable; they do not replace an independently approved root fingerprint.

## Rotate the publisher leaf

Issue a new leaf from the offline Inari issuer before the current certificate
expires. Keep the subject unchanged, generate a new private key, and test an
upgrade from the latest published MSIX before replacing the GitHub secret.

The alpha hierarchy has no external timestamp authority. A signature therefore
remains useful only while its publisher leaf is valid. Publish replacement
artifacts before expiry. Long-lived production distribution should move to
Microsoft Artifact Signing or a separately operated RFC 3161 timestamp service
with its own trust and key-management boundary.

## Rotate an issuer or root

An issuer rotation requires a new Inari intermediate certificate and a staged
deployment to the Local Machine intermediate store. Keep the old issuer until
every supported package has moved.

A root rotation is a fleet trust migration. Deploy the new root alongside the
old one, confirm both trust paths, publish with the new hierarchy, and retire the
old root only after every supported package and recovery image has moved.

If a publisher key may be compromised, disable the release environment and
preserve its audit trail before rotating the leaf. Suspected issuer compromise
invalidates every leaf beneath it. Suspected root compromise requires a full
trust migration.
