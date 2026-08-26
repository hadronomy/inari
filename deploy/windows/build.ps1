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

function Assert-FrozenRuntime(
    [string]$Executable,
    [string]$Description,
    [string]$Report
) {
    Remove-Item $Report -Force -ErrorAction SilentlyContinue
    Write-Host "$Description — validating frozen imports and TLS runtime"
    try {
        Invoke-BoundedProcess `
            $Executable `
            @("--verify-runtime", $Report) `
            30 `
            "$Description runtime verification"
    }
    catch {
        if (Test-Path -LiteralPath $Report -PathType Leaf) {
            Write-Host "$Description runtime verification report:"
            Get-Content -LiteralPath $Report | ForEach-Object { Write-Host $_ }
        }
        throw
    }
    if (-not (Test-Path -LiteralPath $Report -PathType Leaf)) {
        throw "$Description did not produce a runtime verification report."
    }
    Get-Content -LiteralPath $Report | ForEach-Object { Write-Host "  $_" }
}

function Get-DanglingSignatureBytes([string]$Path) {
    # A PE records its Authenticode signature as a file offset and length in
    # the fifth data directory entry. Strip the signature without clearing that
    # entry and the file still claims bytes that are no longer there. Returns
    # how many bytes the file is short, or 0 when it is consistent.
    $Stream = [IO.File]::OpenRead($Path)
    try {
        if ($Stream.Length -lt 0x40) {
            return 0
        }
        $Reader = [IO.BinaryReader]::new($Stream)
        if ($Reader.ReadUInt16() -ne 0x5A4D) {
            return 0
        }
        $Stream.Position = 0x3C
        $HeaderOffset = $Reader.ReadUInt32()
        if ($HeaderOffset -le 0 -or ($HeaderOffset + 0x78) -ge $Stream.Length) {
            return 0
        }
        $Stream.Position = $HeaderOffset
        if ($Reader.ReadUInt32() -ne 0x00004550) {
            return 0
        }
        $OptionalHeader = $HeaderOffset + 0x18
        $Stream.Position = $OptionalHeader
        # PE32+ carries eight more bytes of optional header before the
        # directories than PE32 does.
        $DirectoryOffset = if ($Reader.ReadUInt16() -eq 0x20B) { 112 } else { 96 }
        $Stream.Position = $OptionalHeader + $DirectoryOffset + (4 * 8)
        $Offset = $Reader.ReadUInt32()
        $Size = $Reader.ReadUInt32()
        if ($Size -eq 0) {
            return 0
        }
        $End = [long]$Offset + [long]$Size
        if ($End -le $Stream.Length) {
            return 0
        }
        return $End - $Stream.Length
    }
    finally {
        $Stream.Dispose()
    }
}

function Assert-SignablePayload(
    [string]$Root,
    [string]$Description
) {
    Write-Host "$Description — checking every packaged binary can be signed"
    $Damaged = @(
        Get-ChildItem -LiteralPath $Root -Recurse -File -Include *.exe, *.dll, *.pyd |
            ForEach-Object {
                $Missing = Get-DanglingSignatureBytes $_.FullName
                if ($Missing -gt 0) {
                    "  $($_.FullName) is missing $Missing signature bytes"
                }
            }
    )
    if ($Damaged.Count -gt 0) {
        throw (
            @(
                "$Description contains binaries whose Authenticode certificate table runs past the end of the file:"
                $Damaged
                "Windows rejects the whole MSIX with ERROR_BAD_EXE_FORMAT (0x800700C1) rather than naming the file."
                "These come from a CPython distribution published with its signatures stripped but the directory entry left in place."
                "Set UV_PYTHON to a python.org interpreter of the pinned version and build again."
            ) -join [Environment]::NewLine
        )
    }
    Write-Host "$Description — every packaged binary carries a consistent signature table."
}

