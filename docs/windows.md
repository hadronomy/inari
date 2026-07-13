# Inari Device Center for Windows

Inari Device Center is distributed as a signed x64 MSIX for Windows 11. One
package installs two deliberately separate processes:

- **Inari Device Center** is the user-session tray application, setup assistant,
  and device interface. It starts at sign-in after its first launch.
- **Inari Agent** is a delayed-start Windows service running as `LocalService`.
  It owns hardware access, local durability, and network communication.

Closing the tray never stops the service. Upgrades and removals are controlled
by the operating system or the organization's device-management platform; the
tray does not run an independent updater.

## Before installation

The package uses Inari's private code-signing hierarchy. An administrator must
deploy the published root certificate before Windows will trust the MSIX.
Obtain the expected root fingerprint through an organization-controlled channel
that is separate from the package download. A fingerprint beside an untrusted
download is useful for detecting corruption, but it cannot establish trust by
itself.

Verify the certificate fingerprint in PowerShell:

```powershell
$Certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
    (Resolve-Path .\hadronomy-code-signing-root.cer)
)
$Certificate.GetCertHashString(
    [Security.Cryptography.HashAlgorithmName]::SHA256
).ToLowerInvariant()
```

Compare the result with the approved fingerprint, then verify the release
checksums:

```powershell
Get-FileHash .\Inari-Device-Center_1.20.0-alpha.1_x64.msix -Algorithm SHA256
Get-Content .\SHA256SUMS
```

## Manual installation

An administrator can add the approved root to the local-machine trust store:

```powershell
Import-Certificate `
    -FilePath .\hadronomy-code-signing-root.cer `
    -CertStoreLocation Cert:\LocalMachine\Root
```

Install the package for the current user:

```powershell
Add-AppxPackage .\Inari-Device-Center_1.20.0-alpha.1_x64.msix
```

App Installer presents the Inari identity, allows cancellation, and offers to
launch Device Center when installation completes. The first launch registers
the tray startup task. The agent service is installed from the same package and
starts independently on its delayed automatic schedule.

## Managed deployment

For Intune or Group Policy, deploy the root certificate to **Trusted Root
Certification Authorities** first, then assign the MSIX to the intended users
or devices. Keep certificate trust and application deployment as distinct
policies so each can be audited and rolled back independently.

- In Intune, use a trusted-certificate profile for the root and a line-of-business
  app assignment for the MSIX.
- In Group Policy, distribute the root through Public Key Policies and deploy
  the package with the organization's normal software-delivery system.
- Verify the package publisher remains `CN=Pablo Hernández Jiménez` before each
  rollout.

Inari does not import its root certificate during application installation.
Trust remains an explicit infrastructure decision.

## Invitation links and first pairing

The package registers `inari://` links. Opening an enrollment invitation starts
Device Center or forwards the link to the existing instance, then focuses the
setup assistant. Invitation material is not copied into command logs or stored
by the launcher.

On an installed system, Device Center obtains its first one-use local pairing
secret over a Windows named pipe. The service verifies that the connecting
process belongs to the same MSIX package family before issuing the secret, and
the existing cryptographic client attestation completes the pairing. The HTTP
endpoint that exposes development pairing material is disabled in this profile.

Tray credentials live in Windows Credential Manager. Service credentials are
protected with machine-scope DPAPI in a file readable only by `SYSTEM`,
administrators, and `LocalService`. The installed profile fails closed when
protected storage is unavailable; it never writes a plaintext fallback.

## Configuration and diagnostics

The service uses the production Windows paths documented in the
[agent guide](../packages/agent/README.md). Device Center keeps user-interface
logs in the platform user log directory. The tray's **Open logs** action opens
that directory without exposing service credentials.

For a service that does not start:

1. inspect Windows Service Control Manager events for `InariAgent`;
2. inspect the service bootstrap log under the Inari ProgramData log directory;
3. validate the agent configuration from an elevated shell;
4. confirm the package and publisher signatures with `Get-AuthenticodeSignature`.

Do not make the tray spawn an unmanaged agent process to work around a broken
service installation. Spawn mode is a development profile, not a production
recovery path.

## Upgrade and removal

Deploy a newer package with the same package identity and publisher. MSIX
performs the in-place upgrade and preserves package registration. Coordinate
agent protocol or configuration changes through the normal release notes before
rolling a fleet.

Remove the current-user package with:

```powershell
Get-AppxPackage Inari.DeviceCenter | Remove-AppxPackage
```

Remove the trusted root only after every Inari package signed by that hierarchy
has been retired. Certificate removal is a separate administrative operation;
uninstalling Device Center does not change machine trust.
