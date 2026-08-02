[CmdletBinding()]
param(
    [string]$Executable = "",
    [switch]$CoreOnly,
    [switch]$SkipHealthSmoke
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Executable) {
    $Executable = Join-Path $RepoRoot "apps\desktop\src-tauri\resources\backend\video2notes.exe"
}
$ExpectedRuntimeFlavor = if ($CoreOnly) { "core-only" } else { "full" }

function Require-File {
    param([string]$Path, [string]$Purpose)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
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

Require-File $Executable "Packaged backend"
$ResolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$BackendRoot = Split-Path -Parent $ResolvedExecutable
$ManifestPath = Join-Path $BackendRoot "manifest.json"
Require-File $ManifestPath "Packaged backend manifest"
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($Manifest.schema -ne 2 -or $Manifest.runtime_flavor -ne $ExpectedRuntimeFlavor) {
    throw "Expected a schema-2 '$ExpectedRuntimeFlavor' backend manifest, found schema '$($Manifest.schema)' runtime '$($Manifest.runtime_flavor)'."
}
if ($Manifest.user_model_weights_included -ne $false) {
    throw "The backend manifest does not explicitly exclude user model weights."
}

$NvidiaRuntimeSpecs = @(
    [ordered]@{ id = "nvidia-cublas-cu12"; directory = "cublas"; dll = "cublas64_12.dll" },
    [ordered]@{ id = "nvidia-cuda-nvrtc-cu12"; directory = "cuda_nvrtc"; dll = "nvrtc64_120_0.dll" },
    [ordered]@{ id = "nvidia-cudnn-cu12"; directory = "cudnn"; dll = "cudnn64_9.dll" },
    [ordered]@{ id = "nvidia-nvjitlink-cu12"; directory = "nvjitlink"; dll = "nvJitLink_120_0.dll" }
)
$PaddleComponent = Get-ManifestComponent $Manifest "paddlepaddle"
if (-not $CoreOnly -and $PaddleComponent.distribution -eq "paddlepaddle-gpu") {
    $NvidiaRuntimeSpecs += @(
        [ordered]@{ id = "nvidia-cuda-runtime-cu12"; directory = "cuda_runtime"; dll = "cudart64_12.dll" },
        [ordered]@{ id = "nvidia-cufft-cu12"; directory = "cufft"; dll = "cufft64_11.dll" },
        [ordered]@{ id = "nvidia-curand-cu12"; directory = "curand"; dll = "curand64_10.dll" },
        [ordered]@{ id = "nvidia-cusolver-cu12"; directory = "cusolver"; dll = "cusolver64_11.dll" },
        [ordered]@{ id = "nvidia-cusparse-cu12"; directory = "cusparse"; dll = "cusparse64_12.dll" }
    )
}
$FullInferenceIds = @(
    "faster-whisper", "ctranslate2", "huggingface-hub", "paddleocr", "paddlepaddle"
    $NvidiaRuntimeSpecs | ForEach-Object { [string]$_.id }
)
$RequiredPythonIds = @("yt-dlp", "psutil")
if (-not $CoreOnly) { $RequiredPythonIds += $FullInferenceIds }
foreach ($componentId in $RequiredPythonIds) {
    $component = Get-ManifestComponent $Manifest $componentId
    if (
        $component.kind -ne "python-package" -or
        $component.included -ne $true -or
        $component.import_verified -ne $true -or
        $component.status -ne "bundled" -or
        -not $component.version
    ) {
        throw "Manifest component '$componentId' is not a versioned, import-verified bundled Python runtime."
    }
}
if ($CoreOnly) {
    foreach ($componentId in $FullInferenceIds) {
        $component = Get-ManifestComponent $Manifest $componentId
        if ($component.included -ne $false -or $component.status -ne "excluded-core-only") {
            throw "Core-only manifest component '$componentId' was not marked excluded-core-only."
        }
    }
}
else {
    foreach ($runtime in $NvidiaRuntimeSpecs) {
        $cudaRuntimeFile = "_internal\nvidia\$([string]$runtime.directory)\bin\$([string]$runtime.dll)"
        Require-File (Join-Path $BackendRoot $cudaRuntimeFile) "Bundled NVIDIA CUDA runtime DLL"
        $metadataPrefix = ([string]$runtime.id) -replace "-", "_"
        $metadataDirectories = @(
            Get-ChildItem -LiteralPath (Join-Path $BackendRoot "_internal") -Directory |
                Where-Object { $_.Name -like "$metadataPrefix-*.dist-info" }
        )
        if ($metadataDirectories.Count -ne 1) {
            throw "Expected exactly one bundled '$metadataPrefix' metadata directory, found $($metadataDirectories.Count)."
        }
        $licenseFiles = @(
            Get-ChildItem -LiteralPath $metadataDirectories[0].FullName -Recurse -File |
                Where-Object { $_.Name -match "(?i)^licen[cs]e(?:\..*)?$" }
        )
        if ($licenseFiles.Count -lt 1) {
            throw "Bundled NVIDIA runtime '$metadataPrefix' is missing its retained wheel license."
        }
    }
    Require-File `
        (Join-Path $BackendRoot "_internal\nvidia\cublas\bin\cublasLt64_12.dll") `
        "Bundled NVIDIA cuBLAS LT runtime DLL"
}

foreach ($toolName in @("ffmpeg", "ffprobe")) {
    $toolPath = Join-Path $BackendRoot "tools\$toolName.exe"
    Require-File $toolPath "Bundled $toolName.exe"
    $component = Get-ManifestComponent $Manifest $toolName
    if (
        $component.kind -ne "executable" -or
        $component.included -ne $true -or
        $component.executable_verified -ne $true -or
        $component.status -ne "bundled" -or
        -not $component.version
    ) {
        throw "Manifest component '$toolName' is not a versioned, verified bundled executable."
    }
    $null = & $toolPath -version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Bundled $toolName.exe did not provide a valid -version response."
    }
}

