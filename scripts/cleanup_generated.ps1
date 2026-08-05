[CmdletBinding()]
param(
    [switch]$Execute,
    [switch]$IncludePortableZip,
    # Use only after the matching GitHub Release assets and hashes have been
    # verified. Trusted catalog-entry and parts metadata remain available.
    [switch]$IncludePublishedArchives
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$RepoBoundary = $RepoRoot.TrimEnd("\") + "\"

function Resolve-RepoPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $fullPath = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $RelativePath))
    if (-not $fullPath.StartsWith($RepoBoundary, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup path escapes the repository: $fullPath"
    }
    return $fullPath
}

function Test-PathInside {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Boundary
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $fullBoundary = [System.IO.Path]::GetFullPath($Boundary).TrimEnd("\") + "\"
    return $fullPath.StartsWith($fullBoundary, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-TreeInventory {
    param([Parameter(Mandatory = $true)][string]$Root)

    $directories = [System.Collections.Generic.Queue[string]]::new()
    $reparsePoints = [System.Collections.Generic.List[System.IO.FileSystemInfo]]::new()
    $directories.Enqueue($Root)
    $bytes = [int64]0
    $files = [int64]0

    while ($directories.Count -gt 0) {
        $current = $directories.Dequeue()
        foreach ($entry in Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop) {
            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                [void]$reparsePoints.Add($entry)
                continue
            }
            if ($entry.PSIsContainer) {
                $directories.Enqueue($entry.FullName)
                continue
            }
            $bytes += [int64]$entry.Length
            $files += 1
        }
    }

    return [pscustomobject]@{
        Bytes = $bytes
        Files = $files
        ReparsePoints = $reparsePoints
    }
}

function Assert-ReparsePointsAreInternal {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()]$ReparsePoints,
        [Parameter(Mandatory = $true)][string]$CleanupRoot
    )

    foreach ($link in $ReparsePoints) {
        if (-not (Test-PathInside -Path $link.FullName -Boundary $CleanupRoot)) {
            throw "Reparse point is outside its cleanup root: $($link.FullName)"
        }

        $rawTargets = @($link.Target)
        if ($rawTargets.Count -eq 0 -or [string]::IsNullOrWhiteSpace([string]$rawTargets[0])) {
            throw "Cannot validate reparse target: $($link.FullName)"
        }

        foreach ($rawTarget in $rawTargets) {
            $targetPath = if ([System.IO.Path]::IsPathRooted([string]$rawTarget)) {
                [System.IO.Path]::GetFullPath([string]$rawTarget)
            }
            else {
                [System.IO.Path]::GetFullPath((Join-Path $link.DirectoryName ([string]$rawTarget)))
            }
            if (-not (Test-PathInside -Path $targetPath -Boundary $CleanupRoot)) {
                throw "Reparse target escapes its cleanup root: $($link.FullName) -> $targetPath"
            }
        }
    }
}

function Add-DirectoryTarget {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[object]]$Targets,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) {
        throw "Expected a directory cleanup target: $Path"
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Cleanup root must not be a reparse point: $Path"
    }

    $inventory = Get-TreeInventory -Root $Path
    Assert-ReparsePointsAreInternal -ReparsePoints $inventory.ReparsePoints -CleanupRoot $Path
    [void]$Targets.Add([pscustomobject]@{
        Kind = "directory"
        Path = $Path
        Reason = $Reason
        Bytes = [int64]$inventory.Bytes
        Files = [int64]$inventory.Files
        ReparsePoints = $inventory.ReparsePoints
    })
}

function Add-FileTarget {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[object]]$Targets,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer) {
        throw "Expected a file cleanup target: $Path"
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "File cleanup target must not be a reparse point: $Path"
    }
    [void]$Targets.Add([pscustomobject]@{
        Kind = "file"
        Path = $Path
        Reason = $Reason
        Bytes = [int64]$item.Length
        Files = [int64]1
        ReparsePoints = @()
    })
}

$targets = [System.Collections.Generic.List[object]]::new()

$fixedDirectoryTargets = @(
    @{ Relative = "apps\desktop\src-tauri\target"; Reason = "Rust and Tauri build output" },
    @{ Relative = "apps\desktop\src-tauri\resources\backend\_internal"; Reason = "generated frozen backend staging" },
    @{ Relative = "apps\desktop\src-tauri\resources\backend\tools"; Reason = "generated bundled media tools staging" },
    @{ Relative = "apps\desktop\dist"; Reason = "Vite production output" },
    @{ Relative = "artifacts\build"; Reason = "PyInstaller and release build output" },
    @{ Relative = "artifacts\verification"; Reason = "rebuildable verification workspaces and smoke output" },
    @{ Relative = "artifacts\scratch"; Reason = "temporary packaging probes" },
    @{ Relative = "tmp"; Reason = "temporary UI, PDF, and pipeline smoke output" },
    @{ Relative = ".mypy_cache"; Reason = "mypy cache" },
    @{ Relative = ".pytest_cache"; Reason = "pytest cache" },
    @{ Relative = ".ruff_cache"; Reason = "Ruff cache" }
)

foreach ($definition in $fixedDirectoryTargets) {
    Add-DirectoryTarget -Targets $targets -Path (Resolve-RepoPath $definition.Relative) -Reason $definition.Reason
}

