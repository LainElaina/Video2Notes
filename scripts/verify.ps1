[CmdletBinding()]
param(
    # Strict makes optional verification prerequisites mandatory, as in CI.
    [switch]$Strict,
    [switch]$SkipPython,
    [switch]$SkipDesktop,
    [switch]$SkipCargo,
    [switch]$SkipPlaywright
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$DesktopRoot = Join-Path $RepoRoot "apps\desktop"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$PlaywrightHarness = Join-Path $PSScriptRoot "playwright_harness.py"
$PlaywrightSmoke = Join-Path $PSScriptRoot "playwright_smoke.py"

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Skip-OrThrow {
    param([string]$Message)
    if ($Strict) {
        throw "$Message Strict verification treats this prerequisite as required."
    }
    Write-Warning "$Message Skipping this optional verification. Use -Strict to require it."
}

function Test-PythonModule {
    param([string]$Module)
    if (-not (Test-Path -LiteralPath $VenvPython)) { return $false }
    & $VenvPython -c "import importlib.util, sys; raise SystemExit(0 if importlib.util.find_spec('$Module') else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

function Test-PlaywrightChromium {
    if (-not (Test-PythonModule "playwright")) { return $false }
    & $VenvPython -c "from pathlib import Path; from playwright.sync_api import sync_playwright; p = sync_playwright().start(); executable = p.chromium.executable_path; p.stop(); raise SystemExit(0 if Path(executable).is_file() else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

Push-Location $RepoRoot
try {
    if (-not $SkipPython) {
        & (Join-Path $PSScriptRoot "test.ps1")
        Assert-LastExitCode "Python lint, typecheck, and tests"
    }

    $CanRunDesktop = -not $SkipDesktop -and (Test-Path -LiteralPath (Join-Path $DesktopRoot "package.json"))
    if ($CanRunDesktop) {
        if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
            Skip-OrThrow "pnpm is unavailable. Run .\scripts\bootstrap.ps1 or enable Corepack."
            $CanRunDesktop = $false
        }
        elseif (-not (Test-Path -LiteralPath (Join-Path $DesktopRoot "node_modules"))) {
            Skip-OrThrow "Desktop dependencies are missing. Run .\scripts\bootstrap.ps1."
            $CanRunDesktop = $false
        }
    }

    if ($CanRunDesktop) {
        Push-Location $DesktopRoot
        try {
            pnpm lint
            Assert-LastExitCode "Desktop ESLint"
            pnpm typecheck
            Assert-LastExitCode "Desktop TypeScript typecheck"
            pnpm test
            Assert-LastExitCode "Desktop Vitest"
            pnpm build
            Assert-LastExitCode "Desktop Vite production build"
        }
        finally {
            Pop-Location
        }
    }

    if (-not $SkipCargo -and -not $SkipDesktop -and (Test-Path -LiteralPath (Join-Path $DesktopRoot "src-tauri\Cargo.toml"))) {
        if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
            Skip-OrThrow "Cargo is unavailable. Install the Rust stable toolchain to run Tauri checks."
        }
        else {
            & cargo fmt --manifest-path (Join-Path $DesktopRoot "src-tauri\Cargo.toml") -- --check
            Assert-LastExitCode "Tauri rustfmt"
            # Verification never contacts the registry. Bootstrap performs the explicit fetch.
            & cargo check --offline --manifest-path (Join-Path $DesktopRoot "src-tauri\Cargo.toml")
            Assert-LastExitCode "Tauri cargo check (offline)"
        }
    }

    if (-not $SkipPlaywright) {
        if (-not $CanRunDesktop) {
            Skip-OrThrow "Desktop prerequisites are unavailable, so the Playwright smoke path cannot run."
        }
        elseif (-not (Test-Path -LiteralPath $PlaywrightHarness)) {
            Skip-OrThrow "The repository Playwright server harness is missing at '$PlaywrightHarness'."
        }
        elseif (-not (Test-Path -LiteralPath $PlaywrightSmoke)) {
            Skip-OrThrow "Playwright smoke script is missing at '$PlaywrightSmoke'."
        }
        elseif (-not (Test-PythonModule "playwright")) {
            Skip-OrThrow "Python Playwright is not installed. Run .\scripts\bootstrap.ps1 -WithPlaywright."
        }
        elseif (-not (Test-PlaywrightChromium)) {
            Skip-OrThrow "Playwright Chromium is not installed. Run .\scripts\bootstrap.ps1 -WithPlaywright."
        }
        else {
            & $VenvPython $PlaywrightHarness `
                --desktop-root $DesktopRoot `
                --python $VenvPython `
                --smoke-script $PlaywrightSmoke `
                --base-url "http://127.0.0.1:1420"
            Assert-LastExitCode "Playwright primary-path smoke test"
        }
    }

    Write-Host "Verification completed." -ForegroundColor Green
}
finally {
    Pop-Location
}
