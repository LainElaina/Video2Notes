[CmdletBinding()]
param(
    [switch]$SkipLint
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Python environment is missing. Run .\scripts\bootstrap.ps1 first."
}

Push-Location $RepoRoot
try {
    if (-not $SkipLint) {
        & $VenvPython -m ruff check src tests
        Assert-LastExitCode "Ruff"
        & $VenvPython -m mypy
        Assert-LastExitCode "Mypy"
    }
    & $VenvPython -m compileall -q src tests
    Assert-LastExitCode "Compileall"
    & $VenvPython -m coverage run -m unittest discover -s tests -v
    Assert-LastExitCode "Unit tests"
    & $VenvPython -m coverage report
    Assert-LastExitCode "Coverage report"
}
finally {
    Pop-Location
}