function Invoke-AuthenticodeSign(
    [string]$Path,
    [string]$Description
) {
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
    [string]$TrustBundle,
    [string]$ExpectedSignerHash
) {
    $Arguments = @(
        "verify",
        "-CAfile", $TrustBundle,
        "-ignore-timestamp",
        "-ignore-cdp",
        "-ignore-crl",
        "-require-leaf-hash", "sha256:$ExpectedSignerHash",
        "-in", $Path
    )
    Invoke-BoundedProcess $OsslSignCode $Arguments 60 "$Description verification"
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

function Write-PackageResourceIndex(
    [string]$MakePri,
    [string]$PackageRoot,
    [string]$WorkingDirectory
) {
    $Configuration = Join-Path $WorkingDirectory "priconfig.xml"
    $ResourceIndex = Join-Path $PackageRoot "resources.pri"
    Write-Host "Indexing theme-aware Windows application assets."
    try {
        Invoke-BoundedProcess `
            $MakePri `
            @("createconfig", "/cf", $Configuration, "/dq", "lang-en-US", "/o") `
            60 `
            "MSIX resource configuration"
        Invoke-BoundedProcess `
            $MakePri `
            @(
                "new",
                "/pr", $PackageRoot,
                "/cf", $Configuration,
                "/mn", (Join-Path $PackageRoot "AppxManifest.xml"),
                "/of", $ResourceIndex,
                "/o"
            ) `
            60 `
            "MSIX resource indexing"
    }
    finally {
        Remove-Item $Configuration -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath $ResourceIndex -PathType Leaf)) {
        throw "MSIX resource indexing did not produce resources.pri."
    }
}

$MakeAppx = Require-WindowsSdkCommand "makeappx.exe"
$MakePri = Require-WindowsSdkCommand "makepri.exe"
$SignTool = Require-WindowsSdkCommand "signtool.exe"
# gpui compiles its HLSL shaders during the build and finds fxc.exe only on PATH
# or under one hardcoded SDK version. Resolve it here so the build depends on
# the SDK being installed rather than on which version it happens to be.
$Fxc = Require-WindowsSdkCommand "fxc.exe"
$OsslSignCode = Require-Command "osslsigncode"
$Syft = Require-Command "syft"
$Uv = Require-Command "uv"
$Cargo = Require-Command "cargo"
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
$PublisherCertificateHash = $PublisherCertificate.GetCertHashString(
    [Security.Cryptography.HashAlgorithmName]::SHA256
).ToLowerInvariant()
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

