# Windows code-signing operations

The Windows package uses a dedicated code-signing hierarchy. It is independent
from the step-ca instance used for agent identity and from the certificates used
inside Kubernetes. Reusing either trust domain would turn an application release
credential into device or workload authority.

## Hierarchy

The hierarchy contains:

- an offline RSA root constrained to the code-signing extended key usage;
- a rotating publisher leaf with subject `CN=Inari Device Operations`;
- an encrypted PKCS #12 bundle containing the publisher key, leaf, and chain.

The MSIX identity and publisher subject are stable. Rotate the leaf before it
expires without changing either value. Changing the root requires a staged
enterprise trust rollout before any package signed by the new hierarchy can be
installed.

## Initial provisioning

Provision on the isolated signing workstation, never on a CI runner or ordinary
development machine. Install the pinned mise toolchain and choose a new output
directory outside the repository and synchronized storage:

```sh
mise install step
mise exec -- scripts/provision-windows-signing.sh /secure/removable/inari-signing
```

The command prompts separately while it creates encrypted private keys and the
publisher PFX. When it completes:

1. verify the root and publisher details with `step certificate inspect`;
2. move `root.key` into offline, access-controlled storage;
3. retain an offline backup and a documented two-person recovery procedure;
4. export only `publisher.pfx` and `root.crt` to the protected release setup;
5. delete transient copies after the GitHub environment has been provisioned.

Do not commit certificates, keys, PFX files, passwords, base64 encodings, or
fingerprints prepared for an unreleased hierarchy.

## GitHub environment

Create a protected environment named `windows-release`, restrict it to the
`main` branch, and require maintainer approval. Disable self-review once a
second eligible maintainer can review deployments; a single-maintainer project
must otherwise retain a documented manual approval step. Store:

- base64 of the publisher PFX as `WINDOWS_SIGNING_PFX_BASE64`;
- the PFX export password as `WINDOWS_SIGNING_PFX_PASSWORD`;
- base64 of the DER or PEM root certificate as
  `WINDOWS_CODE_SIGNING_ROOT_CERT_BASE64`.

The release job materializes these values only in the runner's temporary
directory. The build verifies certificate subject, validity, code-signing EKU,
root constraints, and the chain before signing. GitHub-hosted runners trust the
root only for the duration of signature verification and remove it in the
cleanup path. The produced package never installs trust on an operator's
machine.

## Leaf rotation

Issue a new publisher leaf from the offline root before the existing leaf
expires. Preserve the subject, generate a new private key, test-sign a package
in an isolated environment, and verify an in-place upgrade from the latest
production package. Update the protected PFX secret only after that exercise
passes.

Timestamping preserves validation of artifacts signed while a leaf was valid,
but it does not remove the need to rotate ahead of expiry. Retain retired public
leaf certificates and release evidence; destroy superseded private key material
according to the organization's key-retention policy.

## Root rotation and incident response

A root rotation is a trust migration, not a routine release. Distribute the new
root alongside the old one, confirm fleet trust, publish a package signed by the
new hierarchy, and remove the old root only after every supported package has
moved.

If publisher key compromise is suspected, stop the release environment, revoke
maintainer access, preserve audit evidence, rotate the publisher leaf, and block
the affected certificate through the organization's Windows controls. If the
offline root may be compromised, begin the full root-rotation process and treat
every certificate beneath it as untrusted.
