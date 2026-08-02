[CmdletBinding()]
param(
    [string]$Executable = "",
    [ValidateRange(5, 120)][int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Executable) {
    $Executable = Join-Path $RepoRoot "artifacts\portable\current\backend\video2notes.exe"
}

function Require-File {
    param([string]$Path, [string]$Purpose)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Purpose was not found at '$Path'."
    }
}

function Require-Directory {
    param([string]$Path, [string]$Purpose)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Purpose was not found at '$Path'."
    }
}

function Get-ManifestComponent {
    param([object]$Manifest, [string]$Id)
    $matches = @($Manifest.components | Where-Object { $_.id -eq $Id })
    if ($matches.Count -ne 1) {
        throw "The backend manifest must contain exactly one '$Id' component."
    }
    return $matches[0]
}

function New-SessionToken {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes) -replace "[+/=]", "x"
}

function Get-FreeLoopbackPort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
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

function Assert-SafeTemporaryRoot {
    param([string]$Path)

    $temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
    $resolved = [IO.Path]::GetFullPath($Path).TrimEnd("\")
    $leaf = [IO.Path]::GetFileName($resolved)
    if (
        -not $resolved.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase) -or
        -not $leaf.StartsWith("video2notes-portable-cuda-", [StringComparison]::Ordinal)
    ) {
        throw "Refusing to create or remove an unsafe portable CUDA test directory."
    }
    return $resolved
}

function Get-SanitizedLogText {
    param([string]$Path, [string]$Secret)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    $content = Get-Content -LiteralPath $Path -Raw
    if ($Secret) {
        $content = $content -replace [Regex]::Escape($Secret), "[redacted]"
    }
    if ($content.Length -gt 4000) {
        return $content.Substring($content.Length - 4000)
    }
    return $content
}

Require-File $Executable "Packaged backend"
$ResolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$BackendRoot = Split-Path -Parent $ResolvedExecutable
$ManifestPath = Join-Path $BackendRoot "manifest.json"
Require-File $ManifestPath "Packaged backend manifest"
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($Manifest.schema -ne 2 -or $Manifest.runtime_flavor -ne "full") {
    throw "CUDA acceptance requires a schema-2 full packaged backend."
}

$PaddleComponent = Get-ManifestComponent $Manifest "paddlepaddle"
if ($PaddleComponent.distribution -notin @("paddlepaddle", "paddlepaddle-gpu")) {
    throw "The backend manifest has an unsupported PaddlePaddle distribution."
}
$PaddleGpuRuntime = $PaddleComponent.distribution -eq "paddlepaddle-gpu"
$NvidiaRuntimeSpecs = @(
    [ordered]@{ id = "nvidia-cublas-cu12"; directory = "cublas"; dll = "cublas64_12.dll" },
    [ordered]@{ id = "nvidia-cuda-nvrtc-cu12"; directory = "cuda_nvrtc"; dll = "nvrtc64_120_0.dll" },
    [ordered]@{ id = "nvidia-cudnn-cu12"; directory = "cudnn"; dll = "cudnn64_9.dll" },
    [ordered]@{ id = "nvidia-nvjitlink-cu12"; directory = "nvjitlink"; dll = "nvJitLink_120_0.dll" }
)
if ($PaddleGpuRuntime) {
    $NvidiaRuntimeSpecs += @(
        [ordered]@{ id = "nvidia-cuda-runtime-cu12"; directory = "cuda_runtime"; dll = "cudart64_12.dll" },
        [ordered]@{ id = "nvidia-cufft-cu12"; directory = "cufft"; dll = "cufft64_11.dll" },
        [ordered]@{ id = "nvidia-curand-cu12"; directory = "curand"; dll = "curand64_10.dll" },
        [ordered]@{ id = "nvidia-cusolver-cu12"; directory = "cusolver"; dll = "cusolver64_11.dll" },
        [ordered]@{ id = "nvidia-cusparse-cu12"; directory = "cusparse"; dll = "cusparse64_12.dll" }
    )
}
foreach ($runtime in $NvidiaRuntimeSpecs) {
    $component = Get-ManifestComponent $Manifest ([string]$runtime.id)
    if (
        $component.kind -ne "python-package" -or
        $component.included -ne $true -or
        $component.import_verified -ne $true -or
        $component.status -ne "bundled" -or
        -not $component.version
    ) {
        throw "Manifest component '$([string]$runtime.id)' is not a bundled, import-verified runtime."
    }
}

