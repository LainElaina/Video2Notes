[CmdletBinding()]
param(
    [switch]$SkipSmoke,
    # Development escape hatch only. Release and portable builds default to the
    # complete local ASR/OCR runtime.
    [switch]$CoreOnly,
    [string]$FfmpegDirectory = "",
    [string]$FfmpegLicensePath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "packaging_common.ps1")
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DesktopTauriRoot = Join-Path $RepoRoot "apps\desktop\src-tauri"
$ResourceRoot = Join-Path $DesktopTauriRoot "resources\backend"
$ToolRoot = Join-Path $ResourceRoot "tools"
$BuildRoot = Join-Path $RepoRoot "artifacts\build\sidecar"
$DistRoot = Join-Path $BuildRoot "dist"
$SpecRoot = Join-Path $BuildRoot "spec"
$WorkRoot = Join-Path $BuildRoot "work"
$SidecarPath = Join-Path $ResourceRoot "video2notes.exe"
$BackendManifestPath = Join-Path $ResourceRoot "manifest.json"
$RuntimeFlavor = if ($CoreOnly) { "core-only" } else { "full" }
$SourceFingerprintBeforeBuild = Get-Video2NotesSidecarSourceFingerprint $RepoRoot
$PythonComponentSpecs = @(
    [ordered]@{ id = "yt-dlp"; distribution = "yt-dlp"; module = "yt_dlp" },
    [ordered]@{ id = "psutil"; distribution = "psutil"; module = "psutil" },
    [ordered]@{ id = "faster-whisper"; distribution = "faster-whisper"; module = "faster_whisper" },
    [ordered]@{ id = "ctranslate2"; distribution = "ctranslate2"; module = "ctranslate2" },
    [ordered]@{ id = "huggingface-hub"; distribution = "huggingface-hub"; module = "huggingface_hub" },
    [ordered]@{ id = "paddleocr"; distribution = "paddleocr"; module = "paddleocr" },
    [ordered]@{ id = "paddlepaddle"; distribution = "paddlepaddle"; module = "paddle" },
    [ordered]@{ id = "nvidia-cublas-cu12"; distribution = "nvidia-cublas-cu12"; module = "nvidia.cublas" },
    [ordered]@{ id = "nvidia-cuda-nvrtc-cu12"; distribution = "nvidia-cuda-nvrtc-cu12"; module = "nvidia.cuda_nvrtc" },
    [ordered]@{ id = "nvidia-cudnn-cu12"; distribution = "nvidia-cudnn-cu12"; module = "nvidia.cudnn" }
)
$IncludedPythonComponentIds = @("yt-dlp", "psutil")
if (-not $CoreOnly) {
    $IncludedPythonComponentIds += @(
        "faster-whisper", "ctranslate2", "huggingface-hub", "paddleocr", "paddlepaddle",
        "nvidia-cublas-cu12", "nvidia-cuda-nvrtc-cu12", "nvidia-cudnn-cu12"
    )
}

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Require-File {
    param([string]$Path, [string]$Purpose)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Purpose was not found at '$Path'."
    }
}

