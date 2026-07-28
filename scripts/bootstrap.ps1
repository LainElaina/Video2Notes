[CmdletBinding()]
param(
    # Model runtimes are deliberately opt-in: installing them must never download a model.
    [switch]$WithAsr,
    [switch]$WithOcr,
    [switch]$WithPlaywright,
    [switch]$SkipDesktop,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvRoot = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$DesktopRoot = Join-Path $RepoRoot "apps\desktop"

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Require-Command {
    param([string]$Name, [string]$Purpose)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH. $Purpose"
    }
}

Require-Command "python" "Install a native Windows Python 3.11+ and rerun bootstrap."
Require-Command "ffmpeg" "Install ffmpeg (including ffprobe) and rerun bootstrap."
Require-Command "ffprobe" "Install ffmpeg (including ffprobe) and rerun bootstrap."

if (-not (Test-Path -LiteralPath $VenvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $VenvRoot
    }
    else {
        & python -m venv $VenvRoot
    }
    Assert-LastExitCode "Creating the Python virtual environment"
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PosixVenvPython = Join-Path $VenvRoot "bin\python.exe"
    if (Test-Path -LiteralPath $PosixVenvPython) {
        throw "The current .venv was created by an MSYS Python. Remove .venv and rerun bootstrap so a native Windows environment can be created."
    }
    throw "Virtual-environment Python was not created at $VenvPython."
}

& $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
Assert-LastExitCode "Checking Python version (3.11 or newer is required)"

if (-not $Offline) {
    & $VenvPython -m pip install --upgrade pip
    Assert-LastExitCode "Upgrading pip"
}

# Python 3.12+ venvs do not necessarily include setuptools. Because the local
# editable install deliberately disables build isolation, install its declared
# build backend before asking pip to inspect project metadata.
$BuildBackendArguments = @("-m", "pip", "install", "setuptools>=75")
if ($Offline) { $BuildBackendArguments += "--no-index" }
& $VenvPython @BuildBackendArguments
Assert-LastExitCode "Installing the local build backend"

$Extras = @("api", "dev", "render", "secrets")
if ($WithAsr) { $Extras += "asr" }
if ($WithOcr) { $Extras += "ocr" }
$InstallTarget = "{0}[{1}]" -f $RepoRoot, ($Extras -join ",")
$PipArguments = @("-m", "pip", "install", "--no-build-isolation", "--editable", $InstallTarget)
if ($Offline) { $PipArguments += "--no-index" }

# Editable installation replaces its launcher on Windows. Refuse early when a
# running local API has that launcher open instead of leaving a half-uninstalled
# `~ideo2notes-*.dist-info` directory behind.
$LauncherPath = [IO.Path]::GetFullPath((Join-Path $VenvRoot "Scripts\video2notes.exe"))
$LockedLaunchers = @(
    Get-Process -Name "video2notes" -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and [IO.Path]::GetFullPath($_.Path) -eq $LauncherPath
    }
)
if ($LockedLaunchers.Count -gt 0) {
    throw "The local Video2Notes API is running (PID(s): $($LockedLaunchers.Id -join ', ')). Stop it before bootstrap so Windows can safely update the editable launcher."
}
& $VenvPython @PipArguments
Assert-LastExitCode "Installing the local Python package"

if ($WithPlaywright) {
    if ($Offline) {
        throw "-WithPlaywright cannot be combined with -Offline because browser binaries may need to be downloaded."
    }
    & $VenvPython -m pip install playwright
    Assert-LastExitCode "Installing the optional Playwright Python package"
    & $VenvPython -m playwright install chromium
    Assert-LastExitCode "Installing the optional Playwright Chromium browser"
}

if (-not $SkipDesktop -and (Test-Path -LiteralPath (Join-Path $DesktopRoot "package.json"))) {
    Require-Command "node" "Install Node.js 22+ and pnpm, then rerun bootstrap."
    Require-Command "pnpm" "Enable Corepack (corepack enable) or install pnpm 10, then rerun bootstrap."
    Push-Location $DesktopRoot
    try {
        if ($Offline) {
            pnpm install --offline --frozen-lockfile
        }
        else {
            pnpm install --frozen-lockfile
        }
        Assert-LastExitCode "Installing locked desktop dependencies"
    }
    finally {
        Pop-Location
    }

    # Fetching here, rather than in verify, makes verification safe to run without a network.
    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        $CargoFetchArguments = @("fetch", "--manifest-path", (Join-Path $DesktopRoot "src-tauri\Cargo.toml"))
        if ($Offline) { $CargoFetchArguments += "--offline" }
        & cargo @CargoFetchArguments
        Assert-LastExitCode "Fetching locked Rust dependencies"
    }
    else {
        Write-Warning "Rust/Cargo is not installed. Desktop Rust checks and Tauri builds will be unavailable until Rust is installed."
    }
}

Write-Host ""
Write-Host "Video2Notes bootstrap completed." -ForegroundColor Green
Write-Host "  Python:  $VenvPython"
Write-Host "  Tests:   .\scripts\test.ps1"
Write-Host "  Verify:  .\scripts\verify.ps1"
Write-Host "  Dev:     .\scripts\dev.ps1"
Write-Host "  Build:   .\scripts\build.ps1"
if (-not $WithAsr -or -not $WithOcr) {
    Write-Host "  Optional local runtimes: rerun with -WithAsr and/or -WithOcr (models remain local and are never downloaded by bootstrap)."
}
