# Install Inari Device Center on Windows

Inari Device Center is distributed as an x64 MSIX for Windows 11. The package
installs two applications with different lifecycles:

- **Inari Device Center** runs in the signed-in user’s session. It provides the
  tray icon, setup assistant, and device views.
- **Inari Agent** runs as the `InariAgent` Windows service under
  `LocalService`. It owns hardware access, queued work, and network
  communication.

Closing Device Center does not stop the agent. Windows or your device-management
platform owns upgrades and removal.

## Download and verify a release

Download the MSIX, `SHA256SUMS`, both `.cer` files, and both fingerprint files
from the same [GitHub release](https://github.com/hadronomy/inari/releases).
Obtain the expected root fingerprint through a channel your organization already
trusts. A fingerprint downloaded beside the package detects corruption; it does
not establish trust on its own.

From PowerShell, compare the root certificate with that approved fingerprint:

```powershell
$Root = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
    (Resolve-Path .\hadronomy-code-signing-root.cer)
)
$Root.GetCertHashString(
    [Security.Cryptography.HashAlgorithmName]::SHA256
).ToLowerInvariant()
```

Then compare the MSIX hash with the matching line in `SHA256SUMS`:

```powershell
$Package = Get-ChildItem .\Inari-Device-Center_*_x64.msix -File |
    Select-Object -First 1
Get-FileHash $Package.FullName -Algorithm SHA256
Get-Content .\SHA256SUMS
```

Stop if either value differs.

## Trust the publisher

Inari’s alpha releases use a private code-signing hierarchy. Windows needs the
root and the Inari issuing CA in the **local machine** stores before App
Installer can identify the publisher. Installing only the root is not enough:
the MSIX signature carries the publisher certificate, but Windows still needs
the issuing CA to complete the chain.

Open PowerShell as Administrator and run:

```powershell
Import-Certificate `
    -FilePath .\hadronomy-code-signing-root.cer `
    -CertStoreLocation Cert:\LocalMachine\Root

Import-Certificate `
    -FilePath .\inari-code-signing-issuer.cer `
    -CertStoreLocation Cert:\LocalMachine\CA
```

These locations matter. App Installer does not use the current user’s
certificate stores when it verifies package identity.

Confirm that Windows now trusts the package:

```powershell
$Package = Get-ChildItem .\Inari-Device-Center_*_x64.msix -File |
    Select-Object -First 1
$Signature = Get-AuthenticodeSignature $Package.FullName
$Signature | Format-List Status, StatusMessage
$Signature.SignerCertificate | Format-List Subject, Issuer, NotAfter, Thumbprint
```

`Status` must be `Valid`, and the subject must be
`CN=Pablo Hernández Jiménez`. Do not install the package if Windows reports a
different signer or an invalid signature.

## Install Device Center

Double-click the MSIX after the signature is valid, or install it from the same
elevated PowerShell session:

```powershell
Add-AppxPackage $Package.FullName
```

The first launch registers Device Center for sign-in startup. The agent service
starts independently and remains available when the tray is closed.

The App Installer window may continue to show GitHub as the download source and
display the standard warning for software downloaded from the internet. That is
separate from publisher trust. **Publisher: Unknown** is not expected after the
two certificates are installed in the correct machine stores.

## Enterprise deployment

Deploy certificate trust before assigning the MSIX:

1. Place `hadronomy-code-signing-root.cer` in **Local Computer → Trusted Root
   Certification Authorities**.
2. Place `inari-code-signing-issuer.cer` in **Local Computer → Intermediate
   Certification Authorities**.
3. Verify the approved fingerprints through your normal PKI change process.
4. Assign the MSIX to the intended devices or users.

Intune can deliver the certificates with trusted-certificate profiles and the
MSIX as a line-of-business app. Group Policy can distribute the same chain
through Public Key Policies. Keep trust deployment and application deployment
as separate policies so each change is visible and reversible.

Inari never adds its own trust anchors during installation.

## Enrollment links and local pairing

The package registers the `inari://` protocol. Opening an invitation starts
Device Center, or forwards the link to the instance that is already running,
and brings the setup assistant to the foreground.

On an installed system, Device Center requests its first one-use pairing secret
from the agent over a Windows named pipe. The service checks that the caller
belongs to the expected package family before it returns the secret. The tray
then completes the normal signed client-attestation flow. Credentials for the
tray live in Windows Credential Manager; service credentials use machine-scope
DPAPI and a restricted file under ProgramData.

## Troubleshooting

### App Installer still says “Publisher: Unknown”

Check the two machine stores:

```powershell
Get-ChildItem Cert:\LocalMachine\Root |
    Where-Object Subject -eq 'CN=Pablo Hernández Jiménez Code Signing Root CA'

Get-ChildItem Cert:\LocalMachine\CA |
    Where-Object Subject -eq 'CN=Inari Code Signing Issuing CA'
```

If either command returns nothing, import that certificate again from an
elevated shell. A copy under `Cert:\CurrentUser\...` does not satisfy App
Installer. Run `Get-AuthenticodeSignature` again before reopening the MSIX.

### The package still will not install

Record the exact PowerShell error from `Add-AppxPackage`, then inspect:

```text
Event Viewer
  Applications and Services Logs
    Microsoft
      Windows
        AppxDeployment-Server
          Operational
```

Errors such as `0x800B010A` and `0x800B0109` point to an incomplete or untrusted
certificate chain. Manifest and package errors have different codes and should
not be worked around by weakening certificate policy.

### The service does not start

Check Service Control Manager events, then the Inari logs under ProgramData.
Validate the agent configuration from an elevated shell. Do not switch an
installed tray to development spawn mode to hide a service failure; that creates
a second process owner instead of repairing the installation.

## Upgrade and removal

Install a newer MSIX with the same package identity to upgrade in place. Review
the release notes before fleet rollout when a release changes configuration or
protocol behavior.

Remove Device Center for the current user with:

```powershell
Get-AppxPackage Inari.DeviceCenter | Remove-AppxPackage
```

Uninstalling the app does not remove machine trust. Retire the Inari issuing CA
and publisher root through your PKI process only after no supported Inari
package depends on them.

Microsoft’s [App Installer troubleshooting guide](https://learn.microsoft.com/windows/msix/app-installer/troubleshoot-appinstaller-issues)
and [MSIX signing overview](https://learn.microsoft.com/windows/msix/package/signing-package-overview)
describe the Windows trust behavior used here.
