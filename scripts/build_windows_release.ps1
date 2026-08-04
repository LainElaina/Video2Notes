[CmdletBinding()]
param(
    # Installers intentionally contain only the small Core sidecar. Managed
    # CPU/GPU runtimes remain separate, upgradeable release assets.
    [ValidateSet("core")]
    [string]$ReleaseProfile = "core",
    [switch]$ReuseSidecar,
    [switch]$SkipSidecarSmoke,
    [string]$FfmpegDirectory = "",
    [string]$FfmpegLicensePath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "packaging_common.ps1")

$DesktopRoot = Join-Path $RepoRoot "apps\desktop"
$TauriRoot = Join-Path $DesktopRoot "src-tauri"
$TauriConfigPath = Join-Path $TauriRoot "tauri.conf.json"
$BackendRoot = Join-Path $TauriRoot "resources\backend"
$BackendExecutable = Join-Path $BackendRoot "video2notes.exe"
$BackendManifestPath = Join-Path $BackendRoot "manifest.json"
$BundleIds = @("nsis", "msi")

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

function Write-Utf8Json {
    param([string]$Path, [object]$Value)
    $json = $Value | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText($Path, "$json`n", [Text.UTF8Encoding]::new($false))
}

function Write-ChecksumSidecar {
    param([string]$Path, [string]$Sha256)
    $fileName = [IO.Path]::GetFileName($Path)
    [IO.File]::WriteAllText(
        "$Path.sha256",
        "$Sha256  $fileName`n",
        [Text.UTF8Encoding]::new($false)
    )
}