$fixedFileTargets = @(
    @{ Relative = "apps\desktop\src-tauri\resources\backend\manifest.json"; Reason = "generated frozen backend manifest" },
    @{ Relative = "apps\desktop\src-tauri\resources\backend\video2notes.exe"; Reason = "generated frozen backend executable" },
    @{ Relative = ".coverage"; Reason = "coverage database" }
)

foreach ($definition in $fixedFileTargets) {
    Add-FileTarget -Targets $targets -Path (Resolve-RepoPath $definition.Relative) -Reason $definition.Reason
}

if ($IncludePortableZip) {
    Add-FileTarget -Targets $targets -Path (Resolve-RepoPath "artifacts\portable\Video2Notes-portable-current.zip") -Reason "rebuildable duplicate of portable/current"
    Add-FileTarget -Targets $targets -Path (Resolve-RepoPath "artifacts\portable\Video2Notes-portable-current.zip.sha256") -Reason "checksum for rebuildable portable ZIP"
}

if ($IncludePublishedArchives) {
    Add-DirectoryTarget `
        -Targets $targets `
        -Path (Resolve-RepoPath "artifacts\release") `
        -Reason "local copies of published release assets"

    $runtimePackRoot = Resolve-RepoPath "artifacts\runtime-packs"
    if (Test-Path -LiteralPath $runtimePackRoot) {
        foreach ($archive in Get-ChildItem -LiteralPath $runtimePackRoot -Recurse -File -Force) {
            if ($archive.Name -match "\.zip(?:\.part\d{3})?$") {
                Add-FileTarget `
                    -Targets $targets `
                    -Path $archive.FullName `
                    -Reason "published runtime archive; trusted JSON metadata is retained"
            }
        }
    }
}

$portableRoot = Resolve-RepoPath "artifacts\portable"
if (Test-Path -LiteralPath $portableRoot) {
    foreach ($backup in Get-ChildItem -LiteralPath $portableRoot -Force -Directory -Filter ".backup-*") {
        Add-DirectoryTarget -Targets $targets -Path $backup.FullName -Reason "superseded portable build backup"
    }
}

$pocRoot = Resolve-RepoPath "artifacts\poc"
if (Test-Path -LiteralPath $pocRoot) {
    foreach ($experiment in Get-ChildItem -LiteralPath $pocRoot -Force -Directory) {
        $venv = Join-Path $experiment.FullName "venv"
        Add-DirectoryTarget -Targets $targets -Path $venv -Reason "rebuildable proof-of-concept virtual environment"
    }
}

$benchmarksRoot = Resolve-RepoPath "artifacts\benchmarks"
if (Test-Path -LiteralPath $benchmarksRoot) {
    foreach ($benchmark in Get-ChildItem -LiteralPath $benchmarksRoot -Force -Directory) {
        foreach ($managed in Get-ChildItem -LiteralPath $benchmark.FullName -Force -Directory -Filter "managed-components-*") {
            $incomplete = Get-ChildItem -LiteralPath $managed.FullName -Force -Recurse -File -Filter "*.incomplete" -ErrorAction Stop | Select-Object -First 1
            if ($null -ne $incomplete) {
                Add-DirectoryTarget -Targets $targets -Path $managed.FullName -Reason "incomplete managed-component download staging"
            }
        }
    }
}

$targets = @($targets | Sort-Object Path -Unique)
$totalBytes = [int64]0
$totalFiles = [int64]0
$totalLinks = [int64]0
foreach ($target in $targets) {
    $totalBytes += [int64]$target.Bytes
    $totalFiles += [int64]$target.Files
    $totalLinks += [int64]@($target.ReparsePoints).Count
}

Write-Host "Video2Notes generated-data cleanup" -ForegroundColor Cyan
Write-Host ("Repository: {0}" -f $RepoRoot)
Write-Host ("Mode: {0}" -f $(if ($Execute) { "EXECUTE" } else { "DRY RUN" }))
Write-Host "Protected by design: root .venv, node_modules, artifacts/models, canonical benchmarks, and portable/current."
if (-not $IncludePortableZip) {
    Write-Host "Portable ZIP is protected unless -IncludePortableZip is supplied."
}
if (-not $IncludePublishedArchives) {
    Write-Host "Published release archives are protected unless -IncludePublishedArchives is supplied."
}
Write-Host ""

$targets | Select-Object @{Name = "GiB"; Expression = { "{0:N3}" -f ($_.Bytes / 1GB) } }, Files, @{Name = "Links"; Expression = { @($_.ReparsePoints).Count } }, Reason, Path | Format-Table -AutoSize
Write-Host ("Selected: {0} targets, {1:N3} GiB, {2:N0} files, {3:N0} internal reparse points." -f $targets.Count, ($totalBytes / 1GB), $totalFiles, $totalLinks)

if (-not $Execute) {
    Write-Host "Dry run only. Re-run with -Execute after reviewing every path above." -ForegroundColor Yellow
    exit 0
}

foreach ($target in $targets) {
    if ($target.Kind -eq "file") {
        Remove-Item -LiteralPath $target.Path -Force -ErrorAction Stop
        continue
    }

    foreach ($link in (@($target.ReparsePoints) | Sort-Object { $_.FullName.Length } -Descending)) {
        Remove-Item -LiteralPath $link.FullName -Force -ErrorAction Stop
    }
    Remove-Item -LiteralPath $target.Path -Recurse -Force -ErrorAction Stop
}

Write-Host ("Removed {0:N3} GiB of validated generated data. Deleted paths are recoverable only by rebuilding or rerunning the corresponding checks." -f ($totalBytes / 1GB)) -ForegroundColor Green
