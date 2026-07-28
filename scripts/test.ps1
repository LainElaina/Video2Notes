[CmdletBinding()]
param(
    [switch]$SkipLint,
    [switch]$NoCoverage
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
        & $VenvPython -m ruff check src tests scripts
        Assert-LastExitCode "Python Ruff lint"
        & $VenvPython -m mypy
        Assert-LastExitCode "Python mypy typecheck"
    }

    & $VenvPython -m compileall -q src tests scripts
    Assert-LastExitCode "Python compileall"

    if ($NoCoverage) {
        & $VenvPython -m unittest discover -s tests -v
        Assert-LastExitCode "Python unit tests"
    }
    else {
        & $VenvPython -m coverage run -m unittest discover -s tests -v
        Assert-LastExitCode "Python unit tests"
        & $VenvPython -m coverage report
        Assert-LastExitCode "Python coverage report"
    }
}
finally {
    Pop-Location
}
