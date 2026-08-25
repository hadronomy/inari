[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BundleTarget = Join-Path $WorkspaceRoot "target\pyinstaller-verification"
$ExecutableIcon = Join-Path $WorkspaceRoot "target\release\windows\assets\InariDeviceCenter.ico"
$BundleSpec = Join-Path $PSScriptRoot "inari.spec"
$Uv = (Get-Command "uv" -ErrorAction Stop).Source

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

Push-Location $WorkspaceRoot
try {
    & $Uv sync --all-packages --frozen --group windows-build
    Assert-NativeCommandSucceeded $LASTEXITCODE "Python dependency synchronization"

    & $Uv run --no-sync python deploy/windows/build.py icon --output $ExecutableIcon
    Assert-NativeCommandSucceeded $LASTEXITCODE "Windows icon generation"

    & $Uv run --no-sync pyinstaller `
        --noconfirm `
        --clean `
        --workpath (Join-Path $BundleTarget "work") `
        --distpath (Join-Path $BundleTarget "dist") `
        $BundleSpec
    Assert-NativeCommandSucceeded $LASTEXITCODE "PyInstaller bundle creation"

    $Executable = Join-Path $BundleTarget "dist\InariAgentService\InariAgentService.exe"
    $Report = Join-Path $BundleTarget "runtime-verification.txt"
    Remove-Item $Report -Force -ErrorAction SilentlyContinue
    Invoke-BoundedProcess `
        $Executable `
        @("--verify-runtime", $Report) `
        30 `
        "Frozen Agent runtime verification"
    if (-not (Test-Path -LiteralPath $Report -PathType Leaf)) {
        throw "The frozen Agent did not produce a runtime verification report."
    }
    Get-Content -LiteralPath $Report
}
finally {
    Pop-Location
}
