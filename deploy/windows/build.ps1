[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$WindowsTarget = Join-Path $WorkspaceRoot "target\release\windows"
$PyInstallerTarget = Join-Path $WorkspaceRoot "target\pyinstaller"
$PackageRoot = Join-Path $WindowsTarget "package"
$ExecutableIcon = Join-Path $WindowsTarget "assets\InariDeviceCenter.ico"
$BundleSpec = Join-Path $PSScriptRoot "inari.spec"

function Require-Command([string]$Name) {
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $Command) {
        throw "Required command '$Name' is not available. Install the Windows 11 SDK and release tools first."
    }
    return $Command.Source
}

function Require-WindowsSdkCommand([string]$Name) {
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $Command) {
        return $Command.Source
    }

    $InstalledRoots = "HKLM:\SOFTWARE\Microsoft\Windows Kits\Installed Roots"
    $KitsRoot = Get-ItemPropertyValue `
        -Path $InstalledRoots `
        -Name "KitsRoot10" `
        -ErrorAction SilentlyContinue
    if ([string]::IsNullOrWhiteSpace($KitsRoot)) {
        throw "Required Windows SDK command '$Name' is unavailable and KitsRoot10 is not registered."
    }

    $VersionedTools = Get-ChildItem -Path (Join-Path $KitsRoot "bin") -Directory |
        ForEach-Object {
            $Version = $null
            if ([Version]::TryParse($_.Name, [ref]$Version)) {
                [PSCustomObject]@{
                    Path = Join-Path $_.FullName "x64\$Name"
                    Version = $Version
                }
            }
        } |
        Where-Object { Test-Path -LiteralPath $_.Path -PathType Leaf } |
        Sort-Object -Property Version -Descending
    $Tool = $VersionedTools | Select-Object -First 1
    if ($null -eq $Tool) {
        throw "Required Windows SDK command '$Name' was not found beneath '$KitsRoot'."
    }
    return $Tool.Path
}

function Require-Environment([string]$Name) {
    $Value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Required environment variable '$Name' is not set."
    }
    return $Value
}

function Assert-NativeCommandSucceeded([int]$ExitCode, [string]$Operation) {
    if ($ExitCode -ne 0) {
        throw "$Operation failed with exit code $ExitCode."
    }
}

function Invoke-BoundedProcess(
    [string]$FilePath,
    [string[]]$Arguments,
    [int]$TimeoutSeconds,
    [string]$Operation
) {
    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $FilePath
    $StartInfo.UseShellExecute = $false
    foreach ($Argument in $Arguments) {
        $StartInfo.ArgumentList.Add($Argument)
    }

    $Process = [Diagnostics.Process]::Start($StartInfo)
    try {
        if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
            $Process.Kill($true)
            $Process.WaitForExit()
            throw "$Operation timed out after $TimeoutSeconds seconds."
        }
        if ($Process.ExitCode -ne 0) {
            throw "$Operation failed with exit code $($Process.ExitCode)."
        }
    }
    finally {
        $Process.Dispose()
    }
}

function Invoke-AuthenticodeSign([string]$Path, [string]$Description) {
    $Arguments = @(
        "sign",
        "/fd", "SHA256",
        "/f", $SigningPfx,
        "/p", $SigningPassword,
        $Path
    )
    Write-Host "$Description — applying Authenticode signature"
    Invoke-BoundedProcess $SignTool $Arguments 60 "$Description signing"
}

function Assert-AuthenticodeSignature(
    [string]$Path,
    [string]$Description,
    [Security.Cryptography.X509Certificates.X509Certificate2]$ExpectedSigner
) {
    $Signature = Get-AuthenticodeSignature -LiteralPath $Path
    $AcceptedStatuses = @(
        [System.Management.Automation.SignatureStatus]::Valid,
        [System.Management.Automation.SignatureStatus]::NotTrusted
    )
    if ($Signature.Status -notin $AcceptedStatuses) {
        throw "$Description signature validation failed: $($Signature.StatusMessage)"
    }
    if ($null -eq $Signature.SignerCertificate) {
        throw "$Description does not expose an Authenticode signer certificate."
    }
    if (-not [string]::Equals(
        $Signature.SignerCertificate.Thumbprint,
        $ExpectedSigner.Thumbprint,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Description was not signed by the expected publisher certificate."
    }
    Write-Host "$Description — signature integrity and signer identity verified"
}

function Get-BasicConstraints(
    [Security.Cryptography.X509Certificates.X509Certificate2]$Certificate
) {
    return $Certificate.Extensions |
        Where-Object { $_ -is [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension] } |
        Select-Object -First 1
}

function Get-EnhancedKeyUsage(
    [Security.Cryptography.X509Certificates.X509Certificate2]$Certificate
) {
    return $Certificate.Extensions |
        Where-Object { $_ -is [Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension] } |
        Select-Object -First 1
}

$MakeAppx = Require-WindowsSdkCommand "makeappx.exe"
$SignTool = Require-WindowsSdkCommand "signtool.exe"
$Syft = Require-Command "syft"
$Uv = Require-Command "uv"
$SigningPfx = Require-Environment "INARI_SIGNING_PFX"
$SigningPassword = Require-Environment "INARI_SIGNING_PASSWORD"
$RootCertificate = Require-Environment "INARI_CODE_SIGNING_ROOT_CERT"
$CodeSigningOid = "1.3.6.1.5.5.7.3.3"
$SigningCertificates = [Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
$SigningCertificates.Import(
    $SigningPfx,
    $SigningPassword,
    [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
)
$RootCertificateObject = [Security.Cryptography.X509Certificates.X509Certificate2]::new($RootCertificate)

$PublisherCertificates = @($SigningCertificates | Where-Object { $_.HasPrivateKey })
if ($PublisherCertificates.Count -ne 1) {
    throw "The signing PFX must contain exactly one publisher certificate with a private key."
}
$PublisherCertificate = $PublisherCertificates[0]
$IssuerCertificates = @($SigningCertificates | Where-Object {
    $Constraints = Get-BasicConstraints $_
    $null -ne $Constraints -and $Constraints.CertificateAuthority
})
if ($IssuerCertificates.Count -ne 1) {
    throw "The signing PFX must contain exactly one issuing CA certificate."
}
$IssuerCertificate = $IssuerCertificates[0]

$Now = Get-Date
if ($Now -lt $PublisherCertificate.NotBefore -or $Now -gt $PublisherCertificate.NotAfter) {
    throw "The publisher certificate is not currently valid."
}
$PublisherEku = Get-EnhancedKeyUsage $PublisherCertificate
if ($null -eq $PublisherEku -or $CodeSigningOid -notin $PublisherEku.EnhancedKeyUsages.Value) {
    throw "The publisher certificate is not valid for code signing."
}
$IssuerConstraints = Get-BasicConstraints $IssuerCertificate
if (
    $null -eq $IssuerConstraints -or
    -not $IssuerConstraints.CertificateAuthority -or
    -not $IssuerConstraints.HasPathLengthConstraint -or
    $IssuerConstraints.PathLengthConstraint -ne 0
) {
    throw "The signing PFX issuer must be a path-length-zero certificate authority."
}
$IssuerEku = Get-EnhancedKeyUsage $IssuerCertificate
if (
    $null -eq $IssuerEku -or
    $IssuerEku.EnhancedKeyUsages.Count -ne 1 -or
    $IssuerEku.EnhancedKeyUsages[0].Value -ne $CodeSigningOid
) {
    throw "The issuing CA must be constrained to the code-signing extended key usage."
}
$RootConstraints = Get-BasicConstraints $RootCertificateObject
if (
    $null -eq $RootConstraints -or
    -not $RootConstraints.CertificateAuthority -or
    -not $RootConstraints.HasPathLengthConstraint -or
    $RootConstraints.PathLengthConstraint -ne 1
) {
    throw "The supplied code-signing root must be a path-length-one certificate authority."
}
$RootEku = Get-EnhancedKeyUsage $RootCertificateObject
if (
    $null -eq $RootEku -or
    $RootEku.EnhancedKeyUsages.Count -ne 1 -or
    $RootEku.EnhancedKeyUsages[0].Value -ne $CodeSigningOid
) {
    throw "The code-signing root must be constrained to the code-signing extended key usage."
}
$Chain = [Security.Cryptography.X509Certificates.X509Chain]::new()
$Chain.ChainPolicy.TrustMode = [Security.Cryptography.X509Certificates.X509ChainTrustMode]::CustomRootTrust
$Chain.ChainPolicy.CustomTrustStore.Add($RootCertificateObject) | Out-Null
$Chain.ChainPolicy.ExtraStore.Add($IssuerCertificate) | Out-Null
$Chain.ChainPolicy.RevocationMode = [Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
$Chain.ChainPolicy.DisableCertificateDownloads = $true
$Chain.ChainPolicy.UrlRetrievalTimeout = [TimeSpan]::FromMilliseconds(100)
$Chain.ChainPolicy.VerificationFlags = [Security.Cryptography.X509Certificates.X509VerificationFlags]::NoFlag
$Chain.ChainPolicy.ApplicationPolicy.Add([Security.Cryptography.Oid]::new($CodeSigningOid)) | Out-Null
Write-Host "Validating the publisher certificate against the bundled issuer and root."
try {
    if (-not $Chain.Build($PublisherCertificate)) {
        $Problems = ($Chain.ChainStatus | ForEach-Object { $_.StatusInformation.Trim() }) -join "; "
        throw "The publisher certificate does not chain to the supplied code-signing root: $Problems"
    }
    Write-Host "Publisher certificate chain validated without network retrieval."
}
finally {
    $Chain.Dispose()
}

Push-Location $WorkspaceRoot
try {
    Write-Host "Synchronizing frozen application dependencies."
    & $Uv sync --all-packages --frozen --group windows-build
    Assert-NativeCommandSucceeded $LASTEXITCODE "Python dependency synchronization"

    Write-Host "Rendering the Windows executable icon."
    & $Uv run --no-sync python deploy/windows/build.py icon --output $ExecutableIcon
    Assert-NativeCommandSucceeded $LASTEXITCODE "Windows icon generation"

    Write-Host "Building the Device Center and agent service executables."
    & $Uv run --no-sync pyinstaller `
        --noconfirm `
        --clean `
        --workpath (Join-Path $PyInstallerTarget "work") `
        --distpath (Join-Path $PyInstallerTarget "dist") `
        $BundleSpec
    Assert-NativeCommandSucceeded $LASTEXITCODE "PyInstaller bundle creation"

    $Payload = Join-Path $PyInstallerTarget "dist\InariDeviceCenter"
    Write-Host "Moving the frozen bundle into the MSIX package tree."
    $MetadataJson = & $Uv run --no-sync python deploy/windows/build.py package --payload $Payload --output $PackageRoot
    Assert-NativeCommandSucceeded $LASTEXITCODE "MSIX package preparation"
    $Metadata = $MetadataJson | ConvertFrom-Json
    $ReleaseDirectory = Join-Path $WindowsTarget $Metadata.version
    New-Item -ItemType Directory -Path $ReleaseDirectory -Force | Out-Null
    Write-Host "MSIX package tree ready for version $($Metadata.version)."

    Write-Host "Validating the MSIX publisher identity."
    $ActualPublisherName = $PublisherCertificate.Subject.Normalize(
        [Text.NormalizationForm]::FormC
    )
    $ExpectedPublisherName = ([string]$Metadata.publisher).Normalize(
        [Text.NormalizationForm]::FormC
    )
    if (-not [string]::Equals(
        $ActualPublisherName,
        $ExpectedPublisherName,
        [StringComparison]::Ordinal
    )) {
        throw (
            "Publisher certificate subject '$($PublisherCertificate.Subject)' " +
            "does not match package publisher '$($Metadata.publisher)'."
        )
    }

    # The signed MSIX block map protects every packaged file. Authenticode-sign
    # only Inari's entry points instead of replacing third-party signatures.
    $OwnedExecutables = @(
        Get-Item (Join-Path $PackageRoot "InariDeviceCenter.exe")
        Get-Item (Join-Path $PackageRoot "InariAgentService.exe")
    )
    Write-Host "Authenticode signing $($OwnedExecutables.Count) Inari executables."
    for ($Index = 0; $Index -lt $OwnedExecutables.Count; $Index += 1) {
        $File = $OwnedExecutables[$Index]
        $Description = "Inari executable $($Index + 1)/$($OwnedExecutables.Count): $($File.Name)"
        Invoke-AuthenticodeSign $File.FullName $Description
        Assert-AuthenticodeSignature $File.FullName $Description $PublisherCertificate
    }

    $ArtifactBase = "Inari-Device-Center_$($Metadata.version)_x64"
    $MsixPath = Join-Path $ReleaseDirectory "$ArtifactBase.msix"
    Write-Host "Packing the signed payload into $ArtifactBase.msix."
    $MakeAppxArguments = @("pack", "/d", $PackageRoot, "/p", $MsixPath, "/o")
    Invoke-BoundedProcess $MakeAppx $MakeAppxArguments 180 "MSIX packaging"
    Invoke-AuthenticodeSign $MsixPath "MSIX package"
    Assert-AuthenticodeSignature $MsixPath "MSIX package" $PublisherCertificate

    $SbomPath = Join-Path $ReleaseDirectory "$ArtifactBase.spdx.json"
    Write-Host "Generating the SPDX software bill of materials."
    $SyftArguments = @("dir:$PackageRoot", "-o", "spdx-json=$SbomPath")
    Invoke-BoundedProcess $Syft $SyftArguments 180 "SBOM generation"

    Write-Host "Publishing the signing trust chain and checksums."
    $PublishedRoot = Join-Path $ReleaseDirectory "hadronomy-code-signing-root.cer"
    [IO.File]::WriteAllBytes($PublishedRoot, $RootCertificateObject.RawData)
    $PublishedIssuer = Join-Path $ReleaseDirectory "inari-code-signing-issuer.cer"
    [IO.File]::WriteAllBytes($PublishedIssuer, $IssuerCertificate.RawData)
    $RootFingerprint = $RootCertificateObject.GetCertHashString(
        [Security.Cryptography.HashAlgorithmName]::SHA256
    ).ToLowerInvariant()
    Set-Content `
        -Path (Join-Path $ReleaseDirectory "hadronomy-code-signing-root-fingerprint.txt") `
        -Value "SHA256 $RootFingerprint" `
        -Encoding utf8NoBOM
    $IssuerFingerprint = $IssuerCertificate.GetCertHashString(
        [Security.Cryptography.HashAlgorithmName]::SHA256
    ).ToLowerInvariant()
    Set-Content `
        -Path (Join-Path $ReleaseDirectory "inari-code-signing-issuer-fingerprint.txt") `
        -Value "SHA256 $IssuerFingerprint" `
        -Encoding utf8NoBOM

    $Assets = @(
        $MsixPath,
        $SbomPath,
        $PublishedRoot,
        (Join-Path $ReleaseDirectory "hadronomy-code-signing-root-fingerprint.txt"),
        $PublishedIssuer,
        (Join-Path $ReleaseDirectory "inari-code-signing-issuer-fingerprint.txt")
    )
    $ChecksumLines = $Assets | ForEach-Object {
        $Hash = (Get-FileHash $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        "$Hash  $([IO.Path]::GetFileName($_))"
    }
    Set-Content -Path (Join-Path $ReleaseDirectory "SHA256SUMS") -Value $ChecksumLines -Encoding ascii
    Write-Host "Windows release bundle ready at $ReleaseDirectory."
}
finally {
    foreach ($Certificate in $SigningCertificates) {
        $Certificate.Dispose()
    }
    $RootCertificateObject.Dispose()
    Pop-Location
}