$probeFileBase = Join-Path (
    [IO.Path]::GetTempPath()
) ("video2notes-runtime-probe-" + [Guid]::NewGuid().ToString("N"))
$probeStdout = "$probeFileBase.stdout.log"
$probeStderr = "$probeFileBase.stderr.log"
$previousProbe = $env:VIDEO2NOTES_RUNTIME_PROBE
$hadPreviousProbe = Test-Path Env:VIDEO2NOTES_RUNTIME_PROBE
try {
    $env:VIDEO2NOTES_RUNTIME_PROBE = "1"
    $probeProcess = Start-Process `
        -FilePath $ResolvedExecutable `
        -WorkingDirectory $BackendRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $probeStdout `
        -RedirectStandardError $probeStderr `
        -PassThru `
        -Wait
    $probeExitCode = $probeProcess.ExitCode
    $probeOutput = @(
        if (Test-Path -LiteralPath $probeStdout) { Get-Content -LiteralPath $probeStdout }
        if (Test-Path -LiteralPath $probeStderr) { Get-Content -LiteralPath $probeStderr }
    )
}
finally {
    if ($hadPreviousProbe) { $env:VIDEO2NOTES_RUNTIME_PROBE = $previousProbe }
    else { Remove-Item Env:VIDEO2NOTES_RUNTIME_PROBE -ErrorAction SilentlyContinue }
    foreach ($probeLog in @($probeStdout, $probeStderr)) {
        if (Test-Path -LiteralPath $probeLog) {
            Remove-Item -LiteralPath $probeLog -Force -ErrorAction SilentlyContinue
        }
    }
}
$probeJsonLines = @(
    $probeOutput |
        ForEach-Object { [string]$_ } |
        Where-Object { $_.TrimStart().StartsWith("{") }
)
if ($probeExitCode -ne 0 -or $probeJsonLines.Count -eq 0) {
    throw "Packaged runtime import probe failed with exit code $probeExitCode. $($probeOutput -join ' ')"
}
$probe = $probeJsonLines[-1] | ConvertFrom-Json
if ($probe.schema -ne 1 -or $probe.runtime_flavor -ne $ExpectedRuntimeFlavor) {
    throw "Packaged runtime probe returned an unexpected schema or runtime flavor."
}
foreach ($componentId in $RequiredPythonIds) {
    $matches = @($probe.components | Where-Object { $_.id -eq $componentId })
    if ($matches.Count -ne 1 -or $matches[0].importable -ne $true) {
        throw "Packaged runtime could not import '$componentId'."
    }
    $manifestComponent = Get-ManifestComponent $Manifest $componentId
    if ($matches[0].version -ne $manifestComponent.version) {
        throw "Packaged '$componentId' version '$($matches[0].version)' differs from manifest '$($manifestComponent.version)'."
    }
}

$help = & $ResolvedExecutable --help 2>&1
if ($LASTEXITCODE -ne 0 -or ($help -join "`n") -notmatch "evidence-first") {
    throw "The packaged sidecar did not provide a valid --help response."
}

if ($SkipHealthSmoke) {
    Write-Host "Packaged runtime imports, tools, manifest, and --help checks passed (runtime=$ExpectedRuntimeFlavor)." -ForegroundColor Green
    return
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
    Write-Host "Packaged backend runtime imports, bundled tools, --help, and loopback health smoke passed (runtime=$ExpectedRuntimeFlavor)." -ForegroundColor Green
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
