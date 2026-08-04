[CmdletBinding()]
param(
    # Reuse the already frozen canonical sidecar for faster UI/Rust iteration.
    # Its complete manifest is still verified before it enters the portable tree.
    [switch]$ReuseSidecar,
    [ValidateSet("core", "cpu", "nvidia_asr", "full_gpu", "legacy_full")]
    [string]$ReleaseProfile = "legacy_full",
    # Backward-compatible alias for existing development commands.
    [switch]$CoreOnly,
    # Directory containing ZIP + .zip.catalog-entry.json pairs created by
    # build_runtime_pack.ps1 for an offline release profile.
    [string]$OfflineRuntimePackDirectory = "",
    [switch]$SkipSidecarSmoke,
    [switch]$Zip,
    # Keep the immediately previous portable directory after a successful
    # replacement. By default it is only used for rollback during the build
    # and is deleted once the new current directory passes verification.
    [switch]$KeepPreviousPortable,
    [string]$FfmpegDirectory = "",
    [string]$FfmpegLicensePath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "packaging_common.ps1")
$DesktopRoot = Join-Path $RepoRoot "apps\desktop"
$TauriRoot = Join-Path $DesktopRoot "src-tauri"
$CanonicalBackendRoot = Join-Path $TauriRoot "resources\backend"
$ReleaseExecutable = Join-Path $TauriRoot "target\release\video2notes-desktop.exe"
$PortableParent = Join-Path $RepoRoot "artifacts\portable"
$PortableCurrent = Join-Path $PortableParent "current"
$PortableStaging = Join-Path $PortableParent (".staging-" + [guid]::NewGuid().ToString("N"))
$PortableBackup = Join-Path $PortableParent (
    ".backup-" + [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ") + "-" +
        [guid]::NewGuid().ToString("N")
)
$PortableMarkerName = ".video2notes-portable.json"
$EffectiveReleaseProfile = if ($CoreOnly) { "core" } else { $ReleaseProfile }
if (
    $CoreOnly -and
    $PSBoundParameters.ContainsKey("ReleaseProfile") -and
    $ReleaseProfile -ne "core"
) {
    throw "-CoreOnly is an alias for -ReleaseProfile core and cannot be combined with '$ReleaseProfile'."
}
$ReleaseProfileDefinition = Get-Video2NotesReleaseProfile $RepoRoot $EffectiveReleaseProfile
$UseCoreSidecar = $ReleaseProfileDefinition.sidecar_flavor -eq "core-only"
$RuntimeFlavor = [string]$ReleaseProfileDefinition.sidecar_flavor
$ExpectedRuntimePackageIds = @(
    $ReleaseProfileDefinition.runtime_package_ids | ForEach-Object { [string]$_ }
)

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

function Write-JsonFile {
    param([string]$Path, [object]$Value)
    $json = $Value | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText($Path, "$json`n", [Text.UTF8Encoding]::new($false))
}

function Stage-OfflineRuntimePacks {
    param(
        [AllowEmptyString()][string]$SourceDirectory,
        [string]$DestinationDirectory,
        [string]$ProfileId,
        [string[]]$ExpectedPackageIds
    )

    New-Item -ItemType Directory -Path $DestinationDirectory | Out-Null
    $offlineRoot = Join-Path $DestinationDirectory "offline"
    New-Item -ItemType Directory -Path $offlineRoot | Out-Null
    $trustedCatalog = Join-Path $RepoRoot "packaging\runtime-packs\catalog.json"
    $releaseProfiles = Join-Path $RepoRoot "packaging\runtime-packs\release-profiles.json"
    Require-File $trustedCatalog "Trusted runtime package catalog"
    Require-File $releaseProfiles "Runtime release profile catalog"
    Copy-Item -LiteralPath $trustedCatalog -Destination (Join-Path $DestinationDirectory "catalog.json")
    Copy-Item -LiteralPath $releaseProfiles -Destination (Join-Path $DestinationDirectory "release-profiles.json")

    if ($ExpectedPackageIds.Count -eq 0) {
        if ($SourceDirectory) {
            throw "Release profile '$ProfileId' does not accept offline runtime pack archives."
        }
        $entries = @()
    }
    else {
        if (-not $SourceDirectory) {
            throw "Release profile '$ProfileId' requires an -OfflineRuntimePackDirectory containing: $($ExpectedPackageIds -join ', ')."
        }
        $resolvedSource = [IO.Path]::GetFullPath($SourceDirectory)
        if (-not (Test-Path -LiteralPath $resolvedSource -PathType Container)) {
            throw "Offline runtime pack directory was not found at '$resolvedSource'."
        }
        $entryFiles = @(Get-ChildItem -LiteralPath $resolvedSource -Filter "*.zip.catalog-entry.json" -File)
        $entries = @(
            foreach ($entryFile in $entryFiles) {
                $entry = Get-Content -LiteralPath $entryFile.FullName -Raw | ConvertFrom-Json
                if ($entry.schema -ne 1 -or -not $entry.package_id -or -not $entry.archive.file_name) {
                    throw "Offline runtime catalog entry '$($entryFile.FullName)' is unsupported."
                }
                $archiveFileName = [string]$entry.archive.file_name
                if ($archiveFileName -ne [IO.Path]::GetFileName($archiveFileName)) {
                    throw "Offline runtime catalog entry '$($entryFile.FullName)' contains an unsafe archive filename."
                }
                $archivePath = Join-Path $resolvedSource $archiveFileName
                Require-File $archivePath "Offline runtime pack archive"
                & (Join-Path $PSScriptRoot "test_runtime_pack.ps1") `
                    -ArchivePath $archivePath `
                    -CatalogEntryPath $entryFile.FullName
                Copy-Item -LiteralPath $archivePath -Destination (Join-Path $offlineRoot ([IO.Path]::GetFileName($archivePath)))
                Copy-Item -LiteralPath $entryFile.FullName -Destination (Join-Path $offlineRoot $entryFile.Name)
                $entry
            }
        )
        $actualIds = @($entries | ForEach-Object { [string]$_.package_id })
        $duplicateIds = @(
            $actualIds | Group-Object | Where-Object { $_.Count -ne 1 } | ForEach-Object { $_.Name }
        )
        $missingIds = @($ExpectedPackageIds | Where-Object { $_ -notin $actualIds })
        $unexpectedIds = @($actualIds | Where-Object { $_ -notin $ExpectedPackageIds })
        if ($duplicateIds.Count -gt 0 -or $missingIds.Count -gt 0 -or $unexpectedIds.Count -gt 0) {
            throw "Offline runtime pack set does not match release profile '$ProfileId'. Missing: $($missingIds -join ', '); unexpected: $($unexpectedIds -join ', '); duplicate: $($duplicateIds -join ', ')."
        }
    }

    $offlineCatalog = [ordered]@{
        schema = 1
        release_profile = $ProfileId
        packages = @($entries)
    }
    Write-JsonFile (Join-Path $DestinationDirectory "offline-catalog.json") $offlineCatalog
    return @(
        $entries | ForEach-Object {
            [ordered]@{
                package_id = [string]$_.package_id
                version = [string]$_.version
                archive_file_name = [string]$_.archive.file_name
                archive_size_bytes = [long]$_.archive.size_bytes
                installed_size_bytes = [long]$_.installed_size_bytes
            }
        }
    )
}

function Assert-ManagedPortablePath {
    param([string]$Path)

    $resolvedRepository = [IO.Path]::GetFullPath($RepoRoot).TrimEnd("\") + "\"
    $resolvedParent = [IO.Path]::GetFullPath($PortableParent).TrimEnd("\") + "\"
    $resolvedTarget = [IO.Path]::GetFullPath($Path).TrimEnd("\")
    if (-not $resolvedParent.StartsWith($resolvedRepository, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Portable output escaped the repository: '$resolvedParent'."
    }
    if (-not $resolvedTarget.StartsWith($resolvedParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to manage a path outside the portable output root: '$resolvedTarget'."
    }

    $leaf = [IO.Path]::GetFileName($resolvedTarget)
    if (
        $leaf -ne "current" -and
        -not $leaf.StartsWith(".staging-") -and
        -not $leaf.StartsWith(".backup-")
    ) {
        throw "Refusing to manage an unexpected portable output directory: '$resolvedTarget'."
    }
    return $resolvedTarget
}

function Assert-PortableNotRunning {
    param([string]$Directory)

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return
    }
    $prefix = [IO.Path]::GetFullPath($Directory).TrimEnd("\") + "\"
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    }
    catch {
        throw "Could not verify whether the current portable directory is running; refusing to replace it."
    }
    $running = @(
        $processes |
            Where-Object {
                $_.ExecutablePath -and
                [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
                    $prefix,
                    [StringComparison]::OrdinalIgnoreCase
                )
            }
    )
    if ($running.Count -gt 0) {
        $details = $running | ForEach-Object { "$($_.Name) (PID $($_.ProcessId))" }
        throw "Close the running portable app before replacing it: $($details -join ', ')."
    }
}

function Assert-ExistingPortableMarker {
    param([string]$Directory)

    $directoryItem = Get-Item -LiteralPath $Directory
    if (($directoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to replace a portable output directory that is a junction or symbolic link."
    }
    $markerPath = Join-Path $Directory $PortableMarkerName
    Require-File $markerPath "Portable output marker"
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ($marker.product -ne "Video2Notes" -or $marker.portable -ne $true) {
        throw "Refusing to replace an output directory without a valid Video2Notes portable marker."
    }
}

function Assert-BackendManifest {
    param(
        [string]$BackendRoot,
        [AllowEmptyString()][string]$ExpectedRuntimeFlavor = "",
        [AllowEmptyString()][string]$ExpectedReleaseProfile = "",
        [AllowEmptyString()][string]$ExpectedSourceFingerprint = "",
        [switch]$AllowLegacy,
        [switch]$AllowPreNvidiaFullRuntime
    )

    $manifestPath = Join-Path $BackendRoot "manifest.json"
    Require-File $manifestPath "Frozen backend manifest"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema -notin @(1, 2) -or -not $manifest.files) {
        throw "The frozen backend manifest is unsupported or empty."
    }
    if ($manifest.schema -eq 1 -and -not $AllowLegacy) {
        throw "The frozen backend manifest predates runtime-flavor verification and must be rebuilt."
    }
    if ($manifest.schema -eq 2) {
        if ($manifest.runtime_flavor -notin @("full", "core-only")) {
            throw "The frozen backend manifest has an invalid runtime flavor."
        }
        if ($ExpectedRuntimeFlavor -and $manifest.runtime_flavor -ne $ExpectedRuntimeFlavor) {
            throw "Portable runtime '$ExpectedRuntimeFlavor' cannot use a '$($manifest.runtime_flavor)' sidecar. Rebuild it for the selected -ReleaseProfile."
        }
        if (
            $ExpectedReleaseProfile -and
            $ExpectedReleaseProfile -notin @($manifest.compatible_release_profiles)
        ) {
            throw "Frozen backend is not compatible with release profile '$ExpectedReleaseProfile'."
        }
        if (
            $ExpectedSourceFingerprint -and
            (
                $manifest.source_fingerprint_schema -ne 1 -or
                ([string]$manifest.source_fingerprint_sha256).ToLowerInvariant() -ne
                    $ExpectedSourceFingerprint.ToLowerInvariant()
            )
        ) {
            throw "The frozen backend does not match the current Python/sidecar sources. Rebuild without -ReuseSidecar before creating the portable app."
        }
        if ($manifest.user_model_weights_included -ne $false) {
            throw "The frozen backend manifest does not explicitly exclude user model weights."
        }
        $baseFullInferenceIds = @(
            "faster-whisper", "ctranslate2", "paddleocr", "paddlepaddle"
        )
        $ctranslateNvidiaInferenceIds = @(
            "nvidia-cublas-cu12",
            "nvidia-cuda-nvrtc-cu12",
            "nvidia-cudnn-cu12",
            "nvidia-nvjitlink-cu12"
        )
        $paddleGpuNvidiaInferenceIds = @(
            "nvidia-cuda-runtime-cu12",
            "nvidia-cufft-cu12",
            "nvidia-curand-cu12",
            "nvidia-cusolver-cu12",
            "nvidia-cusparse-cu12"
        )
        $nvidiaInferenceIds = @(
            $ctranslateNvidiaInferenceIds + $paddleGpuNvidiaInferenceIds
        )
        $fullInferenceIds = @($baseFullInferenceIds + $nvidiaInferenceIds)
        $paddleMatches = @($manifest.components | Where-Object { $_.id -eq "paddlepaddle" })
        $paddleDistribution = if ($paddleMatches.Count -eq 1) {
            [string]$paddleMatches[0].distribution
        }
        else {
            ""
        }
        if (
            $manifest.runtime_flavor -eq "full" -and
            $paddleMatches.Count -eq 1 -and
            $paddleDistribution -notin @("paddlepaddle", "paddlepaddle-gpu")
        ) {
            throw "The frozen backend manifest has an unsupported PaddlePaddle distribution."
        }
        $requiredNvidiaInferenceIds = @($ctranslateNvidiaInferenceIds)
        if (
            $manifest.runtime_flavor -eq "full" -and
            $paddleDistribution -eq "paddlepaddle-gpu"
        ) {
            $requiredNvidiaInferenceIds += $paddleGpuNvidiaInferenceIds
        }
        $hasNoNvidiaComponentContract = (
            $AllowPreNvidiaFullRuntime -and
            $manifest.runtime_flavor -eq "full" -and
            @(
                $manifest.components |
                    Where-Object { $_.id -in $nvidiaInferenceIds }
            ).Count -eq 0
        )
        $requiredIds = @("yt-dlp", "psutil", "huggingface-hub", "ffmpeg", "ffprobe")
        if ($manifest.runtime_flavor -eq "full") {
            $requiredIds += $baseFullInferenceIds
            $requiredIds += $requiredNvidiaInferenceIds
        }
        foreach ($componentId in $requiredIds) {
            $matches = @($manifest.components | Where-Object { $_.id -eq $componentId })
            # Migration-only compatibility for full packages built before the
            # NVIDIA component contract existed. A present but invalid entry
            # still fails, and newly staged packages never enable this switch.
            $isMissingPreNvidiaComponent = (
                $hasNoNvidiaComponentContract -and
                $componentId -in $requiredNvidiaInferenceIds -and
                $matches.Count -eq 0
            )
            if ($isMissingPreNvidiaComponent) {
                continue
            }
            if (
                $matches.Count -ne 1 -or
                $matches[0].included -ne $true -or
                $matches[0].status -ne "bundled" -or
                -not $matches[0].version
            ) {
                throw "The frozen backend manifest does not verify bundled component '$componentId'."
            }
            if (
                $matches[0].kind -eq "python-package" -and
                $matches[0].import_verified -ne $true
            ) {
                throw "The frozen backend manifest does not verify importing '$componentId'."
            }
            if (
                $matches[0].kind -eq "executable" -and
                $matches[0].executable_verified -ne $true
            ) {
                throw "The frozen backend manifest does not verify executing '$componentId'."
            }
        }
        if (
            $manifest.runtime_flavor -eq "full" -and
            $paddleDistribution -eq "paddlepaddle" -and
            -not $hasNoNvidiaComponentContract
        ) {
            foreach ($componentId in $paddleGpuNvidiaInferenceIds) {
                $matches = @($manifest.components | Where-Object { $_.id -eq $componentId })
                if (
                    $matches.Count -ne 1 -or
                    $matches[0].included -ne $false -or
                    $matches[0].status -ne "excluded-core-only"
                ) {
                    throw "CPU full component '$componentId' is not explicitly marked excluded."
                }
            }
        }
        if ($manifest.runtime_flavor -eq "core-only") {
            foreach ($componentId in $fullInferenceIds) {
                $matches = @($manifest.components | Where-Object { $_.id -eq $componentId })
                if (
                    $matches.Count -ne 1 -or
                    $matches[0].included -ne $false -or
                    $matches[0].status -ne "excluded-core-only"
                ) {
                    throw "Core-only component '$componentId' is not explicitly marked excluded."
                }
            }
        }
    }
    $expectedFiles = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $manifest.files) {
        $relativePath = ([string]$entry.relative_path) -replace "\\", "/"
        $segments = @($relativePath.Split("/", [StringSplitOptions]::RemoveEmptyEntries))
        if (
            -not $relativePath -or
            [IO.Path]::IsPathRooted($relativePath) -or
            $segments.Count -eq 0 -or
            $segments.Count -ne @($relativePath.Split("/")).Count -or
            @($segments | Where-Object { $_ -in @(".", "..") }).Count -gt 0
        ) {
            throw "The frozen backend manifest contains an unsafe path: '$relativePath'."
        }
        if (-not $expectedFiles.Add($relativePath)) {
            throw "The frozen backend manifest contains a duplicate path: '$relativePath'."
        }
        $candidate = Join-Path $BackendRoot ($relativePath -replace "/", "\")
        Require-File $candidate "Frozen backend manifest entry"
        $item = Get-Item -LiteralPath $candidate
        if ($item.Length -ne [long]$entry.bytes) {
            throw "Frozen backend size mismatch for '$relativePath'."
        }
        $actualHash = (Get-Video2NotesFileSha256 -Path $candidate).ToLowerInvariant()
        if ($actualHash -ne ([string]$entry.sha256).ToLowerInvariant()) {
            throw "Frozen backend hash mismatch for '$relativePath'."
        }
    }

    $unexpectedFiles = @(
        Get-ChildItem -LiteralPath $BackendRoot -Recurse -File |
            Where-Object {
                if ($_.FullName -eq $manifestPath) {
                    return $false
                }
                $relative = $_.FullName.Substring($BackendRoot.Length).TrimStart("\") -replace "\\", "/"
                -not $expectedFiles.Contains($relative)
            }
    )
    if ($unexpectedFiles.Count -gt 0) {
        throw "The frozen backend contains files outside its manifest: $($unexpectedFiles.FullName -join ', ')."
    }
    return $manifest
}

function Assert-NoPrivatePayload {
    param([string]$PortableRoot)

    $forbidden = @(
        Get-ChildItem -LiteralPath $PortableRoot -Recurse -File |
            Where-Object {
                $relative = $_.FullName.Substring($PortableRoot.Length).TrimStart("\") -replace "\\", "/"
                $extension = $_.Extension.ToLowerInvariant()
                $allowedRuntimeAsset =
                    $relative -eq "backend/_internal/faster_whisper/assets/silero_vad_v6.onnx"
                if ($allowedRuntimeAsset) { return $false }
                $relative -match "(?i)^(\.venv|node_modules|data|runs|config)(/|$)" -or
                $relative -match "(?i)(^|/)\.cache(/|$)" -or
                $_.Name -match "(?i)(^|\.)(cookies?\.txt|keyring\.json)$" -or
                $_.Name -match "(?i)^\.env(?:\.|$)" -or
                $_.Name -match "(?i)^(model|pytorch_model|adapter_model)\.bin$" -or
                $extension -in @(
                    ".cookies", ".sqlite", ".sqlite3", ".db", ".db-shm", ".db-wal",
                    ".safetensors", ".gguf", ".onnx", ".pt", ".pth", ".ckpt",
                    ".pdmodel", ".pdiparams", ".pdparams", ".pdopt"
                ) -or
                ($extension -in @(".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv") -and
                    $relative -ne "demo/evidence-demo.mp4")
            }
    )
    if ($forbidden.Count -gt 0) {
        throw "Portable output contains prohibited user/model data: $($forbidden.FullName -join ', ')."
    }
}

function Assert-PortableLayout {
    param(
        [string]$PortableRoot,
        [AllowEmptyString()][string]$ExpectedRuntimeFlavor = "",
        [AllowEmptyString()][string]$ExpectedReleaseProfile = "",
        [switch]$AllowLegacyBackend,
        [switch]$AllowPreNvidiaFullRuntimeBackend
    )

    $allowedTopLevel = @(
        "backend",
        "demo",
        "licenses",
        "runtime-packs",
        $PortableMarkerName,
        "BUILD_INFO.json",
        "PORTABLE_README.txt",
        "SHA256SUMS.txt",
        "Video2Notes.exe"
    )
    $unexpectedTopLevel = @(
        Get-ChildItem -LiteralPath $PortableRoot -Force |
            Where-Object { $_.Name -notin $allowedTopLevel }
    )
    if ($unexpectedTopLevel.Count -gt 0) {
        throw "Portable output contains unexpected top-level entries: $($unexpectedTopLevel.FullName -join ', ')."
    }

    Require-File (Join-Path $PortableRoot "Video2Notes.exe") "Portable desktop executable"
    Require-File (Join-Path $PortableRoot $PortableMarkerName) "Portable output marker"
    Require-File (Join-Path $PortableRoot "BUILD_INFO.json") "Portable build information"
    Require-File (Join-Path $PortableRoot "PORTABLE_README.txt") "Portable usage note"
    Require-File (Join-Path $PortableRoot "SHA256SUMS.txt") "Portable checksum manifest"
    foreach ($directoryName in @("backend", "demo", "licenses")) {
        if (-not (Test-Path -LiteralPath (Join-Path $PortableRoot $directoryName) -PathType Container)) {
            throw "Portable resource directory '$directoryName' is missing."
        }
    }
    $runtimePacksRoot = Join-Path $PortableRoot "runtime-packs"
    if (Test-Path -LiteralPath $runtimePacksRoot -PathType Container) {
        foreach ($metadataName in @("catalog.json", "release-profiles.json", "offline-catalog.json")) {
            Require-File (Join-Path $runtimePacksRoot $metadataName) "Portable runtime package metadata"
        }
        $offlineCatalog = Get-Content -LiteralPath (Join-Path $runtimePacksRoot "offline-catalog.json") -Raw | ConvertFrom-Json
        if (
            $offlineCatalog.schema -ne 1 -or
            ($ExpectedReleaseProfile -and $offlineCatalog.release_profile -ne $ExpectedReleaseProfile)
        ) {
            throw "Portable offline runtime package catalog does not match the release profile."
        }
    }
    elseif (-not $AllowLegacyBackend) {
        throw "Portable runtime-packs directory is missing."
    }

    $demoEntries = @(Get-ChildItem -LiteralPath (Join-Path $PortableRoot "demo") -Recurse -Force)
    if (
        $demoEntries.Count -ne 1 -or
        $demoEntries[0].PSIsContainer -or
        $demoEntries[0].Name -ne "evidence-demo.mp4"
    ) {
        throw "The portable demo directory must contain only evidence-demo.mp4."
    }

    $allowedLicenseFiles = @("THIRD_PARTY_NOTICES.md", "VIDEO2NOTES_LICENSE.txt")
    $licenseEntries = @(Get-ChildItem -LiteralPath (Join-Path $PortableRoot "licenses") -Recurse -Force)
    if (
        $licenseEntries.Count -ne $allowedLicenseFiles.Count -or
        @($licenseEntries | Where-Object { $_.PSIsContainer -or $_.Name -notin $allowedLicenseFiles }).Count -gt 0
    ) {
        throw "The portable licenses directory contains unexpected entries."
    }

    $null = Assert-BackendManifest `
        (Join-Path $PortableRoot "backend") `
        $ExpectedRuntimeFlavor `
        $ExpectedReleaseProfile `
        -AllowLegacy:$AllowLegacyBackend `
        -AllowPreNvidiaFullRuntime:$AllowPreNvidiaFullRuntimeBackend
}

function Write-Sha256Sums {
    param([string]$PortableRoot)

    $checksumPath = Join-Path $PortableRoot "SHA256SUMS.txt"
    $lines = @(
        Get-ChildItem -LiteralPath $PortableRoot -Recurse -File |
            Where-Object { $_.FullName -ne $checksumPath } |
            Sort-Object FullName |
            ForEach-Object {
                $relative = $_.FullName.Substring($PortableRoot.Length).TrimStart("\") -replace "\\", "/"
                "{0}  {1}" -f (Get-Video2NotesFileSha256 -Path $_.FullName), $relative
            }
    )
    [IO.File]::WriteAllLines($checksumPath, $lines, [Text.UTF8Encoding]::new($false))
}

function Assert-PortableChecksums {
    param([string]$PortableRoot)

    $checksumPath = Join-Path $PortableRoot "SHA256SUMS.txt"
    Require-File $checksumPath "Portable checksum manifest"
    $expectedFiles = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($line in Get-Content -LiteralPath $checksumPath) {
        if ($line -notmatch "^([0-9A-Fa-f]{64})  (.+)$") {
            throw "The portable checksum manifest contains a malformed line."
        }
        $expectedHash = $Matches[1].ToUpperInvariant()
        $relativePath = $Matches[2] -replace "\\", "/"
        $segments = @($relativePath.Split("/", [StringSplitOptions]::RemoveEmptyEntries))
        if (
            [IO.Path]::IsPathRooted($relativePath) -or
            $segments.Count -eq 0 -or
            $segments.Count -ne @($relativePath.Split("/")).Count -or
            @($segments | Where-Object { $_ -in @(".", "..") }).Count -gt 0
        ) {
            throw "The portable checksum manifest contains an unsafe path: '$relativePath'."
        }
        if (-not $expectedFiles.Add($relativePath)) {
            throw "The portable checksum manifest contains a duplicate path: '$relativePath'."
        }
        $candidate = Join-Path $PortableRoot ($relativePath -replace "/", "\")
        Require-File $candidate "Portable checksum entry"
        $actualHash = Get-Video2NotesFileSha256 -Path $candidate
        if ($actualHash -ne $expectedHash) {
            throw "Portable checksum mismatch for '$relativePath'. Move personal changes out before rebuilding."
        }
    }

    $unexpectedFiles = @(
        Get-ChildItem -LiteralPath $PortableRoot -Recurse -File |
            Where-Object {
                if ($_.FullName -eq $checksumPath) {
                    return $false
                }
                $relative = $_.FullName.Substring($PortableRoot.Length).TrimStart("\") -replace "\\", "/"
                -not $expectedFiles.Contains($relative)
            }
    )
    if ($unexpectedFiles.Count -gt 0) {
        throw "Portable output contains unmanaged files. Move them out before rebuilding: $($unexpectedFiles.FullName -join ', ')."
    }
}

Push-Location $RepoRoot
try {
    if (-not $ReuseSidecar) {
        $sidecarArguments = @{
            ReleaseProfile = $EffectiveReleaseProfile
        }
        if ($SkipSidecarSmoke) { $sidecarArguments["SkipSmoke"] = $true }
        if ($FfmpegDirectory) { $sidecarArguments["FfmpegDirectory"] = $FfmpegDirectory }
        if ($FfmpegLicensePath) { $sidecarArguments["FfmpegLicensePath"] = $FfmpegLicensePath }
        & (Join-Path $PSScriptRoot "build_sidecar.ps1") @sidecarArguments
        Assert-LastExitCode "Building and smoke-testing the self-contained backend sidecar"
    }

    Require-File (Join-Path $CanonicalBackendRoot "video2notes.exe") "Frozen backend executable"
    Require-File (Join-Path $CanonicalBackendRoot "tools\ffmpeg.exe") "Bundled ffmpeg.exe"
    Require-File (Join-Path $CanonicalBackendRoot "tools\ffprobe.exe") "Bundled ffprobe.exe"
    $ExpectedSidecarSourceFingerprint = Get-Video2NotesSidecarSourceFingerprint $RepoRoot
    $null = Assert-BackendManifest `
        $CanonicalBackendRoot `
        $RuntimeFlavor `
        $EffectiveReleaseProfile `
        -ExpectedSourceFingerprint $ExpectedSidecarSourceFingerprint

    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
        throw "pnpm is unavailable. Run .\scripts\bootstrap.ps1 first."
    }
    Push-Location $DesktopRoot
    try {
        pnpm tauri build --no-bundle --ci
        Assert-LastExitCode "Building the portable Tauri release executable"
    }
    finally {
        Pop-Location
    }
    Require-File $ReleaseExecutable "Tauri release executable"

    New-Item -ItemType Directory -Force -Path $PortableParent | Out-Null
    $safeStaging = Assert-ManagedPortablePath $PortableStaging
    New-Item -ItemType Directory -Path $safeStaging | Out-Null

    Copy-Item -LiteralPath $ReleaseExecutable -Destination (Join-Path $safeStaging "Video2Notes.exe")
    Copy-Item -LiteralPath $CanonicalBackendRoot -Destination (Join-Path $safeStaging "backend") -Recurse
    New-Item -ItemType Directory -Path (Join-Path $safeStaging "demo"), (Join-Path $safeStaging "licenses") | Out-Null
    Copy-Item -LiteralPath (Join-Path $RepoRoot "samples\evidence-demo.mp4") -Destination (Join-Path $safeStaging "demo\evidence-demo.mp4")
    Copy-Item -LiteralPath (Join-Path $RepoRoot "LICENSE") -Destination (Join-Path $safeStaging "licenses\VIDEO2NOTES_LICENSE.txt")
    Copy-Item -LiteralPath (Join-Path $RepoRoot "THIRD_PARTY_NOTICES.md") -Destination (Join-Path $safeStaging "licenses\THIRD_PARTY_NOTICES.md")
    $StagedOfflineRuntimePackages = @(
        Stage-OfflineRuntimePacks `
            -SourceDirectory $OfflineRuntimePackDirectory `
            -DestinationDirectory (Join-Path $safeStaging "runtime-packs") `
            -ProfileId $EffectiveReleaseProfile `
            -ExpectedPackageIds $ExpectedRuntimePackageIds
    )

    $StagedBackendManifest = Assert-BackendManifest `
        (Join-Path $safeStaging "backend") `
        $RuntimeFlavor `
        $EffectiveReleaseProfile `
        -ExpectedSourceFingerprint $ExpectedSidecarSourceFingerprint
    & (Join-Path $PSScriptRoot "test_sidecar.ps1") `
        -Executable (Join-Path $safeStaging "backend\video2notes.exe") `
        -CoreOnly:$UseCoreSidecar `
        -SkipHealthSmoke
    Assert-LastExitCode "Validating the portable backend runtime imports and bundled tools"
    Assert-NoPrivatePayload $safeStaging

    $tauriConfiguration = Get-Content -LiteralPath (Join-Path $TauriRoot "tauri.conf.json") -Raw | ConvertFrom-Json
    $gitCommit = (& git rev-parse HEAD).Trim()
    Assert-LastExitCode "Reading the Git commit"
    $gitDescribe = (& git describe --tags --always --dirty).Trim()
    Assert-LastExitCode "Reading the Git build description"
    $gitDirty = [bool](& git status --porcelain=v1)
    Assert-LastExitCode "Reading the Git worktree state"
    $targetTriple = (& rustc --print host-tuple).Trim()
    Assert-LastExitCode "Reading the Rust target triple"

    $buildInfo = [ordered]@{
        schema = 2
        product = "Video2Notes"
        version = [string]$tauriConfiguration.version
        git_describe = $gitDescribe
        commit = $gitCommit
        dirty = $gitDirty
        built_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        target_triple = $targetTriple
        portable = $true
        sidecar_reused = [bool]$ReuseSidecar
        runtime_flavor = $RuntimeFlavor
        release_profile = $EffectiveReleaseProfile
        managed_runtime_packages = $StagedOfflineRuntimePackages
        sidecar_source_fingerprint_sha256 = $ExpectedSidecarSourceFingerprint
        user_model_weights_included = $false
        packaged_runtime_assets = $StagedBackendManifest.packaged_runtime_assets
        runtime_components = $StagedBackendManifest.components
        executable_sha256 = Get-Video2NotesFileSha256 -Path (Join-Path $safeStaging "Video2Notes.exe")
        sidecar_manifest_sha256 = Get-Video2NotesFileSha256 -Path (Join-Path $safeStaging "backend\manifest.json")
    }
    $buildInfo | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $safeStaging "BUILD_INFO.json") -Encoding utf8
    [ordered]@{
        schema = 2
        product = "Video2Notes"
        portable = $true
        runtime_flavor = $RuntimeFlavor
        release_profile = $EffectiveReleaseProfile
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $safeStaging $PortableMarkerName) -Encoding utf8

    $StagedPaddleDistribution = if ($UseCoreSidecar) {
        ""
    }
    else {
        [string]@(
            $StagedBackendManifest.components |
                Where-Object { $_.id -eq "paddlepaddle" }
        )[0].distribution
    }
    $PortableRuntimeNote = if ($EffectiveReleaseProfile -eq "core") {
        "这是小体积 Core 构建；模型下载能力保留，本地 ASR/OCR 推理运行时由应用按需安装。"
    }
    elseif ($EffectiveReleaseProfile -eq "cpu") {
        "这是 CPU 离线构建；附带的 CPU ASR/OCR ZIP 使用与在线安装相同的受管运行时包格式。"
    }
    elseif ($EffectiveReleaseProfile -eq "nvidia_asr") {
        "这是 NVIDIA ASR 离线构建；CUDA ASR 与 CPU OCR 运行时作为可升级、可移除的受管包提供。"
    }
    elseif ($EffectiveReleaseProfile -eq "full_gpu") {
        "这是 Full GPU 离线构建；CUDA ASR/OCR 运行时作为可升级、可移除的受管包提供。"
    }
    elseif ($StagedPaddleDistribution -eq "paddlepaddle-gpu") {
        "这是默认 full GPU 构建，已经包含本地 faster-whisper、CTranslate2、PaddleOCR、PaddlePaddle GPU 与完整 NVIDIA CUDA 12.9 推理运行库。"
    }
    else {
        "这是默认 full CPU OCR 构建，已经包含本地 faster-whisper、CTranslate2、PaddleOCR、PaddlePaddle CPU 与 ASR 所需的 NVIDIA CUDA 12.9 运行库。"
    }
    @"
Video2Notes 免安装版（profile=$EffectiveReleaseProfile; runtime=$RuntimeFlavor）

直接双击 Video2Notes.exe。backend、demo、licenses、runtime-packs 四个目录必须与主程序一起保留。
本版本不会创建安装项或卸载项。任务、配置和 WebView 状态默认保存在 Windows 用户 AppData，
因此覆盖 current 程序目录不会删除既有任务。API 密钥仍保存在 Windows Credential Manager。
$PortableRuntimeNote
用户无需手动安装 Python、FFmpeg、yt-dlp 或推理 Python 包。受管运行时和 ASR/OCR 模型权重
由应用负责显示来源与大小、下载、校验、安装、绑定、升级和移除。

兼容单体构建：.\scripts\build_portable.ps1 -ReleaseProfile legacy_full
小体积 Core：.\scripts\build_portable.ps1 -ReleaseProfile core
快速复用后端：.\scripts\build_portable.ps1 -ReuseSidecar（仅当前 Python/打包源码指纹一致时允许）
旧命令别名：.\scripts\build_portable.ps1 -CoreOnly
"@ | Set-Content -LiteralPath (Join-Path $safeStaging "PORTABLE_README.txt") -Encoding utf8
    Write-Sha256Sums $safeStaging
    Assert-PortableLayout $safeStaging $RuntimeFlavor $EffectiveReleaseProfile
    Assert-PortableChecksums $safeStaging

    Assert-PortableNotRunning $PortableCurrent
    $safeCurrent = Assert-ManagedPortablePath $PortableCurrent
    $safeBackup = Assert-ManagedPortablePath $PortableBackup
    if (Test-Path -LiteralPath $safeCurrent -PathType Container) {
        Assert-ExistingPortableMarker $safeCurrent
        Assert-PortableLayout `
            $safeCurrent `
            -AllowLegacyBackend `
            -AllowPreNvidiaFullRuntimeBackend
        Assert-PortableChecksums $safeCurrent
        Assert-NoPrivatePayload $safeCurrent
        Move-Item -LiteralPath $safeCurrent -Destination $safeBackup
    }
    try {
        if (Test-Path -LiteralPath $safeBackup -PathType Container) {
            Assert-ExistingPortableMarker $safeBackup
            Assert-PortableLayout `
                $safeBackup `
                -AllowLegacyBackend `
                -AllowPreNvidiaFullRuntimeBackend
            Assert-PortableChecksums $safeBackup
            Assert-NoPrivatePayload $safeBackup
        }
        Move-Item -LiteralPath $safeStaging -Destination $safeCurrent
        Assert-PortableLayout $safeCurrent $RuntimeFlavor $EffectiveReleaseProfile
        Assert-PortableChecksums $safeCurrent
        Assert-NoPrivatePayload $safeCurrent
    }
    catch {
        if (Test-Path -LiteralPath $safeCurrent -PathType Container) {
            $safeFailedCurrent = Assert-ManagedPortablePath $safeCurrent
            Remove-Item -LiteralPath $safeFailedCurrent -Recurse -Force
        }
        if (Test-Path -LiteralPath $safeBackup -PathType Container) {
            Move-Item -LiteralPath $safeBackup -Destination $safeCurrent
        }
        throw
    }
    if (Test-Path -LiteralPath $safeBackup -PathType Container) {
        if ($KeepPreviousPortable) {
            Write-Host "Previous portable app retained as requested: $safeBackup"
        }
        else {
            Remove-Item -LiteralPath $safeBackup -Recurse -Force
            Write-Host "Verified the new portable app and removed the temporary rollback backup."
        }
    }

    if ($Zip) {
        $archivePath = Join-Path $PortableParent "Video2Notes-portable-current.zip"
        $archiveHashPath = "$archivePath.sha256"
        if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
        if (Test-Path -LiteralPath $archiveHashPath) { Remove-Item -LiteralPath $archiveHashPath -Force }
        Compress-Archive -LiteralPath $safeCurrent -DestinationPath $archivePath -CompressionLevel Optimal
        $archiveHash = Get-Video2NotesFileSha256 -Path $archivePath
        [IO.File]::WriteAllText(
            $archiveHashPath,
            "$archiveHash  $([IO.Path]::GetFileName($archivePath))`n",
            [Text.UTF8Encoding]::new($false)
        )
        Write-Host "Portable ZIP: $archivePath"
    }

    $portableBytes = (Get-ChildItem -LiteralPath $safeCurrent -Recurse -File | Measure-Object -Property Length -Sum).Sum
    Write-Host ("Portable app is ready: {0} ({1:N1} MiB; profile={2}; runtime={3})" -f (Join-Path $safeCurrent "Video2Notes.exe"), ($portableBytes / 1MB), $EffectiveReleaseProfile, $RuntimeFlavor) -ForegroundColor Green
}
catch {
    if (Test-Path -LiteralPath $PortableStaging -PathType Container) {
        $safeFailedStaging = Assert-ManagedPortablePath $PortableStaging
        Remove-Item -LiteralPath $safeFailedStaging -Recurse -Force
    }
    throw
}
finally {
    Pop-Location
}