$VerificationTrustName = "inari-code-signing-trust-$([Guid]::NewGuid().ToString('N')).pem"
$VerificationTrust = Join-Path ([IO.Path]::GetTempPath()) $VerificationTrustName
$VerificationTrustPem = @(
    $RootCertificateObject.ExportCertificatePem()
    $IssuerCertificate.ExportCertificatePem()
) -join [Environment]::NewLine
[IO.File]::WriteAllText(
    $VerificationTrust,
    $VerificationTrustPem,
    [Text.UTF8Encoding]::new($false)
)
$LocationPushed = $false
try {
    Push-Location $WorkspaceRoot
    $LocationPushed = $true
    # uv prefers its own managed CPython, which is published with Authenticode
    # signatures stripped. Those binaries end up in the frozen payload and make
    # the MSIX unsignable, so a release build asks for a system interpreter and
    # leaves managed downloads as the fallback the payload check will catch.
    $env:UV_PYTHON_PREFERENCE = "system"

    Write-Host "Synchronizing frozen application dependencies."
    & $Uv sync --all-packages --frozen --group windows-build
    Assert-NativeCommandSucceeded $LASTEXITCODE "Python dependency synchronization"

    Write-Host "Rendering the Windows executable icon."
    & $Uv run --no-sync python deploy/windows/build.py icon --output $ExecutableIcon
    Assert-NativeCommandSucceeded $LASTEXITCODE "Windows icon generation"

    Write-Host "Building the frozen agent service."
    & $Uv run --no-sync pyinstaller `
        --noconfirm `
        --clean `
        --workpath (Join-Path $PyInstallerTarget "work") `
        --distpath (Join-Path $PyInstallerTarget "dist") `
        $BundleSpec
    Assert-NativeCommandSucceeded $LASTEXITCODE "PyInstaller bundle creation"

    $Payload = Join-Path $PyInstallerTarget "dist\InariAgentService"
    Assert-FrozenRuntime `
        (Join-Path $Payload "InariAgentService.exe") `
        "Agent service" `
        (Join-Path $PyInstallerTarget "agent-service-runtime.txt")
    # Checked before the Rust build so an unsignable interpreter costs seconds
    # instead of surfacing as an unattributed packaging failure half an hour on.
    Assert-SignablePayload $Payload "Agent service"

    Write-Host "Building the native GPUI Device Center."
    $env:GPUI_FXC_PATH = $Fxc
    & $Cargo build --locked --release --package inari-device-center
    Assert-NativeCommandSucceeded $LASTEXITCODE "Device Center build"
    $DeviceCenterExecutable = Join-Path $WorkspaceRoot "target\release\InariDeviceCenter.exe"
    if (-not (Test-Path -LiteralPath $DeviceCenterExecutable -PathType Leaf)) {
        throw "The Device Center build did not produce '$DeviceCenterExecutable'."
    }
    Copy-Item `
        -LiteralPath $DeviceCenterExecutable `
        -Destination (Join-Path $Payload "InariDeviceCenter.exe") `
        -Force

    Write-Host "Combining the native client and frozen service in the MSIX package tree."
    $MetadataJson = & $Uv run --no-sync python deploy/windows/build.py package --payload $Payload --output $PackageRoot
    Assert-NativeCommandSucceeded $LASTEXITCODE "MSIX package preparation"
    $Metadata = $MetadataJson | ConvertFrom-Json
    $ReleaseDirectory = Join-Path $WindowsTarget $Metadata.version
    New-Item -ItemType Directory -Path $ReleaseDirectory -Force | Out-Null
    Write-Host "MSIX package tree ready for version $($Metadata.version)."
    Write-PackageResourceIndex $MakePri $PackageRoot $WindowsTarget

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
        Assert-AuthenticodeSignature `
            $File.FullName `
            $Description `
            $VerificationTrust `
            $PublisherCertificateHash
    }

    $ArtifactBase = "Inari-Device-Center_$($Metadata.version)_x64"
    $DeviceCenterArtifact = Join-Path $ReleaseDirectory "$ArtifactBase.exe"
    Copy-Item `
        -LiteralPath (Join-Path $PackageRoot "InariDeviceCenter.exe") `
        -Destination $DeviceCenterArtifact `
        -Force
    $MsixPath = Join-Path $ReleaseDirectory "$ArtifactBase.msix"
    Write-Host "Packing the signed payload into $ArtifactBase.msix."
    $MakeAppxArguments = @("pack", "/d", $PackageRoot, "/p", $MsixPath, "/o")
    Invoke-BoundedProcess $MakeAppx $MakeAppxArguments 180 "MSIX packaging"
    Invoke-AuthenticodeSign $MsixPath "MSIX package"
    Assert-AuthenticodeSignature `
        $MsixPath `
        "MSIX package" `
        $VerificationTrust `
        $PublisherCertificateHash

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
        $DeviceCenterArtifact,
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
    $ChecksumManifest = Join-Path $ReleaseDirectory "SHA256SUMS"
    $ChecksumContent = ($ChecksumLines -join "`n") + "`n"
    [IO.File]::WriteAllText($ChecksumManifest, $ChecksumContent, [Text.Encoding]::ASCII)
    Write-Host "Windows release bundle ready at $ReleaseDirectory."
}
finally {
    Remove-Item $VerificationTrust -Force -ErrorAction SilentlyContinue
    foreach ($Certificate in $SigningCertificates) {
        $Certificate.Dispose()
    }
    $RootCertificateObject.Dispose()
    if ($LocationPushed) {
        Pop-Location
    }
}
