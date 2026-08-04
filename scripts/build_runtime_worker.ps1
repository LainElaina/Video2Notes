[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("cpu", "nvidia_asr", "full_gpu")]
    [string]$Profile,
    [string]$PythonPath = "",
    [string]$OutputDirectory = "",
    [switch]$SkipHelpSmoke
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "packaging_common.ps1")

$ProfileDefinitions = @{
    cpu = [ordered]@{
        recipe = "local-inference-cpu-win-x64.json"
        paddle_distribution = "paddlepaddle"
        environment = "cpu-paddle"
        nvidia = @()
    }
    nvidia_asr = [ordered]@{
        recipe = "local-inference-nvidia-asr-cu129-win-x64.json"
        paddle_distribution = "paddlepaddle"
        environment = "cpu-paddle"
        nvidia = @(
            "nvidia-cublas-cu12",
            "nvidia-cuda-nvrtc-cu12",
            "nvidia-cudnn-cu12",
            "nvidia-nvjitlink-cu12"
        )
    }
    full_gpu = [ordered]@{
        recipe = "local-inference-nvidia-full-cu129-win-x64.json"
        paddle_distribution = "paddlepaddle-gpu"
        environment = "full-gpu"
        nvidia = @(
            "nvidia-cublas-cu12",
            "nvidia-cuda-nvrtc-cu12",
            "nvidia-cuda-runtime-cu12",
            "nvidia-cudnn-cu12",
            "nvidia-cufft-cu12",
            "nvidia-curand-cu12",
            "nvidia-cusolver-cu12",
            "nvidia-cusparse-cu12",
            "nvidia-nvjitlink-cu12"
        )
    }
}
$Definition = $ProfileDefinitions[$Profile]
$RecipePath = Join-Path $RepoRoot ("packaging\runtime-packs\recipes\" + $Definition.recipe)

if (-not $PythonPath) {
    if ($Profile -eq "full_gpu") {
        $PythonPath = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    }
    else {
        $PythonPath = Join-Path $RepoRoot "artifacts\build\runtime-envs\cpu-paddle\Scripts\python.exe"
    }
}
$PythonPath = [IO.Path]::GetFullPath($PythonPath)
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Runtime worker build Python was not found at '$PythonPath'."
}

