[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PortableRoot,
    [string]$Python = "",
    [ValidateRange(10, 120)]
    [int]$TimeoutSeconds = 40,
    [string]$ScreenshotPath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ResolvedRoot = [IO.Path]::GetFullPath($PortableRoot).TrimEnd("\")
$Executable = Join-Path $ResolvedRoot "Video2Notes.exe"
$MarkerPath = Join-Path $ResolvedRoot ".video2notes-portable.json"
$SmokeScript = Join-Path $PSScriptRoot "playwright_desktop_connection_smoke.py"
if (-not $Python) {
    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
}

foreach ($required in @($Executable, $MarkerPath, $Python, $SmokeScript)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Portable desktop smoke dependency was not found at '$required'."
    }
}
$marker = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
if ($marker.product -ne "Video2Notes" -or $marker.portable -ne $true) {
    throw "Refusing to start an unmarked portable directory at '$ResolvedRoot'."
}

$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$debugPort = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()
$debugUrl = "http://127.0.0.1:$debugPort"

$temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
$temporaryRoot = [IO.Path]::GetFullPath(
    (Join-Path $temporaryBase ("video2notes-desktop-smoke-" + [Guid]::NewGuid().ToString("N")))
)
if (
    -not $temporaryRoot.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase) -or
    -not (Split-Path -Leaf $temporaryRoot).StartsWith("video2notes-desktop-smoke-")
) {
    throw "Refusing to create or remove an unsafe desktop smoke directory."
}
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null

$previousBrowserArguments = $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
$previousDataRoot = $env:VIDEO2NOTES_DATA_ROOT
$desktop = $null
$smokeFailure = $null
try {
    $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=$debugPort"
    $env:VIDEO2NOTES_DATA_ROOT = Join-Path $temporaryRoot "data"
    $desktop = Start-Process `
        -FilePath $Executable `
        -WorkingDirectory $ResolvedRoot `
        -WindowStyle Hidden `
        -PassThru

    $debugReady = $false
    foreach ($attempt in 1..($TimeoutSeconds * 4)) {
        Start-Sleep -Milliseconds 250
        $desktop.Refresh()
        if ($desktop.HasExited) {
            $logPath = Join-Path $env:VIDEO2NOTES_DATA_ROOT "logs\backend-session.log"
            $log = if (Test-Path -LiteralPath $logPath) {
                Get-Content -LiteralPath $logPath -Raw
            }
            else {
                ""
            }
            throw "Portable desktop exited before WebView2 was ready. $log"
        }
        try {
            $null = Invoke-RestMethod -Uri "$debugUrl/json/version" -TimeoutSec 1
            $debugReady = $true
            break
        }
        catch {
            # WebView2 is still starting.
        }
    }
    if (-not $debugReady) {
        throw "Portable desktop did not expose WebView2 diagnostics within $TimeoutSeconds seconds."
    }

    $arguments = @(
        $SmokeScript,
        "--cdp-url", $debugUrl,
        "--timeout-seconds", "$TimeoutSeconds"
    )
    if ($ScreenshotPath) {
        $arguments += @("--screenshot", [IO.Path]::GetFullPath($ScreenshotPath))
    }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Portable desktop WebView connection smoke failed with exit code $LASTEXITCODE."
    }
}
catch {
    $smokeFailure = $_
}
finally {
    if ($desktop) {
        $desktop.Refresh()
        if (-not $desktop.HasExited) {
            $null = $desktop.CloseMainWindow()
            if (-not $desktop.WaitForExit(10000)) {
                Stop-Process -Id $desktop.Id -Force
                $desktop.WaitForExit()
            }
        }
    }

    $remaining = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.ExecutablePath -and
                [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
                    "$ResolvedRoot\",
                    [StringComparison]::OrdinalIgnoreCase
                )
            }
    )
    if ($remaining.Count -gt 0) {
        foreach ($process in $remaining) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
        if (-not $smokeFailure) {
            $details = $remaining | ForEach-Object { "$($_.Name) (PID $($_.ProcessId))" }
            $smokeFailure = "Portable desktop left child processes running: $($details -join ', ')."
        }
    }

    if ($null -eq $previousBrowserArguments) {
        Remove-Item Env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS -ErrorAction SilentlyContinue
    }
    else {
        $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = $previousBrowserArguments
    }
    if ($null -eq $previousDataRoot) {
        Remove-Item Env:VIDEO2NOTES_DATA_ROOT -ErrorAction SilentlyContinue
    }
    else {
        $env:VIDEO2NOTES_DATA_ROOT = $previousDataRoot
    }
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($smokeFailure) {
    throw $smokeFailure
}
Write-Host "Portable desktop UI, backend connection, and shutdown smoke passed." -ForegroundColor Green
