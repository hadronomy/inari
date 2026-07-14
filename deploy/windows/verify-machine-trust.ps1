[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Package,

    [Parameter(Mandatory)]
    [string]$RootCertificate,

    [Parameter(Mandatory)]
    [string]$IssuerCertificate
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Open-MachineStore([string]$Name) {
    $Store = [Security.Cryptography.X509Certificates.X509Store]::new(
        $Name,
        [Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine
    )
    $Store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    return $Store
}

function Add-CertificateIfMissing(
    [Security.Cryptography.X509Certificates.X509Store]$Store,
    [Security.Cryptography.X509Certificates.X509Certificate2]$Certificate
) {
    $Existing = $Store.Certificates.Find(
        [Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
        $Certificate.Thumbprint,
        $false
    )
    if ($Existing.Count -gt 0) {
        return $false
    }
    $Store.Add($Certificate)
    return $true
}

$PackagePath = (Resolve-Path -LiteralPath $Package).Path
$RootPath = (Resolve-Path -LiteralPath $RootCertificate).Path
$IssuerPath = (Resolve-Path -LiteralPath $IssuerCertificate).Path
$Root = [Security.Cryptography.X509Certificates.X509Certificate2]::new($RootPath)
$Issuer = [Security.Cryptography.X509Certificates.X509Certificate2]::new($IssuerPath)
$RootStore = Open-MachineStore "Root"
$IssuerStore = Open-MachineStore "CA"
$RootAdded = $false
$IssuerAdded = $false

try {
    Write-Host "Installing the release root in Local Machine / Trusted Root Certification Authorities."
    $RootAdded = Add-CertificateIfMissing $RootStore $Root
    Write-Host "Installing the Inari issuer in Local Machine / Intermediate Certification Authorities."
    $IssuerAdded = Add-CertificateIfMissing $IssuerStore $Issuer

    Write-Host "Validating the signed MSIX with the same machine stores used by App Installer."
    $Signature = Get-AuthenticodeSignature -LiteralPath $PackagePath
    if ($Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "Windows did not trust the MSIX signature: $($Signature.StatusMessage)"
    }
    if ($null -eq $Signature.SignerCertificate) {
        throw "Windows did not return the MSIX publisher certificate."
    }
    if ($Signature.SignerCertificate.Subject -ne "CN=Pablo Hernández Jiménez") {
        throw "Unexpected MSIX publisher: $($Signature.SignerCertificate.Subject)"
    }
    Write-Host "Windows machine-trust validation succeeded for $($Signature.SignerCertificate.Subject)."
}
finally {
    if ($IssuerAdded) {
        $IssuerStore.Remove($Issuer)
    }
    if ($RootAdded) {
        $RootStore.Remove($Root)
    }
    $IssuerStore.Dispose()
    $RootStore.Dispose()
    $Issuer.Dispose()
    $Root.Dispose()
}
