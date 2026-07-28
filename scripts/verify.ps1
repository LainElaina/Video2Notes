[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

Push-Location $RepoRoot
try {
    & (Join-Path $PSScriptRoot "test.ps1")

    $DesktopRoot = Join-Path $RepoRoot "apps\desktop"
    if (Test-Path -LiteralPath (Join-Path $DesktopRoot "package.json")) {
        Push-Location $DesktopRoot
        try {
            pnpm lint
            Assert-LastExitCode "Desktop lint"
            pnpm typecheck
            Assert-LastExitCode "Desktop typecheck"
            pnpm test
            Assert-LastExitCode "Desktop tests"
            pnpm build
            Assert-LastExitCode "Desktop build"
        }
        finally {
            Pop-Location
        }
    }

    Write-Host "Core verification passed." -ForegroundColor Green
}
finally {
    Pop-Location
}
