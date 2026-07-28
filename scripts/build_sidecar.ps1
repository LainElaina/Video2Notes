[CmdletBinding()]
param(
    [switch]$SkipSmoke,
    [string]$FfmpegDirectory = "",
    [string]$FfmpegLicensePath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DesktopTauriRoot = Join-Path $RepoRoot "apps\desktop\src-tauri"
$ResourceRoot = Join-Path $DesktopTauriRoot "resources\backend"
$ToolRoot = Join-Path $ResourceRoot "tools"
$BuildRoot = Join-Path $RepoRoot "artifacts\build\sidecar"
$DistRoot = Join-Path $BuildRoot "dist"
$SpecRoot = Join-Path $BuildRoot "spec"
$WorkRoot = Join-Path $BuildRoot "work"
$SidecarPath = Join-Path $ResourceRoot "video2notes.exe"

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
$FfmpegSource = Get-ToolDirectory $FfmpegDirectory
$FfmpegLicenseSource = Get-FfmpegLicenseFile $FfmpegSource $FfmpegLicensePath

# The destination is build output, ignored by Git. Model directories, cookies,
# videos, and user data are never copied into it or passed to PyInstaller.
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
foreach ($path in @($DistRoot, $WorkRoot, $SpecRoot)) {
    $safePath = Assert-PathInsideRepository $path
    if (Test-Path -LiteralPath $safePath) {
        Remove-Item -LiteralPath $safePath -Recurse -Force
    }
}

$PyInstallerArguments = @(
    "-m", "PyInstaller",
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
    "--collect-all", "uvicorn",
    "--collect-all", "keyring",
    "--copy-metadata", "yt-dlp",
    "--exclude-module", "paddle",
    "--exclude-module", "paddleocr",
    "--exclude-module", "faster_whisper",
    "--exclude-module", "ctranslate2",
    "--exclude-module", "torch",
    (Join-Path $PSScriptRoot "sidecar_entry.py")
)
& $VenvPython @PyInstallerArguments
Assert-LastExitCode "Building the PyInstaller backend sidecar"

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
Get-ChildItem -LiteralPath $BuiltSidecarDirectory -Force | Copy-Item -Destination $ResourceRoot -Recurse -Force
Copy-Item -LiteralPath (Join-Path $FfmpegSource "ffmpeg.exe") -Destination (Join-Path $ToolRoot "ffmpeg.exe") -Force
Copy-Item -LiteralPath (Join-Path $FfmpegSource "ffprobe.exe") -Destination (Join-Path $ToolRoot "ffprobe.exe") -Force
Copy-Item -LiteralPath $FfmpegLicenseSource -Destination (Join-Path $ToolRoot "FFMPEG_LICENSE.txt") -Force

$FfmpegVersionOutput = (& (Join-Path $FfmpegSource "ffmpeg.exe") -version 2>&1 | Out-String).Trim()
Assert-LastExitCode "Reading bundled FFmpeg build information"
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

$AllowedTopLevelEntries = @("_internal", "tools", "video2notes.exe")
$UnexpectedTopLevelEntries = @(
    Get-ChildItem -LiteralPath $ResourceRoot -Force |
        Where-Object { $_.Name -notin $AllowedTopLevelEntries }
)
if ($UnexpectedTopLevelEntries.Count -gt 0) {
    throw "The backend resource output contains unexpected top-level entries: $($UnexpectedTopLevelEntries.Name -join ', ')"
}

$ForbiddenFiles = Get-ChildItem -LiteralPath $ResourceRoot -Recurse -File | Where-Object {
    $_.Extension -in @(".mp4", ".mkv", ".mov", ".webm", ".avi", ".cookies", ".sqlite", ".sqlite3", ".safetensors", ".gguf", ".pt", ".onnx") -or
    $_.FullName -match "(?i)[\\/](faster[_-]?whisper|paddle(?:ocr|paddle)?|huggingface|modelscope|torch)[\\/]"
}
if ($ForbiddenFiles) {
    throw "The backend resource output contains prohibited user/model data: $($ForbiddenFiles.FullName -join ', ')"
}

$Manifest = [ordered]@{
    schema = 1
    target_triple = (& rustc --print host-tuple).Trim()
    pyinstaller_version = (& $VenvPython -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
    files = @(
        Get-ChildItem -LiteralPath $ResourceRoot -Recurse -File |
            Where-Object { $_.Name -ne "manifest.json" } |
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
$Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ResourceRoot "manifest.json") -Encoding utf8

if (-not $SkipSmoke) {
    & (Join-Path $PSScriptRoot "test_sidecar.ps1") -Executable $SidecarPath
    Assert-LastExitCode "Running the packaged backend health smoke"
}

$Bytes = (Get-ChildItem -LiteralPath $ResourceRoot -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("Backend sidecar is ready: {0} ({1:N1} MiB resource total)" -f $SidecarPath, ($Bytes / 1MB)) -ForegroundColor Green