$BuildRoot = Join-Path $RepoRoot ("artifacts\build\runtime-worker-pyinstaller\" + $Profile)
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$SpecRoot = Join-Path $BuildRoot "spec"
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $RepoRoot ("artifacts\build\runtime-workers\" + $Profile)
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd("\")
$ArtifactsRoot = [IO.Path]::GetFullPath((Join-Path $RepoRoot "artifacts")).TrimEnd("\") + "\"
if (-not $OutputDirectory.StartsWith($ArtifactsRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Runtime worker output must stay below the repository artifacts directory."
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

function Remove-ManagedDirectory {
    param([string]$Path, [string]$RequiredPrefix)
    $resolved = [IO.Path]::GetFullPath($Path)
    $prefix = [IO.Path]::GetFullPath($RequiredPrefix).TrimEnd("\") + "\"
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to recursively remove an unmanaged runtime build path: '$resolved'."
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

function Write-JsonFile {
    param([string]$Path, [object]$Value)
    $json = $Value | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText($Path, "$json`n", [Text.UTF8Encoding]::new($false))
}

$NvidiaSpecs = @(
    [ordered]@{ distribution = "nvidia-cublas-cu12"; module = "nvidia.cublas"; directory = "cublas"; sentinel = "cublas64_12.dll" },
    [ordered]@{ distribution = "nvidia-cuda-nvrtc-cu12"; module = "nvidia.cuda_nvrtc"; directory = "cuda_nvrtc"; sentinel = "nvrtc64_120_0.dll" },
    [ordered]@{ distribution = "nvidia-cuda-runtime-cu12"; module = "nvidia.cuda_runtime"; directory = "cuda_runtime"; sentinel = "cudart64_12.dll" },
    [ordered]@{ distribution = "nvidia-cudnn-cu12"; module = "nvidia.cudnn"; directory = "cudnn"; sentinel = "cudnn64_9.dll" },
    [ordered]@{ distribution = "nvidia-cufft-cu12"; module = "nvidia.cufft"; directory = "cufft"; sentinel = "cufft64_11.dll" },
    [ordered]@{ distribution = "nvidia-curand-cu12"; module = "nvidia.curand"; directory = "curand"; sentinel = "curand64_10.dll" },
    [ordered]@{ distribution = "nvidia-cusolver-cu12"; module = "nvidia.cusolver"; directory = "cusolver"; sentinel = "cusolver64_11.dll" },
    [ordered]@{ distribution = "nvidia-cusparse-cu12"; module = "nvidia.cusparse"; directory = "cusparse"; sentinel = "cusparse64_12.dll" },
    [ordered]@{ distribution = "nvidia-nvjitlink-cu12"; module = "nvidia.nvjitlink"; directory = "nvjitlink"; sentinel = "nvJitLink_120_0.dll" }
)
$SelectedNvidia = @(
    $NvidiaSpecs | Where-Object { $_.distribution -in @($Definition.nvidia) }
)

Require-File $RecipePath "Runtime package recipe"
Require-File (Join-Path $PSScriptRoot "runtime_worker_entry.py") "Runtime worker entry point"
Require-File (Join-Path $PSScriptRoot "pyinstaller_runtime_hook.py") "Runtime worker hook"
Require-File (Join-Path $RepoRoot "THIRD_PARTY_NOTICES.md") "Third-party notices"

& $PythonPath -c "import PyInstaller; import video2notes"
Assert-LastExitCode "Checking the runtime worker build environment"

$InventoryScript = @'
import importlib.metadata
import importlib.util
import json
import os

required = json.loads(os.environ["VIDEO2NOTES_RUNTIME_WORKER_REQUIRED"])
payload = {}
for distribution, module in required.items():
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        version = None
    try:
        available = importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        available = False
    payload[distribution] = {"version": version, "module": module, "available": available}
for distribution in ("paddlepaddle", "paddlepaddle-gpu"):
    try:
        payload[distribution] = {
            "version": importlib.metadata.version(distribution),
            "module": "paddle",
            "available": importlib.util.find_spec("paddle") is not None,
        }
    except importlib.metadata.PackageNotFoundError:
        payload[distribution] = {"version": None, "module": "paddle", "available": False}
print(json.dumps(payload, separators=(",", ":")))
'@
$Required = [ordered]@{
    "faster-whisper" = "faster_whisper"
    "ctranslate2" = "ctranslate2"
    "paddleocr" = "paddleocr"
    "Pillow" = "PIL"
    "pydantic" = "pydantic"
}
foreach ($runtime in $SelectedNvidia) {
    $Required[[string]$runtime.distribution] = [string]$runtime.module
}
$previousRequired = $env:VIDEO2NOTES_RUNTIME_WORKER_REQUIRED
$hadPreviousRequired = Test-Path Env:VIDEO2NOTES_RUNTIME_WORKER_REQUIRED
try {
    $env:VIDEO2NOTES_RUNTIME_WORKER_REQUIRED = ConvertTo-Json $Required -Compress
    $inventoryJson = ($InventoryScript | & $PythonPath - | Out-String).Trim()
    Assert-LastExitCode "Inspecting the runtime worker build environment"
}
finally {
    if ($hadPreviousRequired) { $env:VIDEO2NOTES_RUNTIME_WORKER_REQUIRED = $previousRequired }
    else { Remove-Item Env:VIDEO2NOTES_RUNTIME_WORKER_REQUIRED -ErrorAction SilentlyContinue }
}
$Inventory = $inventoryJson | ConvertFrom-Json
foreach ($property in $Required.Keys) {
    $item = $Inventory.$property
    if (-not $item.version -or -not [bool]$item.available) {
        throw "Runtime worker profile '$Profile' is missing '$property' in '$PythonPath'."
    }
}
$ExpectedPaddle = [string]$Definition.paddle_distribution
$OtherPaddle = if ($ExpectedPaddle -eq "paddlepaddle") { "paddlepaddle-gpu" } else { "paddlepaddle" }
if (-not $Inventory.$ExpectedPaddle.version -or $Inventory.$OtherPaddle.version) {
    throw "Runtime worker profile '$Profile' requires only '$ExpectedPaddle'; found an incompatible Paddle environment."
}

$MetadataScript = @'
import importlib.metadata
import json
import paddlex

installed = {dist.metadata["Name"] for dist in importlib.metadata.distributions()}
known = set(paddlex.utils.deps.BASE_DEP_SPECS)
print(json.dumps(sorted(installed & known, key=str.casefold), separators=(",", ":")))
'@
$PaddleMetadataJson = ($MetadataScript | & $PythonPath - | Out-String).Trim()
Assert-LastExitCode "Inspecting PaddleX dependency metadata"
$PaddleMetadata = @($PaddleMetadataJson | ConvertFrom-Json | ForEach-Object { [string]$_ })

Remove-ManagedDirectory $BuildRoot (Join-Path $RepoRoot "artifacts\build")
Remove-ManagedDirectory $OutputDirectory (Join-Path $RepoRoot "artifacts")
New-Item -ItemType Directory -Force -Path $BuildRoot, $DistRoot, $WorkRoot, $SpecRoot | Out-Null

$PyInstallerArguments = @(
    "--noconfirm", "--clean", "--onedir", "--console",
    "--name", "runtime-worker",
    "--distpath", $DistRoot,
    "--workpath", $WorkRoot,
    "--specpath", $SpecRoot,
    "--paths", (Join-Path $RepoRoot "src"),
    "--runtime-hook", (Join-Path $PSScriptRoot "pyinstaller_runtime_hook.py"),
    "--collect-all", "faster_whisper",
    "--collect-all", "ctranslate2",
    "--collect-all", "paddleocr",
    "--hidden-import", "paddle",
    "--collect-data", "paddlex",
    "--collect-binaries", "paddle",
    "--copy-metadata", "paddleocr",
    "--copy-metadata", "paddlex",
    "--copy-metadata", "faster-whisper",
    "--copy-metadata", "ctranslate2",
    "--copy-metadata", $ExpectedPaddle,
    "--exclude-module", "torch"
)
if ($Profile -eq "cpu") {
    $PyInstallerArguments += @("--exclude-module", "nvidia")
}
foreach ($runtime in $SelectedNvidia) {
    $PyInstallerArguments += @(
        "--hidden-import", [string]$runtime.module,
        "--collect-binaries", [string]$runtime.module,
        "--copy-metadata", [string]$runtime.distribution
    )
}
foreach ($distribution in $PaddleMetadata) {
    $PyInstallerArguments += @("--copy-metadata", $distribution)
}
$PyInstallerArguments += (Join-Path $PSScriptRoot "runtime_worker_entry.py")

$Driver = @'
import json
import os
from PyInstaller.__main__ import run

arguments = json.loads(os.environ["VIDEO2NOTES_RUNTIME_WORKER_PYINSTALLER_ARGS"])
if not isinstance(arguments, list) or not all(isinstance(item, str) and item for item in arguments):
    raise TypeError("PyInstaller arguments must be a non-empty string list")
run(arguments)
'@
$previousArguments = $env:VIDEO2NOTES_RUNTIME_WORKER_PYINSTALLER_ARGS
$hadPreviousArguments = Test-Path Env:VIDEO2NOTES_RUNTIME_WORKER_PYINSTALLER_ARGS
try {
    $env:VIDEO2NOTES_RUNTIME_WORKER_PYINSTALLER_ARGS = ConvertTo-Json @($PyInstallerArguments) -Compress
    $Driver | & $PythonPath -
    Assert-LastExitCode "Building the isolated runtime worker"
}
finally {
    if ($hadPreviousArguments) { $env:VIDEO2NOTES_RUNTIME_WORKER_PYINSTALLER_ARGS = $previousArguments }
    else { Remove-Item Env:VIDEO2NOTES_RUNTIME_WORKER_PYINSTALLER_ARGS -ErrorAction SilentlyContinue }
}

$BuiltDirectory = Join-Path $DistRoot "runtime-worker"
$BuiltExecutable = Join-Path $BuiltDirectory "runtime-worker.exe"
Require-File $BuiltExecutable "Frozen runtime worker"
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
Get-ChildItem -LiteralPath $BuiltDirectory -Force | Copy-Item -Destination $OutputDirectory -Recurse -Force
$LicenseRoot = Join-Path $OutputDirectory "licenses"
New-Item -ItemType Directory -Path $LicenseRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $RepoRoot "THIRD_PARTY_NOTICES.md") -Destination (Join-Path $LicenseRoot "THIRD_PARTY_NOTICES.md")

foreach ($runtime in $SelectedNvidia) {
    $sentinel = Join-Path $OutputDirectory ("_internal\nvidia\{0}\bin\{1}" -f $runtime.directory, $runtime.sentinel)
    Require-File $sentinel "Bundled $($runtime.distribution) runtime DLL"
}
if ($Profile -ne "cpu") {
    Require-File (Join-Path $OutputDirectory "_internal\nvidia\cublas\bin\cublasLt64_12.dll") "Bundled cuBLAS LT runtime DLL"
}

$Recipe = Get-Content -LiteralPath $RecipePath -Raw | ConvertFrom-Json
$GitCommit = (& git -C $RepoRoot rev-parse HEAD).Trim()
Assert-LastExitCode "Reading the source commit"
$BuildInfo = [ordered]@{
    schema = 1
    product = "Video2Notes runtime worker"
    profile = $Profile
    package_id = [string]$Recipe.package_id
    package_version = [string]$Recipe.version
    target_triple = (& rustc --print host-tuple).Trim()
    commit = $GitCommit
    built_at_utc = [DateTime]::UtcNow.ToString("o")
    python = (& $PythonPath -c "import platform; print(platform.python_version())").Trim()
    pyinstaller = (& $PythonPath -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
    paddle_distribution = $ExpectedPaddle
    components = @(
        $Required.Keys | ForEach-Object {
            [ordered]@{ distribution = $_; version = [string]$Inventory.$_.version }
        }
    ) + @([ordered]@{ distribution = $ExpectedPaddle; version = [string]$Inventory.$ExpectedPaddle.version })
    user_model_weights_included = $false
}
Write-JsonFile (Join-Path $OutputDirectory "RUNTIME_BUILD_INFO.json") $BuildInfo

if (Test-Path -LiteralPath (Join-Path $OutputDirectory "runtime-package.json")) {
    throw "Runtime worker payload must not contain runtime-package.json before trusted packaging."
}
$AllowedRuntimeAssets = @("_internal/faster_whisper/assets/silero_vad_v6.onnx")
$ForbiddenFiles = @(
    Get-ChildItem -LiteralPath $OutputDirectory -Recurse -File | Where-Object {
        $relative = $_.FullName.Substring($OutputDirectory.Length).TrimStart("\") -replace "\\", "/"
        if ($relative -in $AllowedRuntimeAssets) { return $false }
        $extension = $_.Extension.ToLowerInvariant()
        $_.Name -match "(?i)(^|\.)(cookies?\.txt|keyring\.json)$" -or
        $_.Name -match "(?i)^\.env(?:\.|$)" -or
        $_.Name -match "(?i)^(model|pytorch_model|adapter_model)\.bin$" -or
        $extension -in @(
            ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv",
            ".cookies", ".sqlite", ".sqlite3", ".db", ".safetensors", ".gguf",
            ".pt", ".pth", ".ckpt", ".onnx", ".pdmodel", ".pdiparams", ".pdparams"
        )
    }
)
if ($ForbiddenFiles.Count -gt 0) {
    throw "Runtime worker output contains prohibited model, media, or credential files: $($ForbiddenFiles.FullName -join ', ')."
}

if (-not $SkipHelpSmoke) {
    & (Join-Path $OutputDirectory "runtime-worker.exe") --help | Out-Null
    Assert-LastExitCode "Starting the frozen runtime worker"
}
$Bytes = [long]((Get-ChildItem -LiteralPath $OutputDirectory -Recurse -File | Measure-Object Length -Sum).Sum)
Write-Host ("Runtime worker payload ready: {0} ({1:N1} MiB; profile={2})" -f $OutputDirectory, ($Bytes / 1MB), $Profile) -ForegroundColor Green
Write-Host ("Package it with: .\scripts\build_runtime_pack.ps1 -RecipePath '{0}' -PayloadRoot '{1}'" -f $RecipePath, $OutputDirectory)
