[CmdletBinding()]
param(
    [string]$Executable = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Executable) {
    $Executable = Join-Path $RepoRoot "apps\desktop\src-tauri\resources\backend\video2notes.exe"
}

function New-SessionToken {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) }
    finally { $generator.Dispose() }
    return [Convert]::ToBase64String($bytes) -replace "[+/=]", "x"
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

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Packaged backend was not found at '$Executable'. Run .\scripts\build_sidecar.ps1 first."
}
$ResolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$help = & $ResolvedExecutable --help 2>&1
if ($LASTEXITCODE -ne 0 -or ($help -join "`n") -notmatch "evidence-first") {
    throw "The packaged sidecar did not provide a valid --help response."
}

$port = Get-Random -Minimum 44000 -Maximum 48000
$temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
$temporaryRoot = [IO.Path]::GetFullPath(
    (Join-Path $temporaryBase ("video2notes-sidecar-smoke-" + [Guid]::NewGuid().ToString("N")))
)
if (
    -not $temporaryRoot.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase) -or
    -not (Split-Path -Leaf $temporaryRoot).StartsWith("video2notes-sidecar-smoke-")
) {
    throw "Refusing to create or remove an unsafe sidecar smoke directory."
}
$token = New-SessionToken
$previousPath = $env:PATH
$previousToken = $env:VIDEO2NOTES_TOKEN
New-Item -ItemType Directory -Force -Path $temporaryRoot | Out-Null
$dataRoot = Join-Path $temporaryRoot "data root with spaces"
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$stdout = Join-Path $temporaryRoot "server.stdout.log"
$stderr = Join-Path $temporaryRoot "server.stderr.log"
$process = $null
try {
    # This intentionally removes a developer-installed FFmpeg from PATH. The
    # packaged runtime hook must locate tools/ffmpeg.exe beside the sidecar.
    $env:PATH = Join-Path $env:WINDIR "System32"
    $env:VIDEO2NOTES_TOKEN = $token
    $arguments = Join-WindowsCommandLineArguments @("serve", "--port", "$port", "--data-root", $dataRoot)
    $process = Start-Process -FilePath $ResolvedExecutable -ArgumentList $arguments -WorkingDirectory $temporaryRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $ready = $false
    foreach ($attempt in 1..80) {
        Start-Sleep -Milliseconds 250
        try {
            $response = Invoke-RestMethod -Headers @{ "X-Video2Notes-Token" = $token } -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 1
            if ($null -ne $response) {
                $ready = $true
                break
            }
        }
        catch {
            if ($process.HasExited) {
                $stderrText = if (Test-Path -LiteralPath $stderr) { Get-Content -Raw $stderr } else { "" }
                throw "Packaged sidecar exited before health became ready. $stderrText"
            }
        }
    }
    if (-not $ready) { throw "Packaged sidecar did not become healthy within 20 seconds." }
    Write-Host "Packaged backend --help and loopback health smoke passed." -ForegroundColor Green
}
finally {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }
    $env:PATH = $previousPath
    if ($null -eq $previousToken) { Remove-Item Env:VIDEO2NOTES_TOKEN -ErrorAction SilentlyContinue }
    else { $env:VIDEO2NOTES_TOKEN = $previousToken }
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