function Assert-PathInside {
    param([string]$Parent, [string]$Path, [string]$Purpose)
    $resolvedParent = [IO.Path]::GetFullPath($Parent).TrimEnd("\") + "\"
    $resolvedPath = [IO.Path]::GetFullPath($Path).TrimEnd("\")
    if (-not $resolvedPath.StartsWith($resolvedParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Purpose escaped its managed root: '$resolvedPath'."
    }
    return $resolvedPath
}

function Assert-InstallerSignature {
    param([string]$Path, [ValidateSet("nsis", "msi")][string]$Kind)
    Require-File $Path "$Kind installer"
    $stream = [IO.File]::OpenRead($Path)
    try {
        if ($stream.Length -le 8) {
            throw "$Kind installer is unexpectedly empty."
        }
        $header = New-Object byte[] 8
        $null = $stream.Read($header, 0, $header.Length)
    }
    finally {
        $stream.Dispose()
    }

    if ($Kind -eq "nsis" -and ($header[0] -ne 0x4D -or $header[1] -ne 0x5A)) {
        throw "NSIS output does not have a Windows PE header."
    }
    $oleHeader = @(0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1)
    if ($Kind -eq "msi") {
        for ($index = 0; $index -lt $oleHeader.Count; $index += 1) {
            if ($header[$index] -ne $oleHeader[$index]) {
                throw "MSI output does not have an OLE compound-file header."
            }
        }
    }
}

function Assert-CoreSidecar {
    param([string]$ExpectedReleaseProfile)

    Require-File $BackendExecutable "Frozen Core backend executable"
    Require-File (Join-Path $BackendRoot "tools\ffmpeg.exe") "Bundled ffmpeg.exe"
    Require-File (Join-Path $BackendRoot "tools\ffprobe.exe") "Bundled ffprobe.exe"
    Require-File $BackendManifestPath "Frozen Core backend manifest"

    $manifest = Get-Content -LiteralPath $BackendManifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema -ne 2 -or $manifest.runtime_flavor -ne "core-only") {
        throw "Windows installers require a schema-2 core-only sidecar; refusing a legacy or full runtime."
    }
    if ($ExpectedReleaseProfile -notin @($manifest.compatible_release_profiles)) {
        throw "The frozen sidecar is not compatible with release profile '$ExpectedReleaseProfile'."
    }
    $expectedFingerprint = Get-Video2NotesSidecarSourceFingerprint $RepoRoot
    if (
        $manifest.source_fingerprint_schema -ne 1 -or
        ([string]$manifest.source_fingerprint_sha256).ToLowerInvariant() -ne
            $expectedFingerprint.ToLowerInvariant()
    ) {
        throw "The frozen sidecar does not match the current Python and packaging sources. Rebuild without -ReuseSidecar."
    }
    if ($manifest.user_model_weights_included -ne $false) {
        throw "The Core sidecar manifest does not explicitly exclude user model weights."
    }
    if (@($manifest.packaged_runtime_assets).Count -ne 0) {
        throw "The Core sidecar unexpectedly declares packaged local-inference runtime assets."
    }

    return [pscustomobject]@{
        Manifest = $manifest
        ManifestSha256 = (Get-Video2NotesFileSha256 -Path $BackendManifestPath).ToLowerInvariant()
        SourceFingerprintSha256 = $expectedFingerprint.ToLowerInvariant()
        PayloadSizeBytes = [long](
            Get-ChildItem -LiteralPath $BackendRoot -Recurse -File |
                Measure-Object -Property Length -Sum
        ).Sum
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Windows NSIS/MSI installers can only be built on Windows."
}
if ($ReuseSidecar -and ($FfmpegDirectory -or $FfmpegLicensePath)) {
    throw "FFmpeg build inputs cannot be combined with -ReuseSidecar."
}

$releaseProfileDefinition = Get-Video2NotesReleaseProfile $RepoRoot $ReleaseProfile
if (
    $releaseProfileDefinition.sidecar_flavor -ne "core-only" -or
    @($releaseProfileDefinition.runtime_package_ids).Count -ne 0
) {
    throw "The Windows installer build is intentionally restricted to the dependency-free Core release profile."
}

Require-File $TauriConfigPath "Tauri configuration"
$tauriConfiguration = Get-Content -LiteralPath $TauriConfigPath -Raw | ConvertFrom-Json
$productVersion = [string]$tauriConfiguration.version
if ($productVersion -notmatch "^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$") {
    throw "Tauri version '$productVersion' is not a supported semantic version."
}
if (
    $tauriConfiguration.bundle.active -ne $true -or
    [string]$tauriConfiguration.bundle.resources."resources/backend/" -ne "backend/"
) {
    throw "Tauri bundling must map resources/backend/ to backend/ before creating installers."
}
$desktopPackage = Get-Content -LiteralPath (Join-Path $DesktopRoot "package.json") -Raw | ConvertFrom-Json
if ([string]$desktopPackage.version -ne $productVersion) {
    throw "Desktop package and Tauri versions do not match."
}

foreach ($commandName in @("cargo", "git", "pnpm", "rustc")) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "$commandName is unavailable. Run .\scripts\bootstrap.ps1 first."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $DesktopRoot "node_modules") -PathType Container)) {
    throw "Desktop dependencies are missing. Run .\scripts\bootstrap.ps1 first."
}

$cargoMetadataJson = & cargo metadata `
    --manifest-path (Join-Path $TauriRoot "Cargo.toml") `
    --format-version 1 `
    --no-deps
Assert-LastExitCode "Reading Cargo package metadata"
$cargoMetadata = ($cargoMetadataJson -join "`n") | ConvertFrom-Json
$cargoPackages = @($cargoMetadata.packages | Where-Object { $_.name -eq "video2notes-desktop" })
if ($cargoPackages.Count -ne 1 -or [string]$cargoPackages[0].version -ne $productVersion) {
    throw "Rust package and Tauri versions do not match."
}

$targetTriple = (& rustc --print host-tuple).Trim()
Assert-LastExitCode "Reading the Rust host target"
if ($targetTriple -ne "x86_64-pc-windows-msvc") {
    throw "This release script currently publishes only Windows x64 MSVC installers; found '$targetTriple'."
}

