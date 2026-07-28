[CmdletBinding()]
param(
    [switch]$SkipPython,
    [switch]$SkipDesktop,
    [switch]$Tauri,
    [switch]$SkipSidecarSmoke,
    [string]$FfmpegDirectory = "",
    [string]$FfmpegLicensePath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DesktopRoot = Join-Path $RepoRoot "apps\desktop"
$ReleaseRoot = Join-Path $RepoRoot "artifacts\release\python"

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

Push-Location $RepoRoot
try {
    if (-not $SkipPython) {
        if (-not (Test-Path -LiteralPath $VenvPython)) { throw "Python environment is missing. Run .\scripts\bootstrap.ps1 first." }
        New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
        # --no-build-isolation prevents an implicit package-index request during a local build.
        & $VenvPython -m pip wheel --no-deps --no-build-isolation --wheel-dir $ReleaseRoot $RepoRoot
        Assert-LastExitCode "Building the local Python wheel"
    }

    if (-not $SkipDesktop -and (Test-Path -LiteralPath (Join-Path $DesktopRoot "package.json"))) {
        if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { throw "pnpm is unavailable. Run .\scripts\bootstrap.ps1 first." }
        if (-not (Test-Path -LiteralPath (Join-Path $DesktopRoot "node_modules"))) { throw "Desktop dependencies are missing. Run .\scripts\bootstrap.ps1 first." }
        Push-Location $DesktopRoot
        try {
            if ($Tauri) {
                $SidecarArguments = @()
                if ($SkipSidecarSmoke) { $SidecarArguments += "-SkipSmoke" }
                if ($FfmpegDirectory) { $SidecarArguments += @("-FfmpegDirectory", $FfmpegDirectory) }
                if ($FfmpegLicensePath) { $SidecarArguments += @("-FfmpegLicensePath", $FfmpegLicensePath) }
                & (Join-Path $PSScriptRoot "build_sidecar.ps1") @SidecarArguments
                Assert-LastExitCode "Building and smoke-testing the self-contained backend sidecar"
                pnpm tauri build
                Assert-LastExitCode "Building the Tauri installer"
            }
            else {
                pnpm build
                Assert-LastExitCode "Building the Vite desktop frontend"
                Write-Host "Tauri installer was not built. Use .\scripts\build.ps1 -Tauri to create it."
            }
        }
        finally { Pop-Location }
    }
    Write-Host "Build completed." -ForegroundColor Green
}
finally { Pop-Location }
