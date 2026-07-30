[CmdletBinding()]
param(
    # Reuse the already frozen canonical sidecar for faster UI/Rust iteration.
    # Its complete manifest is still verified before it enters the portable tree.
    [switch]$ReuseSidecar,
    [switch]$SkipSidecarSmoke,
    [switch]$Zip,
    [string]$FfmpegDirectory = "",
    [string]$FfmpegLicensePath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$DesktopRoot = Join-Path $RepoRoot "apps\desktop"
$TauriRoot = Join-Path $DesktopRoot "src-tauri"
$CanonicalBackendRoot = Join-Path $TauriRoot "resources\backend"
$ReleaseExecutable = Join-Path $TauriRoot "target\release\video2notes-desktop.exe"
$PortableParent = Join-Path $RepoRoot "artifacts\portable"
$PortableCurrent = Join-Path $PortableParent "current"
$PortableStaging = Join-Path $PortableParent (".staging-" + [guid]::NewGuid().ToString("N"))
$PortableOld = Join-Path $PortableParent (".old-" + [guid]::NewGuid().ToString("N"))
$PortableMarkerName = ".video2notes-portable.json"

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
    if ($leaf -ne "current" -and -not $leaf.StartsWith(".staging-") -and -not $leaf.StartsWith(".old-")) {
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
    param([string]$BackendRoot)

    $manifestPath = Join-Path $BackendRoot "manifest.json"
    Require-File $manifestPath "Frozen backend manifest"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema -ne 1 -or -not $manifest.files) {
        throw "The frozen backend manifest is unsupported or empty."
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
        $actualHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
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
}

function Assert-NoPrivatePayload {
    param([string]$PortableRoot)

    $forbidden = @(
        Get-ChildItem -LiteralPath $PortableRoot -Recurse -File |
            Where-Object {
                $relative = $_.FullName.Substring($PortableRoot.Length).TrimStart("\") -replace "\\", "/"
                $extension = $_.Extension.ToLowerInvariant()
                $relative -match "(?i)(^|/)(\.venv|node_modules|data|runs|config|faster[_-]?whisper|paddle(?:ocr|paddle)?|huggingface|modelscope|torch)(/|$)" -or
                $_.Name -match "(?i)(^|\.)(cookies?\.txt|keyring\.json)$" -or
                $_.Name -match "(?i)^\.env(?:\.|$)" -or
                $extension -in @(
                    ".cookies", ".sqlite", ".sqlite3", ".db", ".db-shm", ".db-wal",
                    ".safetensors", ".gguf", ".onnx", ".pt"
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
    param([string]$PortableRoot)

    $allowedTopLevel = @(
        "backend",
        "demo",
        "licenses",
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

    Assert-BackendManifest (Join-Path $PortableRoot "backend")
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
                "{0}  {1}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $relative
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
        $actualHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash
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
        $sidecarArguments = @()
        if ($SkipSidecarSmoke) { $sidecarArguments += "-SkipSmoke" }
        if ($FfmpegDirectory) { $sidecarArguments += @("-FfmpegDirectory", $FfmpegDirectory) }
        if ($FfmpegLicensePath) { $sidecarArguments += @("-FfmpegLicensePath", $FfmpegLicensePath) }
        & (Join-Path $PSScriptRoot "build_sidecar.ps1") @sidecarArguments
        Assert-LastExitCode "Building and smoke-testing the self-contained backend sidecar"
    }

    Require-File (Join-Path $CanonicalBackendRoot "video2notes.exe") "Frozen backend executable"
    Require-File (Join-Path $CanonicalBackendRoot "tools\ffmpeg.exe") "Bundled ffmpeg.exe"
    Require-File (Join-Path $CanonicalBackendRoot "tools\ffprobe.exe") "Bundled ffprobe.exe"
    Assert-BackendManifest $CanonicalBackendRoot

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

    Assert-BackendManifest (Join-Path $safeStaging "backend")
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
        schema = 1
        product = "Video2Notes"
        version = [string]$tauriConfiguration.version
        git_describe = $gitDescribe
        commit = $gitCommit
        dirty = $gitDirty
        built_at_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        target_triple = $targetTriple
        portable = $true
        sidecar_reused = [bool]$ReuseSidecar
        executable_sha256 = (Get-FileHash -LiteralPath (Join-Path $safeStaging "Video2Notes.exe") -Algorithm SHA256).Hash
        sidecar_manifest_sha256 = (Get-FileHash -LiteralPath (Join-Path $safeStaging "backend\manifest.json") -Algorithm SHA256).Hash
    }
    $buildInfo | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $safeStaging "BUILD_INFO.json") -Encoding utf8
    [ordered]@{
        schema = 1
        product = "Video2Notes"
        portable = $true
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $safeStaging $PortableMarkerName) -Encoding utf8

    @"
Video2Notes 免安装调试版

直接双击 Video2Notes.exe。backend、demo、licenses 三个目录必须与主程序一起保留。
本版本不会创建安装项或卸载项。任务、配置和 WebView 状态默认保存在 Windows 用户 AppData，
因此覆盖 current 程序目录不会删除既有任务。API 密钥仍保存在 Windows Credential Manager。

完整构建：.\scripts\build_portable.ps1
快速复用后端：.\scripts\build_portable.ps1 -ReuseSidecar
"@ | Set-Content -LiteralPath (Join-Path $safeStaging "PORTABLE_README.txt") -Encoding utf8
    Write-Sha256Sums $safeStaging
    Assert-PortableLayout $safeStaging
    Assert-PortableChecksums $safeStaging

    Assert-PortableNotRunning $PortableCurrent
    $safeCurrent = Assert-ManagedPortablePath $PortableCurrent
    $safeOld = Assert-ManagedPortablePath $PortableOld
    if (Test-Path -LiteralPath $safeCurrent -PathType Container) {
        Assert-ExistingPortableMarker $safeCurrent
        Assert-PortableLayout $safeCurrent
        Assert-PortableChecksums $safeCurrent
        Assert-NoPrivatePayload $safeCurrent
        Move-Item -LiteralPath $safeCurrent -Destination $safeOld
    }
    try {
        Move-Item -LiteralPath $safeStaging -Destination $safeCurrent
    }
    catch {
        if (Test-Path -LiteralPath $safeOld -PathType Container) {
            Move-Item -LiteralPath $safeOld -Destination $safeCurrent
        }
        throw
    }
    if (Test-Path -LiteralPath $safeOld -PathType Container) {
        Assert-ExistingPortableMarker $safeOld
        Assert-PortableLayout $safeOld
        Assert-PortableChecksums $safeOld
        Remove-Item -LiteralPath $safeOld -Recurse -Force
    }

    if ($Zip) {
        $archivePath = Join-Path $PortableParent "Video2Notes-portable-current.zip"
        $archiveHashPath = "$archivePath.sha256"
        if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
        if (Test-Path -LiteralPath $archiveHashPath) { Remove-Item -LiteralPath $archiveHashPath -Force }
        Compress-Archive -LiteralPath $safeCurrent -DestinationPath $archivePath -CompressionLevel Optimal
        $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
        [IO.File]::WriteAllText(
            $archiveHashPath,
            "$archiveHash  $([IO.Path]::GetFileName($archivePath))`n",
            [Text.UTF8Encoding]::new($false)
        )
        Write-Host "Portable ZIP: $archivePath"
    }

    $portableBytes = (Get-ChildItem -LiteralPath $safeCurrent -Recurse -File | Measure-Object -Property Length -Sum).Sum
    Write-Host ("Portable app is ready: {0} ({1:N1} MiB)" -f (Join-Path $safeCurrent "Video2Notes.exe"), ($portableBytes / 1MB)) -ForegroundColor Green
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
