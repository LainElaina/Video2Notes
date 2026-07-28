[CmdletBinding()]
param(
    [switch]$WithApi,
    [switch]$WithAsr,
    [switch]$WithOcr
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvRoot = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"

foreach ($Command in @("python", "ffmpeg", "ffprobe")) {
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "Required command '$Command' was not found on PATH."
    }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $VenvRoot
    }
    else {
        & python -m venv $VenvRoot
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python virtual environment."
    }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PosixVenvPython = Join-Path $VenvRoot "bin\python.exe"
    if (Test-Path -LiteralPath $PosixVenvPython) {
        throw "The current .venv was created by an MSYS Python. Remove .venv and rerun bootstrap so the Windows Python launcher can create a native environment."
    }
    throw "Virtual environment Python was not created at $VenvPython."
}

& $VenvPython -m pip install --upgrade pip

$Extras = @("dev", "render", "secrets")
if ($WithApi) { $Extras += "api" }
if ($WithAsr) { $Extras += "asr" }
if ($WithOcr) { $Extras += "ocr" }

$ExtraSpec = $Extras -join ","
$InstallTarget = "{0}[{1}]" -f $RepoRoot, $ExtraSpec
& $VenvPython -m pip install -e $InstallTarget

Write-Host ""
Write-Host "Video2Notes Python environment is ready:" -ForegroundColor Green
Write-Host "  $VenvPython"
Write-Host "Run .\scripts\test.ps1 to verify the core."