function Assert-PathInsideRepository {
    param([string]$Path)
    $resolvedRepository = [IO.Path]::GetFullPath($RepoRoot).TrimEnd("\") + "\"
    $resolvedTarget = [IO.Path]::GetFullPath($Path)
    if (-not $resolvedTarget.StartsWith($resolvedRepository, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing a recursive build cleanup outside the repository: '$resolvedTarget'."
    }
    return $resolvedTarget
}

function Get-Sha256 {
    param([string]$Path)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try {
        $bytes = $algorithm.ComputeHash($stream)
        return ([BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
    }
    finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

function Get-PythonRuntimeInventory {
    param(
        [string]$Python,
        [object[]]$ComponentSpecs,
        [string[]]$RequiredComponentIds
    )

    $inventoryScript = @'
import importlib.metadata
import importlib.util
import json
import os

specs = json.loads(os.environ["VIDEO2NOTES_BUILD_COMPONENT_SPECS"])
inventory = []
for spec in specs:
    try:
        package_version = importlib.metadata.version(spec["distribution"])
        installed = True
    except importlib.metadata.PackageNotFoundError:
        package_version = None
        installed = False
    try:
        module_available = importlib.util.find_spec(spec["module"]) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        module_available = False
    inventory.append({
        **spec,
        "version": package_version,
        "installed": installed,
        "module_available": module_available,
    })
print(json.dumps(inventory, separators=(",", ":")))
'@
    $specsJson = ConvertTo-Json @($ComponentSpecs) -Compress
    $previousSpecs = $env:VIDEO2NOTES_BUILD_COMPONENT_SPECS
    $hadPreviousSpecs = Test-Path Env:VIDEO2NOTES_BUILD_COMPONENT_SPECS
    try {
        $env:VIDEO2NOTES_BUILD_COMPONENT_SPECS = $specsJson
        # Read the script from stdin.  Passing multiline Python through `-c`
        # is not stable after multiple Windows CreateProcess quoting layers.
        $inventoryJson = ($inventoryScript | & $Python - | Out-String).Trim()
        Assert-LastExitCode "Inspecting the installed Python runtime packages"
    }
    finally {
        if ($hadPreviousSpecs) { $env:VIDEO2NOTES_BUILD_COMPONENT_SPECS = $previousSpecs }
        else { Remove-Item Env:VIDEO2NOTES_BUILD_COMPONENT_SPECS -ErrorAction SilentlyContinue }
    }
    # Windows PowerShell 5.1 can preserve a JSON array as one nested Object[]
    # when it crosses a function return boundary.  Re-enumerate it explicitly.
    $parsedInventory = $inventoryJson | ConvertFrom-Json
    $inventory = @($parsedInventory | ForEach-Object { $_ })
    $missing = @(
        $inventory | Where-Object {
            $_.id -in $RequiredComponentIds -and
            (-not [bool]$_.installed -or -not [bool]$_.module_available)
        }
    )
    if ($missing.Count -gt 0) {
        $details = $missing | ForEach-Object { "$($_.id) ($($_.module))" }
        throw "Runtime flavor '$RuntimeFlavor' requires installed packages missing from .venv: $($details -join ', '). Run .\scripts\bootstrap.ps1 -WithAsr -WithOcr before a full release build, or pass -CoreOnly only for fast development iteration."
    }
    return $inventory
}

function Get-PaddleMetadataDistributions {
    param([string]$Python)

    # Mirror PaddleOCR's official PyInstaller packaging guidance:
    # https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/others/packaging.html
    # PaddleX checks installed dependency metadata at runtime, so copy metadata for
    # every installed dependency it recognizes instead of guessing a subset.
    $metadataScript = @'
import importlib.metadata
import json
import paddlex

installed = {dist.metadata["Name"] for dist in importlib.metadata.distributions()}
known = set(paddlex.utils.deps.BASE_DEP_SPECS)
print(json.dumps(sorted(installed & known, key=str.casefold), separators=(",", ":")))
'@
    $metadataJson = ($metadataScript | & $Python - | Out-String).Trim()
    Assert-LastExitCode "Inspecting installed PaddleX dependency metadata"
    $parsedMetadata = $metadataJson | ConvertFrom-Json
    return @($parsedMetadata | ForEach-Object { [string]$_ })
}

function Invoke-FrozenRuntimeProbe {
    param(
        [string]$Executable,
        [string]$ExpectedRuntimeFlavor
    )

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
            -FilePath $Executable `
            -WorkingDirectory (Split-Path -Parent $Executable) `
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

    $jsonLines = @(
        $probeOutput |
            ForEach-Object { [string]$_ } |
            Where-Object { $_.TrimStart().StartsWith("{") }
    )
    if ($probeExitCode -ne 0 -or $jsonLines.Count -eq 0) {
        throw "The frozen runtime import probe failed with exit code $probeExitCode. $($probeOutput -join ' ')"
    }
    $payload = $jsonLines[-1] | ConvertFrom-Json
    if ($payload.runtime_flavor -ne $ExpectedRuntimeFlavor) {
        throw "The frozen runtime reported '$($payload.runtime_flavor)' instead of '$ExpectedRuntimeFlavor'."
    }
    $failed = @($payload.components | Where-Object { -not [bool]$_.importable })
    if ($failed.Count -gt 0) {
        $details = $failed | ForEach-Object { "$($_.id): $($_.error)" }
        throw "The frozen runtime could not import required components: $($details -join '; ')"
    }
    return $payload
}

function Get-ToolDirectory {
    param([string]$RequestedDirectory)
    if ($RequestedDirectory) {
        $candidate = [IO.Path]::GetFullPath($RequestedDirectory)
        Require-File (Join-Path $candidate "ffmpeg.exe") "ffmpeg.exe"
        Require-File (Join-Path $candidate "ffprobe.exe") "ffprobe.exe"
        return $candidate
    }

    $ffmpeg = Get-Command "ffmpeg" -ErrorAction SilentlyContinue
    $ffprobe = Get-Command "ffprobe" -ErrorAction SilentlyContinue
    if ($null -eq $ffmpeg -or $null -eq $ffprobe) {
        throw "FFmpeg and ffprobe are required to create a self-contained sidecar. Install them, put both beside each other, or pass -FfmpegDirectory."
    }
    $directory = Split-Path -Parent $ffmpeg.Source
    if ((Split-Path -Parent $ffprobe.Source) -ne $directory) {
        throw "ffmpeg and ffprobe must be in the same directory. Pass -FfmpegDirectory containing both executable files."
    }
    return $directory
}

function Get-FfmpegLicenseFile {
    param([string]$ToolDirectory, [string]$RequestedPath)
    if ($RequestedPath) {
        $candidate = [IO.Path]::GetFullPath($RequestedPath)
        Require-File $candidate "FFmpeg license"
        return $candidate
    }

    $parent = Split-Path -Parent $ToolDirectory
    $candidates = @(
        (Join-Path $ToolDirectory "LICENSE"),
        (Join-Path $ToolDirectory "COPYING.GPLv3"),
        (Join-Path $parent "LICENSE"),
        (Join-Path $parent "COPYING.GPLv3")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    throw "The FFmpeg distribution license was not found beside '$ToolDirectory'. Pass -FfmpegLicensePath so the installer includes it."
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Python environment is missing. Run .\scripts\bootstrap.ps1 first."
}
Require-File (Join-Path $DesktopTauriRoot "tauri.conf.json") "Tauri configuration"
& $VenvPython -c "import PyInstaller"
Assert-LastExitCode "Checking the pinned PyInstaller build dependency"
$PythonInventory = @(
    Get-PythonRuntimeInventory $VenvPython $PythonComponentSpecs $IncludedPythonComponentIds
)
$PaddleMetadataDistributions = @()
if (-not $CoreOnly) {
    $PaddleMetadataDistributions = @(Get-PaddleMetadataDistributions $VenvPython)
}
$FfmpegSource = Get-ToolDirectory $FfmpegDirectory
$FfmpegLicenseSource = Get-FfmpegLicenseFile $FfmpegSource $FfmpegLicensePath

# The destination is build output, ignored by Git. Model directories, cookies,
# videos, and user data are never copied into it or passed to PyInstaller.
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$ProbeComponentSpecsJson = ConvertTo-Json @(
    $PythonComponentSpecs | Where-Object { $_.id -in $IncludedPythonComponentIds }
) -Compress
$RuntimeProbeHookPath = Join-Path $BuildRoot "pyinstaller_runtime_probe_hook.py"
$RuntimeProbeHook = @"
from __future__ import annotations

import importlib
import importlib.metadata
import json
import os

_RUNTIME_FLAVOR = "$RuntimeFlavor"
_COMPONENTS = json.loads(r'''$ProbeComponentSpecsJson''')

if os.environ.get("VIDEO2NOTES_RUNTIME_PROBE") == "1":
    results = []
    failed = False
    for component in _COMPONENTS:
        try:
            module = importlib.import_module(component["module"])
            if (
                component["id"] == "huggingface-hub"
                and not callable(getattr(module, "snapshot_download", None))
            ):
                raise AttributeError("snapshot_download is unavailable")
            package_version = importlib.metadata.version(component["distribution"])
            results.append({**component, "version": package_version, "importable": True})
        except BaseException as error:
            failed = True
            results.append({
                **component,
                "version": None,
                "importable": False,
                "error": f"{type(error).__name__}: {error}",
            })
    print(json.dumps({
        "schema": 1,
        "runtime_flavor": _RUNTIME_FLAVOR,
        "components": results,
    }, separators=(",", ":")), flush=True)
    os._exit(1 if failed else 0)
"@
[IO.File]::WriteAllText(
    $RuntimeProbeHookPath,
    $RuntimeProbeHook,
    [Text.UTF8Encoding]::new($false)
)
foreach ($path in @($DistRoot, $WorkRoot, $SpecRoot)) {
    $safePath = Assert-PathInsideRepository $path
    if (Test-Path -LiteralPath $safePath) {
        Remove-Item -LiteralPath $safePath -Recurse -Force
    }
}

$PyInstallerArguments = @(
    # --onedir keeps the service as the process Tauri starts and stops. A
    # one-file bundle forks an unpacked child on Windows, which would make
    # Tauri observe the parent exiting while the API child remains orphaned.
    "--noconfirm", "--clean", "--onedir", "--console",
    "--name", "video2notes",
    "--distpath", $DistRoot,
    "--workpath", $WorkRoot,
    "--specpath", $SpecRoot,
    "--paths", (Join-Path $RepoRoot "src"),
    "--runtime-hook", (Join-Path $PSScriptRoot "pyinstaller_runtime_hook.py"),
    "--runtime-hook", $RuntimeProbeHookPath,
    "--collect-all", "uvicorn",
    "--collect-all", "keyring",
    "--collect-all", "yt_dlp",
    "--copy-metadata", "yt-dlp",
    "--collect-all", "psutil",
    "--copy-metadata", "psutil"
)
if ($CoreOnly) {
    $PyInstallerArguments += @(
        "--exclude-module", "paddle",
        "--exclude-module", "paddleocr",
        "--exclude-module", "paddlex",
        "--exclude-module", "faster_whisper",
        "--exclude-module", "ctranslate2",
        "--exclude-module", "huggingface_hub",
        "--exclude-module", "nvidia",
        "--exclude-module", "torch"
    )
}
else {
    # PaddleOCR's official PyInstaller guidance requires Paddle binaries,
    # PaddleX data, and dependency metadata. Paddle itself must not use
    # collect-all on Windows: enumerating optional JIT/TensorRT submodules can
    # terminate PyInstaller's isolated analysis process. A hidden root import
    # plus all native binaries follows the supported packaging path.
    $PyInstallerArguments += @(
        "--collect-all", "faster_whisper",
        "--collect-all", "ctranslate2",
        "--hidden-import", "nvidia.cublas",
        "--hidden-import", "nvidia.cuda_nvrtc",
        "--hidden-import", "nvidia.cudnn",
        "--collect-binaries", "nvidia.cublas",
        "--collect-binaries", "nvidia.cuda_nvrtc",
        "--collect-binaries", "nvidia.cudnn",
        "--collect-all", "huggingface_hub",
        "--collect-all", "paddleocr",
        "--hidden-import", "paddle",
        "--collect-data", "paddlex",
        "--collect-binaries", "paddle",
        "--copy-metadata", "paddleocr",
        "--copy-metadata", "paddlex",
        "--copy-metadata", "faster-whisper",
        "--copy-metadata", "ctranslate2",
        "--copy-metadata", "nvidia-cublas-cu12",
        "--copy-metadata", "nvidia-cuda-nvrtc-cu12",
        "--copy-metadata", "nvidia-cudnn-cu12",
        "--copy-metadata", "huggingface-hub",
        "--copy-metadata", "paddlepaddle"
    )
    foreach ($distribution in $PaddleMetadataDistributions) {
        $PyInstallerArguments += @("--copy-metadata", $distribution)
    }
}
$PyInstallerArguments += (Join-Path $PSScriptRoot "sidecar_entry.py")
$pyInstallerDriver = @'
import json
import os

from PyInstaller.__main__ import run

arguments = json.loads(os.environ["VIDEO2NOTES_BUILD_PYINSTALLER_ARGS"])
if not isinstance(arguments, list) or not all(
    isinstance(argument, str) and argument for argument in arguments
):
    raise TypeError("PyInstaller arguments must be a non-empty string list")
run(arguments)
'@
$pyInstallerArgumentsJson = ConvertTo-Json @($PyInstallerArguments) -Compress
$previousPyInstallerArguments = $env:VIDEO2NOTES_BUILD_PYINSTALLER_ARGS
$hadPreviousPyInstallerArguments = Test-Path Env:VIDEO2NOTES_BUILD_PYINSTALLER_ARGS
try {
    # Preserve the argument array as JSON.  PowerShell 5's native-command
    # quoting can otherwise collapse repeated options after nested launches.
    $env:VIDEO2NOTES_BUILD_PYINSTALLER_ARGS = $pyInstallerArgumentsJson
    $pyInstallerDriver | & $VenvPython -
    Assert-LastExitCode "Building the PyInstaller backend sidecar"
}
finally {
    if ($hadPreviousPyInstallerArguments) {
        $env:VIDEO2NOTES_BUILD_PYINSTALLER_ARGS = $previousPyInstallerArguments
    }
    else {
        Remove-Item Env:VIDEO2NOTES_BUILD_PYINSTALLER_ARGS -ErrorAction SilentlyContinue
    }
}

$BuiltSidecarDirectory = Join-Path $DistRoot "video2notes"
$BuiltSidecar = Join-Path $BuiltSidecarDirectory "video2notes.exe"
Require-File $BuiltSidecar "PyInstaller output"
$safeResourceRoot = Assert-PathInsideRepository $ResourceRoot
if (Test-Path -LiteralPath $safeResourceRoot) {
    # This exact ignored output directory is recreated so a previous manifest,
    # tool, log, or interrupted copy can never leak into the next installer.
    Remove-Item -LiteralPath $safeResourceRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $ResourceRoot, $ToolRoot | Out-Null
$PlaceholderPath = Join-Path $ResourceRoot ".gitkeep"
[IO.File]::WriteAllText(
    $PlaceholderPath,
    "This directory is populated by scripts/build_sidecar.ps1.`n",
    [Text.UTF8Encoding]::new($false)
)
Get-ChildItem -LiteralPath $BuiltSidecarDirectory -Force | Copy-Item -Destination $ResourceRoot -Recurse -Force
if (-not $CoreOnly) {
    foreach ($cudaRuntimeFile in @(
        "_internal\nvidia\cublas\bin\cublas64_12.dll",
        "_internal\nvidia\cublas\bin\cublasLt64_12.dll",
        "_internal\nvidia\cudnn\bin\cudnn64_9.dll",
        "_internal\nvidia\cuda_nvrtc\bin\nvrtc64_120_0.dll"
    )) {
        Require-File (Join-Path $ResourceRoot $cudaRuntimeFile) "Bundled NVIDIA CUDA runtime DLL"
    }
    foreach ($metadataPrefix in @(
        "nvidia_cublas_cu12", "nvidia_cuda_nvrtc_cu12", "nvidia_cudnn_cu12"
    )) {
        $metadataDirectories = @(
            Get-ChildItem -LiteralPath (Join-Path $ResourceRoot "_internal") -Directory |
                Where-Object { $_.Name -like "$metadataPrefix-*.dist-info" }
        )
        if ($metadataDirectories.Count -ne 1) {
            throw "Expected exactly one bundled '$metadataPrefix' metadata directory, found $($metadataDirectories.Count)."
        }
        Require-File `
            (Join-Path $metadataDirectories[0].FullName "licenses\License.txt") `
            "Bundled NVIDIA runtime license"
    }
}
Copy-Item -LiteralPath (Join-Path $FfmpegSource "ffmpeg.exe") -Destination (Join-Path $ToolRoot "ffmpeg.exe") -Force
Copy-Item -LiteralPath (Join-Path $FfmpegSource "ffprobe.exe") -Destination (Join-Path $ToolRoot "ffprobe.exe") -Force
Copy-Item -LiteralPath $FfmpegLicenseSource -Destination (Join-Path $ToolRoot "FFMPEG_LICENSE.txt") -Force

$FfmpegVersionOutput = (& (Join-Path $FfmpegSource "ffmpeg.exe") -version 2>&1 | Out-String).Trim()
Assert-LastExitCode "Reading bundled FFmpeg build information"
$FfprobeVersionOutput = (& (Join-Path $FfmpegSource "ffprobe.exe") -version 2>&1 | Out-String).Trim()
Assert-LastExitCode "Reading bundled ffprobe build information"
$FfmpegSourceReference = "https://ffmpeg.org/download.html#get-sources"
if ($FfmpegVersionOutput -match "git-([0-9a-fA-F]{7,40})") {
    $FfmpegSourceReference = "https://github.com/FFmpeg/FFmpeg/commit/$($Matches[1])"
}
@"
Video2Notes bundles the ffmpeg.exe and ffprobe.exe supplied by the release build host.
The adjacent FFMPEG_LICENSE.txt is copied from that same binary distribution.
Corresponding upstream source reference: $FfmpegSourceReference
Windows build provider and archive index: https://www.gyan.dev/ffmpeg/builds/

Exact bundled build identification:
$FfmpegVersionOutput
"@ | Set-Content -LiteralPath (Join-Path $ToolRoot "FFMPEG_BUILD_INFO.txt") -Encoding utf8

$AllowedToolEntries = @(
    "ffmpeg.exe",
    "ffprobe.exe",
    "FFMPEG_LICENSE.txt",
    "FFMPEG_BUILD_INFO.txt"
)
$UnexpectedToolEntries = @(
    Get-ChildItem -LiteralPath $ToolRoot -Force |
        Where-Object { $_.Name -notin $AllowedToolEntries }
)
if ($UnexpectedToolEntries.Count -gt 0) {
    throw "The backend tool output contains unexpected entries: $($UnexpectedToolEntries.Name -join ', ')"
}

$AllowedTopLevelEntries = @(".gitkeep", "_internal", "tools", "video2notes.exe")
$UnexpectedTopLevelEntries = @(
    Get-ChildItem -LiteralPath $ResourceRoot -Force |
        Where-Object { $_.Name -notin $AllowedTopLevelEntries }
)
if ($UnexpectedTopLevelEntries.Count -gt 0) {
    throw "The backend resource output contains unexpected top-level entries: $($UnexpectedTopLevelEntries.Name -join ', ')"
}

$AllowedPackagedRuntimeAssets = @(
    # faster-whisper's small upstream Silero VAD asset is required by the
    # project's default vad_filter path. It is not a user-selected ASR model.
    "_internal/faster_whisper/assets/silero_vad_v6.onnx"
)
$ForbiddenFiles = Get-ChildItem -LiteralPath $ResourceRoot -Recurse -File | Where-Object {
    $relative = $_.FullName.Substring($ResourceRoot.Length).TrimStart("\") -replace "\\", "/"
    if ($relative -in $AllowedPackagedRuntimeAssets) { return $false }
    $extension = $_.Extension.ToLowerInvariant()
    $relative -match "(?i)(^|/)\.cache(/|$)" -or
    $_.Name -match "(?i)(^|\.)(cookies?\.txt|keyring\.json)$" -or
    $_.Name -match "(?i)^\.env(?:\.|$)" -or
    $_.Name -match "(?i)^(model|pytorch_model|adapter_model)\.bin$" -or
    $extension -in @(
        ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv",
        ".cookies", ".sqlite", ".sqlite3", ".db", ".db-shm", ".db-wal",
        ".safetensors", ".gguf", ".pt", ".pth", ".onnx", ".ckpt",
        ".pdmodel", ".pdiparams", ".pdparams", ".pdopt"
    )
}
if ($ForbiddenFiles) {
    throw "The backend resource output contains prohibited user/model data: $($ForbiddenFiles.FullName -join ', ')"
}

$FrozenRuntimeProbe = Invoke-FrozenRuntimeProbe $SidecarPath $RuntimeFlavor
$RuntimeComponents = @(
    foreach ($component in $PythonInventory) {
        $included = $component.id -in $IncludedPythonComponentIds
        $probeComponent = @($FrozenRuntimeProbe.components | Where-Object { $_.id -eq $component.id })
        if ($included -and $probeComponent.Count -ne 1) {
            throw "The frozen runtime probe did not report exactly one '$($component.id)' component."
        }
        if ($included -and $probeComponent[0].version -ne $component.version) {
            throw "The frozen '$($component.id)' version '$($probeComponent[0].version)' differs from build .venv version '$($component.version)'."
        }
        [ordered]@{
            id = [string]$component.id
            kind = "python-package"
            distribution = [string]$component.distribution
            module = [string]$component.module
            version = if ($included) { [string]$component.version } else { $null }
            included = $included
            import_verified = $included
            status = if ($included) { "bundled" } else { "excluded-core-only" }
        }
    }
    [ordered]@{
        id = "ffmpeg"
        kind = "executable"
        version = [string](($FfmpegVersionOutput -split "`r?`n")[0]).Trim()
        included = $true
        executable_verified = $true
        status = "bundled"
    }
    [ordered]@{
        id = "ffprobe"
        kind = "executable"
        version = [string](($FfprobeVersionOutput -split "`r?`n")[0]).Trim()
        included = $true
        executable_verified = $true
        status = "bundled"
    }
)

$SourceFingerprintAfterBuild = Get-Video2NotesSidecarSourceFingerprint $RepoRoot
if ($SourceFingerprintAfterBuild -ne $SourceFingerprintBeforeBuild) {
    throw "Python or sidecar packaging sources changed while PyInstaller was running. Rebuild so the frozen backend has one coherent source fingerprint."
}

$Manifest = [ordered]@{
    schema = 2
    target_triple = (& rustc --print host-tuple).Trim()
    pyinstaller_version = (& $VenvPython -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
    runtime_flavor = $RuntimeFlavor
    source_fingerprint_schema = 1
    source_fingerprint_sha256 = $SourceFingerprintAfterBuild
    user_model_weights_included = $false
    packaged_runtime_assets = if ($CoreOnly) {
        @()
    }
    else {
        @("faster-whisper/silero-vad-v6", "nvidia/cuda12-cublas-cudnn-nvrtc")
    }
    components = $RuntimeComponents
    files = @(
        Get-ChildItem -LiteralPath $ResourceRoot -Recurse -File |
            Where-Object { $_.FullName -ne $BackendManifestPath } |
            Sort-Object FullName |
            ForEach-Object {
                [ordered]@{
                    relative_path = $_.FullName.Substring($ResourceRoot.Length).TrimStart("\\") -replace "\\", "/"
                    bytes = $_.Length
                    sha256 = Get-Sha256 $_.FullName
                }
            }
    )
}
$Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $BackendManifestPath -Encoding utf8

if (-not $SkipSmoke) {
    & (Join-Path $PSScriptRoot "test_sidecar.ps1") -Executable $SidecarPath -CoreOnly:$CoreOnly
    Assert-LastExitCode "Running the packaged backend health smoke"
}

$Bytes = (Get-ChildItem -LiteralPath $ResourceRoot -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("Backend sidecar is ready: {0} ({1:N1} MiB resource total; runtime={2})" -f $SidecarPath, ($Bytes / 1MB), $RuntimeFlavor) -ForegroundColor Green
