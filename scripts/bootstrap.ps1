[CmdletBinding()]
param(
    # Model runtimes are deliberately opt-in: installing them must never download a model.
    [switch]$WithAsr,
    [switch]$WithOcr,
    [switch]$WithOcrGpu,
    [switch]$WithPlaywright,
    [switch]$SkipDesktop,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvRoot = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$DesktopRoot = Join-Path $RepoRoot "apps\desktop"
$PublicPypiIndex = "https://pypi.org/simple"
$PaddleCu129Index = "https://www.paddlepaddle.org.cn/packages/stable/cu129/"
$PaddleGpuDistribution = "paddlepaddle-gpu"
$PaddleGpuVersion = "3.3.1"

if ($WithOcr -and $WithOcrGpu) {
    throw "-WithOcr (CPU) and -WithOcrGpu (NVIDIA CUDA) are mutually exclusive. Select exactly one OCR runtime."
}
if ($WithOcrGpu -and $env:OS -ne "Windows_NT") {
    throw "-WithOcrGpu currently supports native Windows only. Use -WithOcr for the portable CPU runtime."
}

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

function Get-PythonDistributionVersion {
    param([string]$Python, [string]$Distribution)
    $probe = @'
import importlib.metadata
import sys

try:
    print(importlib.metadata.version(sys.argv[1]))
except importlib.metadata.PackageNotFoundError:
    pass
'@
    $version = ($probe | & $Python - $Distribution | Out-String).Trim()
    Assert-LastExitCode "Inspecting installed '$Distribution' runtime"
    return $version
}

function Uninstall-PythonDistribution {
    param([string]$Python, [string]$Distribution)
    & $Python -m pip --isolated uninstall --yes $Distribution
    Assert-LastExitCode "Removing incompatible '$Distribution' runtime"
}

function New-TemporaryWheelDirectory {
    $systemTemporary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
    $candidate = [IO.Path]::GetFullPath(
        (Join-Path $systemTemporary ("video2notes-bootstrap-" + [Guid]::NewGuid().ToString("N")))
    )
    if (-not $candidate.StartsWith($systemTemporary, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to create a wheel staging directory outside the system temporary directory."
    }
    New-Item -ItemType Directory -Path $candidate | Out-Null
    return $candidate
}

function Remove-TemporaryWheelDirectory {
    param([string]$Path)
    $systemTemporary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
    $resolved = [IO.Path]::GetFullPath($Path)
    if (
        -not $resolved.StartsWith($systemTemporary, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([IO.Path]::GetFileName($resolved)).StartsWith("video2notes-bootstrap-")
    ) {
        throw "Refusing to recursively remove an unverified wheel staging directory: '$resolved'."
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
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

$InstalledCpuPaddle = Get-PythonDistributionVersion $VenvPython "paddlepaddle"
$InstalledGpuPaddle = Get-PythonDistributionVersion $VenvPython $PaddleGpuDistribution
if ($WithOcrGpu -and $Offline) {
    if ($InstalledGpuPaddle -ne $PaddleGpuVersion -or $InstalledCpuPaddle) {
        throw "Offline NVIDIA OCR setup requires '$PaddleGpuDistribution==$PaddleGpuVersion' to already be installed and the CPU 'paddlepaddle' distribution to be absent. Run once online with -WithOcrGpu."
    }
}
if ($WithOcr -and $Offline -and $InstalledGpuPaddle) {
    throw "Offline CPU OCR setup cannot safely replace an installed Paddle GPU runtime. Run once online with -WithOcr, or use a clean CPU environment."
}

if (-not $Offline) {
    & $VenvPython -m pip --isolated install `
        --no-input `
        --index-url $PublicPypiIndex `
        --upgrade pip
    Assert-LastExitCode "Upgrading pip"
}

# Python 3.12+ venvs do not necessarily include setuptools. Because the local
# editable install deliberately disables build isolation, install its declared
# build backend before asking pip to inspect project metadata.
$BuildBackendArguments = @(
    "-m", "pip", "--isolated", "install", "--no-input", "setuptools>=75"
)
if ($Offline) { $BuildBackendArguments += "--no-index" }
else { $BuildBackendArguments += @("--index-url", $PublicPypiIndex) }
& $VenvPython @BuildBackendArguments
Assert-LastExitCode "Installing the local build backend"

# Switching from the GPU distribution to the CPU distribution must happen
# before the editable ``ocr`` extra is resolved. Both distributions own the
# same ``paddle`` package tree and must never coexist in one environment.
if ($WithOcr -and $InstalledGpuPaddle) {
    $CpuWheelRoot = New-TemporaryWheelDirectory
    try {
        & $VenvPython -m pip --isolated download `
            --no-input `
            --no-deps `
            --only-binary=:all: `
            --dest $CpuWheelRoot `
            --index-url $PublicPypiIndex `
            "paddlepaddle>=3.3,<4"
        Assert-LastExitCode "Downloading the public PaddlePaddle CPU wheel"
        $CpuWheels = @(Get-ChildItem -LiteralPath $CpuWheelRoot -File -Filter "paddlepaddle-*.whl")
        if ($CpuWheels.Count -ne 1) {
            throw "Expected exactly one PaddlePaddle CPU wheel, found $($CpuWheels.Count)."
        }
        Uninstall-PythonDistribution $VenvPython $PaddleGpuDistribution
        if ($InstalledCpuPaddle) {
            Uninstall-PythonDistribution $VenvPython "paddlepaddle"
        }
        & $VenvPython -m pip --isolated install `
            --no-input --no-deps --no-index $CpuWheels[0].FullName
        Assert-LastExitCode "Installing the exclusive PaddlePaddle CPU runtime"
        $InstalledGpuPaddle = ""
        $InstalledCpuPaddle = Get-PythonDistributionVersion $VenvPython "paddlepaddle"
    }
    finally {
        Remove-TemporaryWheelDirectory $CpuWheelRoot
    }
}

$Extras = @("api", "dev", "render", "secrets")
if ($WithAsr) { $Extras += "asr" }
if ($WithOcr) { $Extras += "ocr" }
if ($WithOcrGpu) { $Extras += "ocr-gpu" }
$InstallTarget = "{0}[{1}]" -f $RepoRoot, ($Extras -join ",")
$PipArguments = @(
    "-m", "pip", "--isolated", "install", "--no-input",
    "--no-build-isolation", "--editable", $InstallTarget
)
if ($Offline) { $PipArguments += "--no-index" }
else { $PipArguments += @("--index-url", $PublicPypiIndex) }

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

# The GPU wheel is hosted on Paddle's official CUDA 12.9 index rather than
# PyPI. Download it completely before removing a CPU Paddle installation, then
# install from the verified local staging directory with dependency resolution
# disabled. All CUDA dependencies were already resolved from public PyPI by
# the ``ocr-gpu`` extra above.
if ($WithOcrGpu -and -not $Offline) {
    $InstalledCpuPaddle = Get-PythonDistributionVersion $VenvPython "paddlepaddle"
    $InstalledGpuPaddle = Get-PythonDistributionVersion $VenvPython $PaddleGpuDistribution
    if ($InstalledGpuPaddle -ne $PaddleGpuVersion -or $InstalledCpuPaddle) {
        $GpuWheelRoot = New-TemporaryWheelDirectory
        try {
            & $VenvPython -m pip --isolated download `
                --no-input `
                --no-cache-dir `
                --no-deps `
                --only-binary=:all: `
                --dest $GpuWheelRoot `
                --index-url $PaddleCu129Index `
                "$PaddleGpuDistribution==$PaddleGpuVersion"
            Assert-LastExitCode "Downloading PaddlePaddle GPU from the official cu129 index"
            $GpuWheels = @(
                Get-ChildItem -LiteralPath $GpuWheelRoot -File -Filter "paddlepaddle_gpu-*.whl"
            )
            if ($GpuWheels.Count -ne 1) {
                throw "Expected exactly one PaddlePaddle GPU wheel, found $($GpuWheels.Count)."
            }
            if ($InstalledCpuPaddle) {
                Uninstall-PythonDistribution $VenvPython "paddlepaddle"
            }
            if ($InstalledGpuPaddle) {
                Uninstall-PythonDistribution $VenvPython $PaddleGpuDistribution
            }
            & $VenvPython -m pip --isolated install `
                --no-input --no-deps --no-index $GpuWheels[0].FullName
            Assert-LastExitCode "Installing the exclusive PaddlePaddle GPU runtime"
        }
        finally {
            Remove-TemporaryWheelDirectory $GpuWheelRoot
        }
    }
}

if ($WithOcrGpu) {
    $InstalledCpuPaddle = Get-PythonDistributionVersion $VenvPython "paddlepaddle"
    $InstalledGpuPaddle = Get-PythonDistributionVersion $VenvPython $PaddleGpuDistribution
    if ($InstalledGpuPaddle -ne $PaddleGpuVersion -or $InstalledCpuPaddle) {
        throw "NVIDIA OCR runtime validation failed: expected only '$PaddleGpuDistribution==$PaddleGpuVersion'."
    }
}
elseif ($WithOcr) {
    $InstalledGpuPaddle = Get-PythonDistributionVersion $VenvPython $PaddleGpuDistribution
    $InstalledCpuPaddle = Get-PythonDistributionVersion $VenvPython "paddlepaddle"
    if (-not $InstalledCpuPaddle -or $InstalledGpuPaddle) {
        throw "CPU OCR runtime validation failed: expected only the 'paddlepaddle' distribution."
    }
}

if ($WithPlaywright) {
    if ($Offline) {
        throw "-WithPlaywright cannot be combined with -Offline because browser binaries may need to be downloaded."
    }
    & $VenvPython -m pip --isolated install `
        --no-input --index-url $PublicPypiIndex playwright
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
Write-Host "  Portable: .\scripts\build_portable.ps1"
if (-not $WithAsr -or (-not $WithOcr -and -not $WithOcrGpu)) {
    Write-Host "  Optional local runtimes: rerun with -WithAsr and either -WithOcr (CPU) or -WithOcrGpu (NVIDIA CUDA). Models are never downloaded by bootstrap."
}