$buildRoot = Join-Path $RepoRoot "artifacts\build\windows-release\$productVersion\$ReleaseProfile"
$cargoTargetRoot = Join-Path $buildRoot "target"
$bundleOutputRoot = Join-Path $cargoTargetRoot "release\bundle"
$releaseRoot = Join-Path $RepoRoot "artifacts\release\windows\$productVersion"
New-Item -ItemType Directory -Force -Path $buildRoot, $cargoTargetRoot, $releaseRoot | Out-Null

Push-Location $RepoRoot
try {
    if (-not $ReuseSidecar) {
        $sidecarArguments = @{
            ReleaseProfile = $ReleaseProfile
        }
        if ($SkipSidecarSmoke) { $sidecarArguments["SkipSmoke"] = $true }
        if ($FfmpegDirectory) {
            $sidecarArguments["FfmpegDirectory"] = $FfmpegDirectory
        }
        if ($FfmpegLicensePath) {
            $sidecarArguments["FfmpegLicensePath"] = $FfmpegLicensePath
        }
        & (Join-Path $PSScriptRoot "build_sidecar.ps1") @sidecarArguments
        Assert-LastExitCode "Building the Core backend sidecar"
    }

    $sidecarBeforeBuild = Assert-CoreSidecar $ReleaseProfile
    & (Join-Path $PSScriptRoot "test_sidecar.ps1") `
        -Executable $BackendExecutable `
        -CoreOnly `
        -SkipHealthSmoke
    Assert-LastExitCode "Probing the Core backend sidecar"

    foreach ($bundleId in $BundleIds) {
        $staleBundleDirectory = Assert-PathInside `
            $cargoTargetRoot `
            (Join-Path $bundleOutputRoot $bundleId) `
            "Tauri $bundleId bundle output"
        if (Test-Path -LiteralPath $staleBundleDirectory -PathType Container) {
            Remove-Item -LiteralPath $staleBundleDirectory -Recurse -Force
        }
    }

    $hadCargoTargetDirectory = Test-Path Env:CARGO_TARGET_DIR
    $previousCargoTargetDirectory = $env:CARGO_TARGET_DIR
    try {
        $env:CARGO_TARGET_DIR = $cargoTargetRoot
        Push-Location $DesktopRoot
        try {
            $bundleArgument = $BundleIds -join ","
            pnpm tauri build --bundles $bundleArgument --ci
            Assert-LastExitCode "Building the Tauri NSIS and MSI installers"
        }
        finally {
            Pop-Location
        }
    }
    finally {
        if ($hadCargoTargetDirectory) {
            $env:CARGO_TARGET_DIR = $previousCargoTargetDirectory
        }
        else {
            Remove-Item Env:CARGO_TARGET_DIR -ErrorAction SilentlyContinue
        }
    }

    $sidecarAfterBuild = Assert-CoreSidecar $ReleaseProfile
    if ($sidecarAfterBuild.ManifestSha256 -ne $sidecarBeforeBuild.ManifestSha256) {
        throw "The canonical Core sidecar changed while Tauri was building. Rebuild the installers."
    }

    $installerSpecs = @(
        [ordered]@{
            kind = "nsis"
            extension = ".exe"
            file_name = "Video2Notes-$productVersion-core-windows-x64-setup.exe"
            content_type = "application/vnd.microsoft.portable-executable"
        },
        [ordered]@{
            kind = "msi"
            extension = ".msi"
            file_name = "Video2Notes-$productVersion-core-windows-x64.msi"
            content_type = "application/x-msi"
        }
    )
    $releaseArtifacts = [Collections.Generic.List[object]]::new()
    foreach ($spec in $installerSpecs) {
        $sourceDirectory = Join-Path $bundleOutputRoot ([string]$spec.kind)
        $sourceCandidates = @(
            Get-ChildItem -LiteralPath $sourceDirectory -File |
                Where-Object { $_.Extension -eq [string]$spec.extension }
        )
        if ($sourceCandidates.Count -ne 1) {
            throw "Expected exactly one $($spec.kind) installer, found $($sourceCandidates.Count) in '$sourceDirectory'."
        }
        Assert-InstallerSignature $sourceCandidates[0].FullName ([string]$spec.kind)
        $destinationPath = Join-Path $releaseRoot ([string]$spec.file_name)
        Copy-Item -LiteralPath $sourceCandidates[0].FullName -Destination $destinationPath -Force
        Assert-InstallerSignature $destinationPath ([string]$spec.kind)
        $sha256 = (Get-Video2NotesFileSha256 -Path $destinationPath).ToLowerInvariant()
        Write-ChecksumSidecar $destinationPath $sha256
        $releaseArtifacts.Add([ordered]@{
            installer_format = [string]$spec.kind
            file_name = [string]$spec.file_name
            checksum_file_name = "$([string]$spec.file_name).sha256"
            content_type = [string]$spec.content_type
            size_bytes = (Get-Item -LiteralPath $destinationPath).Length
            sha256 = $sha256
        })
    }

    $gitCommit = (& git rev-parse HEAD).Trim()
    Assert-LastExitCode "Reading the Git commit"
    $gitDescribe = (& git describe --tags --always --dirty).Trim()
    Assert-LastExitCode "Reading the Git build description"
    $gitDirty = [bool](& git status --porcelain=v1)
    Assert-LastExitCode "Reading the Git worktree state"

    $releaseManifestPath = Join-Path $releaseRoot "windows-release-manifest.json"
    $releaseManifest = [ordered]@{
        schema = 1
        product = "Video2Notes"
        version = $productVersion
        platform = "windows"
        architecture = "x86_64"
        target_triple = $targetTriple
        release_profile = $ReleaseProfile
        runtime_flavor = "core-only"
        runtime_delivery = "managed-on-demand"
        built_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        source = [ordered]@{
            commit = $gitCommit
            git_describe = $gitDescribe
            dirty = $gitDirty
        }
        sidecar = [ordered]@{
            reused = [bool]$ReuseSidecar
            requested_release_profile = [string]$sidecarAfterBuild.Manifest.requested_release_profile
            compatible_release_profiles = @($sidecarAfterBuild.Manifest.compatible_release_profiles)
            manifest_sha256 = $sidecarAfterBuild.ManifestSha256
            source_fingerprint_schema = 1
            source_fingerprint_sha256 = $sidecarAfterBuild.SourceFingerprintSha256
            payload_size_bytes = $sidecarAfterBuild.PayloadSizeBytes
            user_model_weights_included = $false
        }
        artifacts = @($releaseArtifacts)
    }
    Write-Utf8Json $releaseManifestPath $releaseManifest
    $releaseManifestHash = (Get-Video2NotesFileSha256 -Path $releaseManifestPath).ToLowerInvariant()
    Write-ChecksumSidecar $releaseManifestPath $releaseManifestHash

    $checksumLines = @(
        $releaseArtifacts | ForEach-Object { "$($_.sha256)  $($_.file_name)" }
        "$releaseManifestHash  $([IO.Path]::GetFileName($releaseManifestPath))"
    )
    [IO.File]::WriteAllText(
        (Join-Path $releaseRoot "SHA256SUMS.txt"),
        (($checksumLines -join "`n") + "`n"),
        [Text.UTF8Encoding]::new($false)
    )

    Write-Host "Windows Core installers are ready: $releaseRoot" -ForegroundColor Green
    foreach ($artifact in $releaseArtifacts) {
        Write-Host (
            "  {0}: {1:N1} MiB; SHA-256 {2}" -f
                $artifact.file_name,
                ([long]$artifact.size_bytes / 1MB),
                $artifact.sha256
        )
    }
}
finally {
    Pop-Location
}
