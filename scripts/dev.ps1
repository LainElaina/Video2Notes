[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 8755,
    [switch]$Tauri,
    [string]$DataRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) "artifacts\dev")
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DesktopRoot = Join-Path $RepoRoot "apps\desktop"

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

function ConvertTo-WindowsCommandLineArgument {
    param([AllowEmptyString()][string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }

    $builder = [Text.StringBuilder]::new()
    $null = $builder.Append([char]34)
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes += 1
        }
        elseif ($character -eq [char]34) {
            if ($backslashes -gt 0) {
                $null = $builder.Append([char]92, (2 * $backslashes))
            }
            $null = $builder.Append([char]92)
            $null = $builder.Append([char]34)
            $backslashes = 0
        }
        else {
            if ($backslashes -gt 0) {
                $null = $builder.Append([char]92, $backslashes)
                $backslashes = 0
            }
            $null = $builder.Append($character)
        }
    }
    if ($backslashes -gt 0) {
        $null = $builder.Append([char]92, (2 * $backslashes))
    }
    $null = $builder.Append([char]34)
    return $builder.ToString()
}

function Join-WindowsCommandLineArguments {
    param([string[]]$Values)
    return (($Values | ForEach-Object { ConvertTo-WindowsCommandLineArgument $_ }) -join " ")
}

if (-not (Test-Path -LiteralPath $VenvPython)) { throw "Python environment is missing. Run .\scripts\bootstrap.ps1 first." }
if (-not (Test-Path -LiteralPath (Join-Path $DesktopRoot "node_modules"))) { throw "Desktop dependencies are missing. Run .\scripts\bootstrap.ps1 first." }
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { throw "pnpm is unavailable. Enable Corepack or install pnpm 10." }

New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$TokenBytes = New-Object byte[] 36
$TokenGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $TokenGenerator.GetBytes($TokenBytes)
}
finally {
    $TokenGenerator.Dispose()
}
$SessionToken = [Convert]::ToBase64String($TokenBytes) -replace "[+/=]", "x"
$ApiLog = Join-Path $DataRoot "dev-api.log"
$ApiErrorLog = Join-Path $DataRoot "dev-api.error.log"
$ApiArguments = Join-WindowsCommandLineArguments @("-m", "video2notes", "serve", "--port", "$ApiPort", "--data-root", $DataRoot)
$PreviousApiToken = $env:VIDEO2NOTES_TOKEN
$PreviousApiUrl = $env:VITE_VIDEO2NOTES_API_URL
$PreviousFrontendToken = $env:VITE_VIDEO2NOTES_API_TOKEN
$env:VIDEO2NOTES_TOKEN = $SessionToken
$ApiProcess = Start-Process -FilePath $VenvPython -ArgumentList $ApiArguments -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $ApiLog -RedirectStandardError $ApiErrorLog -PassThru

try {
    $Headers = @{ "X-Video2Notes-Token" = $SessionToken }
    $Ready = $false
    foreach ($Attempt in 1..40) {
        Start-Sleep -Milliseconds 250
        try {
            $null = Invoke-WebRequest -UseBasicParsing -Headers $Headers -Uri "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 1
            $Ready = $true
            break
        }
        catch {
            if ($ApiProcess.HasExited) { throw "The local API exited during startup. See $ApiLog and $ApiErrorLog" }
        }
    }
    if (-not $Ready) { throw "The local API did not become ready. See $ApiLog and $ApiErrorLog" }

    $env:VITE_VIDEO2NOTES_API_URL = "http://127.0.0.1:$ApiPort"
    $env:VITE_VIDEO2NOTES_API_TOKEN = $SessionToken
    Write-Host "Local API: http://127.0.0.1:$ApiPort (loopback only)" -ForegroundColor Green
    Write-Host "Press Ctrl+C to stop both the desktop frontend and local API."
    Push-Location $DesktopRoot
    try {
        if ($Tauri) {
            pnpm tauri dev
        }
        else {
            pnpm dev
        }
        Assert-LastExitCode "Desktop development server"
    }
    finally { Pop-Location }
}
finally {
    if ($ApiProcess -and -not $ApiProcess.HasExited) {
        Stop-Process -Id $ApiProcess.Id -Force
        $ApiProcess.WaitForExit()
    }
    if ($null -eq $PreviousApiToken) { Remove-Item Env:VIDEO2NOTES_TOKEN -ErrorAction SilentlyContinue }
    else { $env:VIDEO2NOTES_TOKEN = $PreviousApiToken }
    if ($null -eq $PreviousApiUrl) { Remove-Item Env:VITE_VIDEO2NOTES_API_URL -ErrorAction SilentlyContinue }
    else { $env:VITE_VIDEO2NOTES_API_URL = $PreviousApiUrl }
    if ($null -eq $PreviousFrontendToken) { Remove-Item Env:VITE_VIDEO2NOTES_API_TOKEN -ErrorAction SilentlyContinue }
    else { $env:VITE_VIDEO2NOTES_API_TOKEN = $PreviousFrontendToken }
}