$ExpectedRuntimeDirectories = @(
    foreach ($runtime in $NvidiaRuntimeSpecs) {
        $directory = Join-Path `
            $BackendRoot `
            "_internal\nvidia\$([string]$runtime.directory)\bin"
        Require-Directory $directory "Bundled NVIDIA runtime directory"
        Require-File `
            (Join-Path $directory ([string]$runtime.dll)) `
            "Bundled NVIDIA runtime DLL"
        (Resolve-Path -LiteralPath $directory).Path
    }
)
$PackagedNvidiaRoot = [IO.Path]::GetFullPath(
    (Join-Path $BackendRoot "_internal\nvidia")
).TrimEnd("\")
$PackagedNvidiaPrefix = $PackagedNvidiaRoot + "\"

if (-not $env:WINDIR) {
    throw "WINDIR is unavailable; the packaged Windows runtime cannot be tested safely."
}
$IsolatedPath = Join-Path $env:WINDIR "System32"
Require-Directory $IsolatedPath "Windows System32 directory"

$TemporaryRoot = Assert-SafeTemporaryRoot (
    Join-Path ([IO.Path]::GetTempPath()) (
        "video2notes-portable-cuda-" + [Guid]::NewGuid().ToString("N")
    )
)
$DataRoot = Join-Path $TemporaryRoot "data root with spaces"
$StdoutPath = Join-Path $TemporaryRoot "server.stdout.log"
$StderrPath = Join-Path $TemporaryRoot "server.stderr.log"
$EnvironmentNames = @(
    "PATH",
    "VIDEO2NOTES_TOKEN",
    "VIDEO2NOTES_NVIDIA_RUNTIME_ROOT",
    "PYTHONHOME",
    "PYTHONPATH"
)
$SavedEnvironment = @{}
foreach ($name in $EnvironmentNames) {
    $SavedEnvironment[$name] = [pscustomobject]@{
        Exists = Test-Path -LiteralPath "Env:$name"
        Value = [Environment]::GetEnvironmentVariable(
            $name,
            [EnvironmentVariableTarget]::Process
        )
    }
}

$Token = New-SessionToken
$Port = Get-FreeLoopbackPort
$Process = $null
try {
    New-Item -ItemType Directory -Path $TemporaryRoot | Out-Null
    New-Item -ItemType Directory -Path $DataRoot | Out-Null

    # Keep only Windows system tools on PATH. The sidecar must discover all
    # CUDA runtime DLLs from its own _internal/nvidia package tree.
    [Environment]::SetEnvironmentVariable(
        "PATH",
        $IsolatedPath,
        [EnvironmentVariableTarget]::Process
    )
    foreach ($name in @(
        "VIDEO2NOTES_NVIDIA_RUNTIME_ROOT",
        "PYTHONHOME",
        "PYTHONPATH"
    )) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $null,
            [EnvironmentVariableTarget]::Process
        )
    }
    [Environment]::SetEnvironmentVariable(
        "VIDEO2NOTES_TOKEN",
        $Token,
        [EnvironmentVariableTarget]::Process
    )

    $Arguments = Join-WindowsCommandLineArguments @(
        "serve",
        "--port",
        "$Port",
        "--data-root",
        $DataRoot
    )
    $Process = Start-Process `
        -FilePath $ResolvedExecutable `
        -ArgumentList $Arguments `
        -WorkingDirectory $TemporaryRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -PassThru

    $HealthUri = "http://127.0.0.1:$Port/api/health"
    $SystemUri = "http://127.0.0.1:$Port/api/system"
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $Health = $null
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($Process.HasExited) {
            $safeError = Get-SanitizedLogText $StderrPath $Token
            throw "Packaged backend exited before health became ready. $safeError"
        }
        try {
            $candidate = Invoke-RestMethod -Uri $HealthUri -TimeoutSec 1
            if ($candidate.status -eq "ok" -and $candidate.scope -eq "local-only") {
                $Health = $candidate
                break
            }
        }
        catch {
            if ($Process.HasExited) {
                $safeError = Get-SanitizedLogText $StderrPath $Token
                throw "Packaged backend exited before health became ready. $safeError"
            }
        }
        Start-Sleep -Milliseconds 200
    }
    if ($null -eq $Health) {
        $safeError = Get-SanitizedLogText $StderrPath $Token
        throw "Packaged backend did not become healthy within $TimeoutSeconds seconds. $safeError"
    }

    $AcceptedWithoutToken = $false
    try {
        $null = Invoke-RestMethod -Uri $SystemUri -TimeoutSec 5
        $AcceptedWithoutToken = $true
    }
    catch {
        $response = $_.Exception.Response
        if ($null -eq $response -or [int]$response.StatusCode -ne 401) {
            throw "Unauthenticated /api/system did not return HTTP 401."
        }
    }
    if ($AcceptedWithoutToken) {
        throw "Unauthenticated /api/system unexpectedly accepted the request."
    }

    $Report = Invoke-RestMethod `
        -Headers @{ "X-Video2Notes-Token" = $Token } `
        -Uri $SystemUri `
        -TimeoutSec $TimeoutSeconds
    $Asr = $Report.acceleration.asr
    if ($null -eq $Asr -or $Asr.cuda_available -ne $true) {
        $reason = if ($null -ne $Asr) { [string]$Asr.reason } else { "missing ASR report" }
        throw "Packaged ASR CUDA is unavailable: $reason"
    }
    if ([int]$Asr.device_count -lt 1) {
        throw "Packaged ASR CUDA reported no NVIDIA device."
    }
    $ComputeTypes = @($Asr.supported_compute_types | ForEach-Object { [string]$_ })
    if (@($ComputeTypes | Where-Object { $_ -in @("float16", "int8_float16") }).Count -eq 0) {
        throw "Packaged ASR CUDA supports neither float16 nor int8_float16."
    }

    $ReportedRuntimeDirectories = @(
        $Asr.runtime_directories | ForEach-Object { [string]$_ }
    )
    $ReportedDirectorySet = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($directory in $ReportedRuntimeDirectories) {
        Require-Directory $directory "Reported NVIDIA runtime directory"
        $resolved = (Resolve-Path -LiteralPath $directory).Path
        if (
            -not $resolved.StartsWith(
                $PackagedNvidiaPrefix,
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            $resolved -match "(?i)(^|[\\/])\.venv([\\/]|$)"
        ) {
            throw "ASR reported an external NVIDIA runtime directory: '$resolved'."
        }
        $null = $ReportedDirectorySet.Add($resolved)
    }
    foreach ($expected in $ExpectedRuntimeDirectories) {
        if (-not $ReportedDirectorySet.Contains($expected)) {
            throw "ASR did not report packaged NVIDIA runtime directory '$expected'."
        }
    }

    if ($PaddleGpuRuntime) {
        $Ocr = $Report.acceleration.ocr
        if ($null -eq $Ocr -or $Ocr.cuda_available -ne $true) {
            $reason = if ($null -ne $Ocr) { [string]$Ocr.reason } else { "missing OCR report" }
            throw "Packaged PaddleOCR CUDA is unavailable: $reason"
        }
        if ([int]$Ocr.device_count -lt 1) {
            throw "Packaged PaddleOCR CUDA reported no NVIDIA device."
        }
    }

    Write-Host (
        "ASR CUDA verified: engine={0}; devices={1}; compute_types={2}" -f
            $Asr.engine,
            $Asr.device_count,
            ($ComputeTypes -join ",")
    )
    Write-Host (
        "Bundled NVIDIA runtime directories verified: {0}" -f
            ($ExpectedRuntimeDirectories -join "; ")
    )
    if ($PaddleGpuRuntime) {
        Write-Host (
            "PaddleOCR CUDA verified: engine={0}; devices={1}" -f
                $Ocr.engine,
                $Ocr.device_count
        )
    }
    Write-Host "Packaged /api/system CUDA acceptance passed." -ForegroundColor Green
}
finally {
    try {
        if ($Process -and -not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
            if (-not $Process.WaitForExit(5000)) {
                throw "Packaged backend did not stop within five seconds."
            }
        }
    }
    finally {
        try {
            foreach ($name in $EnvironmentNames) {
                $saved = $SavedEnvironment[$name]
                $value = if ($saved.Exists) { $saved.Value } else { $null }
                [Environment]::SetEnvironmentVariable(
                    $name,
                    $value,
                    [EnvironmentVariableTarget]::Process
                )
            }
        }
        finally {
            if (Test-Path -LiteralPath $TemporaryRoot -PathType Container) {
                $SafeCleanupRoot = Assert-SafeTemporaryRoot $TemporaryRoot
                Remove-Item `
                    -LiteralPath $SafeCleanupRoot `
                    -Recurse `
                    -Force `
                    -ErrorAction SilentlyContinue
            }
        }
    }
}
