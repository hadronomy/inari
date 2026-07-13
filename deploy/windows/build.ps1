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
$TimestampUrl = if ($env:INARI_TIMESTAMP_URL) { $env:INARI_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
$CodeSigningOid = "1.3.6.1.5.5.7.3.3"
$SigningCertificates = [Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
$SigningCertificates.Import(
    $SigningPfx,
    $SigningPassword,
    [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
)
$RootCertificateObject = [Security.Cryptography.X509Certificates.X509Certificate2]::new($RootCertificate)
$TemporaryRootStore = $null
$TemporaryRootAdded = $false

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
$Chain.ChainPolicy.ApplicationPolicy.Add([Security.Cryptography.Oid]::new($CodeSigningOid)) | Out-Null
if (-not $Chain.Build($PublisherCertificate)) {
    $Problems = ($Chain.ChainStatus | ForEach-Object { $_.StatusInformation.Trim() }) -join "; "
    throw "The publisher certificate does not chain to the supplied code-signing root: $Problems"
}
$Chain.Dispose()

Push-Location $WorkspaceRoot
try {
    & $Uv sync --all-packages --frozen --group windows-build
    Assert-NativeCommandSucceeded $LASTEXITCODE "Python dependency synchronization"
    & $Uv run --no-sync python deploy/windows/build.py icon --output $ExecutableIcon
    Assert-NativeCommandSucceeded $LASTEXITCODE "Windows icon generation"
    & $Uv run --no-sync pyinstaller `
        --noconfirm `
        --clean `
        --workpath (Join-Path $PyInstallerTarget "work") `
        --distpath (Join-Path $PyInstallerTarget "dist") `
        $BundleSpec
    Assert-NativeCommandSucceeded $LASTEXITCODE "PyInstaller bundle creation"

    $Payload = Join-Path $PyInstallerTarget "dist\InariDeviceCenter"
    $MetadataJson = & $Uv run --no-sync python deploy/windows/build.py package --payload $Payload --output $PackageRoot
    Assert-NativeCommandSucceeded $LASTEXITCODE "MSIX package preparation"
    $Metadata = $MetadataJson | ConvertFrom-Json
    $ReleaseDirectory = Join-Path $WindowsTarget $Metadata.version
    New-Item -ItemType Directory -Path $ReleaseDirectory -Force | Out-Null

    $ExpectedPublisherName = [Security.Cryptography.X509Certificates.X500DistinguishedName]::new(
        [string]$Metadata.publisher
    )
    $ActualPublisherIdentity = [Convert]::ToHexString(
        $PublisherCertificate.SubjectName.RawData
    )
    $ExpectedPublisherIdentity = [Convert]::ToHexString(
        $ExpectedPublisherName.RawData
    )
    if ($ActualPublisherIdentity -cne $ExpectedPublisherIdentity) {
        throw (
            "Publisher certificate subject '$($PublisherCertificate.Subject)' " +
            "does not match package publisher '$($Metadata.publisher)'."
        )
    }

    $TemporaryRootStore = [Security.Cryptography.X509Certificates.X509Store]::new(
        [Security.Cryptography.X509Certificates.StoreName]::Root,
        [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
    )
    $TemporaryRootStore.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    $TrustedRoots = $TemporaryRootStore.Certificates.Find(
        [Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
        $RootCertificateObject.Thumbprint,
        $false
    )
    if ($TrustedRoots.Count -eq 0) {
        if ($env:GITHUB_ACTIONS -ne "true") {
            throw "The code-signing root is not trusted by this release account. Import it explicitly before a local build."
        }
        $TemporaryRootStore.Add($RootCertificateObject)
        $TemporaryRootAdded = $true
    }

    Get-ChildItem $PackageRoot -Recurse -File | Sort-Object FullName | Where-Object {
        $_.Extension -in ".exe", ".dll", ".pyd"
    } | ForEach-Object {
        & $SignTool sign /fd SHA256 /td SHA256 /tr $TimestampUrl /f $SigningPfx /p $SigningPassword $_.FullName
        if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed for $($_.Name)." }
        & $SignTool verify /pa /all /v $_.FullName
        if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed for $($_.Name)." }
    }

    $ArtifactBase = "Inari-Device-Center_$($Metadata.version)_x64"
    $MsixPath = Join-Path $ReleaseDirectory "$ArtifactBase.msix"
    & $MakeAppx pack /d $PackageRoot /p $MsixPath /o
    if ($LASTEXITCODE -ne 0) { throw "MakeAppx failed." }
    & $SignTool sign /fd SHA256 /td SHA256 /tr $TimestampUrl /f $SigningPfx /p $SigningPassword $MsixPath
    if ($LASTEXITCODE -ne 0) { throw "MSIX signing failed." }
    & $SignTool verify /pa /all /v $MsixPath
    if ($LASTEXITCODE -ne 0) { throw "MSIX signature verification failed." }

    $SbomPath = Join-Path $ReleaseDirectory "$ArtifactBase.spdx.json"
    & $Syft "dir:$PackageRoot" -o "spdx-json=$SbomPath"
    if ($LASTEXITCODE -ne 0) { throw "SBOM generation failed." }

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
}
finally {
    if ($null -ne $TemporaryRootStore) {
        if ($TemporaryRootAdded) {
            $TemporaryRootStore.Remove($RootCertificateObject)
        }
        $TemporaryRootStore.Close()
    }
    foreach ($Certificate in $SigningCertificates) {
        $Certificate.Dispose()
    }
    $RootCertificateObject.Dispose()
    Pop-Location
}
