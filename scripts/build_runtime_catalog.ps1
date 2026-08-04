[CmdletBinding()]
param(
    [string]$CatalogEntryDirectory = "",
    [string]$PartManifestDirectory = "",
    [string]$OutputPath = "",
    [string]$CatalogId = "video2notes-official-runtime-packs",
    [string]$GitHubRepository = "LainElaina/Video2Notes",
    [Parameter(Mandatory = $true)]
    [string]$ReleaseTag,
    [string]$PythonPath = "",
    [switch]$Overwrite,
    [switch]$SkipArtifactVerification,
    [switch]$SkipModelValidation
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "packaging_common.ps1")

function Require-Directory {
    param([string]$Path, [string]$Purpose)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Purpose was not found at '$Path'."
    }
}

function Assert-SafeFileName {
    param([string]$Value, [string]$Purpose)
    if (
        -not $Value -or
        $Value -ne [IO.Path]::GetFileName($Value) -or
        $Value -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$"
    ) {
        throw "$Purpose contains an unsafe file name: '$Value'."
    }
}

function Assert-Sha256 {
    param([string]$Value, [string]$Purpose)
    if ($Value -cnotmatch "^[0-9a-f]{64}$") {
        throw "$Purpose contains an invalid lowercase SHA-256 value."
    }
}

function Write-JsonFile {
    param([string]$Path, [object]$Value)
    $json = ($Value | ConvertTo-Json -Depth 16) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($Path, "$json`n", [Text.UTF8Encoding]::new($false))
}

function Resolve-PythonExecutable {
    param([string]$RequestedPath)
    if ($RequestedPath) {
        $resolved = [IO.Path]::GetFullPath($RequestedPath)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "Python executable was not found at '$resolved'."
        }
        return $resolved
    }

    $bundledCandidate = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $bundledCandidate -PathType Leaf) {
        return $bundledCandidate
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Python is required for runtime catalog model validation."
    }
    return $command.Source
}

function Invoke-CatalogModelValidation {
    param(
        [string]$Python,
        [string]$CatalogPath,
        [int]$ExpectedReleaseCount
    )

    $validationScript = @'
import json
import os
from pathlib import Path

from video2notes.components.runtime_catalog import RuntimePackageCatalog

path = Path(os.environ["VIDEO2NOTES_CATALOG_VALIDATION_PATH"])
expected_count = int(os.environ["VIDEO2NOTES_CATALOG_EXPECTED_RELEASES"])
payload = json.loads(path.read_text(encoding="utf-8"))
catalog = RuntimePackageCatalog.model_validate(payload)
if len(catalog.releases) != expected_count:
    raise RuntimeError(
        f"runtime catalog contains {len(catalog.releases)} releases, expected {expected_count}"
    )
with path.open("w", encoding="utf-8", newline="\n") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
print(
    f"Validated runtime catalog: {len(catalog.releases)} releases, "
    f"{catalog.target_triple}, protocol {catalog.runtime_protocol_version}"
)
'@

    $previousPythonPath = $env:PYTHONPATH
    $previousCatalogPath = $env:VIDEO2NOTES_CATALOG_VALIDATION_PATH
    $previousExpectedCount = $env:VIDEO2NOTES_CATALOG_EXPECTED_RELEASES
    try {
        $sourceRoot = Join-Path $RepoRoot "src"
        $env:PYTHONPATH = if ($previousPythonPath) {
            $sourceRoot + [IO.Path]::PathSeparator + $previousPythonPath
        }
        else {
            $sourceRoot
        }
        $env:VIDEO2NOTES_CATALOG_VALIDATION_PATH = $CatalogPath
        $env:VIDEO2NOTES_CATALOG_EXPECTED_RELEASES = [string]$ExpectedReleaseCount
        $validationScript | & $Python -
        if ($LASTEXITCODE -ne 0) {
            throw "RuntimePackageCatalog model validation failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
        $env:VIDEO2NOTES_CATALOG_VALIDATION_PATH = $previousCatalogPath
        $env:VIDEO2NOTES_CATALOG_EXPECTED_RELEASES = $previousExpectedCount
    }
}

if (-not $CatalogEntryDirectory) {
    $CatalogEntryDirectory = Join-Path $RepoRoot "artifacts\runtime-packs"
}
if (-not $PartManifestDirectory) {
    $PartManifestDirectory = $CatalogEntryDirectory
}
if (-not $OutputPath) {
    $OutputPath = Join-Path $RepoRoot "packaging\runtime-packs\catalog.json"
}

$resolvedEntryDirectory = [IO.Path]::GetFullPath($CatalogEntryDirectory)
$resolvedPartDirectory = [IO.Path]::GetFullPath($PartManifestDirectory)
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
Require-Directory $resolvedEntryDirectory "Runtime catalog entry directory"
Require-Directory $resolvedPartDirectory "Runtime archive part manifest directory"

if (-not $CatalogId -or $CatalogId.Length -gt 160) {
    throw "CatalogId must contain between 1 and 160 characters."
}
if ($GitHubRepository -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
    throw "GitHubRepository must use the 'owner/repository' form."
}
if ($ReleaseTag -notmatch "^v[A-Za-z0-9][A-Za-z0-9._-]{0,126}$") {
    throw "ReleaseTag must be a safe GitHub release tag beginning with 'v'."
}
$expectedVersion = $ReleaseTag.Substring(1)
$releaseBaseUrl = "https://github.com/$GitHubRepository/releases/download/$ReleaseTag"

$entryFiles = @(
    Get-ChildItem -LiteralPath $resolvedEntryDirectory `
        -Recurse `
        -File `
        -Filter "*.zip.catalog-entry.json" |
        Sort-Object FullName
)
if ($entryFiles.Count -eq 0) {
    throw "No runtime pack catalog entries were found in '$resolvedEntryDirectory'."
}

$partManifestFiles = @(
    Get-ChildItem -LiteralPath $resolvedPartDirectory `
        -Recurse `
        -File `
        -Filter "*.zip.parts.json" |
        Sort-Object FullName
)
$partManifestByArchive = @{}
foreach ($partManifestFile in $partManifestFiles) {
    $partManifest = Get-Content -LiteralPath $partManifestFile.FullName -Raw |
        ConvertFrom-Json
    if ([int]$partManifest.schema -ne 1 -or $null -eq $partManifest.archive) {
        throw "Runtime archive part manifest '$($partManifestFile.FullName)' is unsupported."
    }
    $archiveFileName = [string]$partManifest.archive.file_name
    Assert-SafeFileName $archiveFileName "Runtime archive part manifest"
    $archiveKey = $archiveFileName.ToLowerInvariant()
    if ($partManifestByArchive.ContainsKey($archiveKey)) {
        throw "More than one part manifest targets '$archiveFileName'."
    }
    $partManifestByArchive[$archiveKey] = [pscustomobject]@{
        Path = $partManifestFile.FullName
        Directory = $partManifestFile.DirectoryName
        Manifest = $partManifest
    }
}

$usedPartManifestKeys = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase
)
$releases = @()
foreach ($entryFile in $entryFiles) {
    $release = Get-Content -LiteralPath $entryFile.FullName -Raw | ConvertFrom-Json
    if ([int]$release.schema -ne 1 -or $null -eq $release.archive) {
        throw "Runtime catalog entry '$($entryFile.FullName)' is unsupported."
    }
    if ([string]$release.version -cne $expectedVersion) {
        throw "Runtime package '$($release.package_id)' version '$($release.version)' does not match release tag '$ReleaseTag'."
    }

    $archiveFileName = [string]$release.archive.file_name
    Assert-SafeFileName $archiveFileName "Runtime catalog entry"
    if ([IO.Path]::GetExtension($archiveFileName) -ne ".zip") {
        throw "Runtime catalog entry archive '$archiveFileName' must be a ZIP file."
    }
    Assert-Sha256 ([string]$release.archive.sha256) "Runtime catalog entry archive"
    $archivePath = Join-Path $entryFile.DirectoryName $archiveFileName
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
        throw "Runtime archive was not found at '$archivePath'."
    }

    $archiveKey = $archiveFileName.ToLowerInvariant()
    if ($partManifestByArchive.ContainsKey($archiveKey)) {
        $partManifestRecord = $partManifestByArchive[$archiveKey]
        [void]$usedPartManifestKeys.Add($archiveKey)
        $partManifest = $partManifestRecord.Manifest
        if (
            [string]$partManifest.archive.file_name -cne $archiveFileName -or
            [long]$partManifest.archive.size_bytes -ne [long]$release.archive.size_bytes -or
            [string]$partManifest.archive.sha256 -cne [string]$release.archive.sha256 -or
            [bool]$partManifest.reassembly.verified -ne $true -or
            [long]$partManifest.reassembly.size_bytes -ne [long]$release.archive.size_bytes -or
            [string]$partManifest.reassembly.sha256 -cne [string]$release.archive.sha256
        ) {
            throw "Runtime archive part manifest does not match catalog entry '$archiveFileName'."
        }

        $parts = @($partManifest.parts | Sort-Object { [int]$_.index })
        if ($parts.Count -lt 2 -or [int]$partManifest.part_count -ne $parts.Count) {
            throw "Multipart runtime archive '$archiveFileName' must contain at least two parts."
        }
        $publishedParts = @()
        $partBytes = [long]0
        $partNames = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::OrdinalIgnoreCase
        )
        for ($offset = 0; $offset -lt $parts.Count; $offset++) {
            $part = $parts[$offset]
            $expectedIndex = $offset + 1
            if ([int]$part.index -ne $expectedIndex) {
                throw "Multipart runtime archive '$archiveFileName' has non-contiguous indexes."
            }
            $partFileName = [string]$part.file_name
            Assert-SafeFileName $partFileName "Runtime archive part $expectedIndex"
            if (-not $partNames.Add($partFileName)) {
                throw "Multipart runtime archive '$archiveFileName' repeats '$partFileName'."
            }
            Assert-Sha256 ([string]$part.sha256) "Runtime archive part $expectedIndex"
            if ([long]$part.size_bytes -lt 1) {
                throw "Runtime archive part $expectedIndex has an invalid size."
            }
            $partBytes += [long]$part.size_bytes
            $publishedParts += [ordered]@{
                file_name = $partFileName
                source_url = "$releaseBaseUrl/$([Uri]::EscapeDataString($partFileName))"
                size_bytes = [long]$part.size_bytes
                sha256 = [string]$part.sha256
            }
        }
        if ($partBytes -ne [long]$release.archive.size_bytes) {
            throw "Multipart runtime archive '$archiveFileName' part sizes do not match the ZIP."
        }

        if (-not $SkipArtifactVerification) {
            & (Join-Path $PSScriptRoot "split_release_archive.ps1") `
                -Mode Verify `
                -ArchivePath $archivePath `
                -OutputDirectory $partManifestRecord.Directory
        }
        $release.archive.source_url = $null
        $release.archive.offline_only = $false
        if ($null -eq $release.archive.PSObject.Properties["parts"]) {
            $release.archive | Add-Member -NotePropertyName parts -NotePropertyValue @($publishedParts)
        }
        else {
            $release.archive.parts = @($publishedParts)
        }
    }
    else {
        if (-not $SkipArtifactVerification) {
            $archiveItem = Get-Item -LiteralPath $archivePath
            if ([long]$archiveItem.Length -ne [long]$release.archive.size_bytes) {
                throw "Runtime archive size does not match '$($entryFile.FullName)'."
            }
            $archiveHash = (Get-Video2NotesFileSha256 -Path $archivePath).ToLowerInvariant()
            if ($archiveHash -cne [string]$release.archive.sha256) {
                throw "Runtime archive SHA-256 does not match '$($entryFile.FullName)'."
            }
        }
        $release.archive.source_url = "$releaseBaseUrl/$([Uri]::EscapeDataString($archiveFileName))"
        $release.archive.offline_only = $false
        if ($null -ne $release.archive.PSObject.Properties["parts"]) {
            $release.archive.PSObject.Properties.Remove("parts")
        }
    }
    $releases += $release
}

$unusedPartManifests = @(
    $partManifestByArchive.Keys |
        Where-Object { -not $usedPartManifestKeys.Contains($_) }
)
if ($unusedPartManifests.Count -gt 0) {
    throw "Part manifests do not match a runtime catalog entry: $($unusedPartManifests -join ', ')."
}

$releases = @($releases | Sort-Object package_id, version)
$identities = @($releases | ForEach-Object { "$($_.package_id)`0$($_.version)" })
if (@($identities | Select-Object -Unique).Count -ne $identities.Count) {
    throw "Runtime catalog contains duplicate package/version identities."
}
$targetTriples = @($releases.target_triple | Select-Object -Unique)
$protocolVersions = @($releases.runtime_protocol_version | Select-Object -Unique)
if ($targetTriples.Count -ne 1 -or $protocolVersions.Count -ne 1) {
    throw "Runtime catalog entries must use one target triple and protocol version."
}

$catalog = [ordered]@{
    schema = 1
    catalog_id = $CatalogId
    target_triple = [string]$targetTriples[0]
    runtime_protocol_version = [int]$protocolVersions[0]
    packages = @($releases)
}

$outputDirectory = Split-Path -Parent $resolvedOutput
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
if (Test-Path -LiteralPath $resolvedOutput -PathType Leaf) {
    if (-not $Overwrite) {
        throw "Runtime catalog already exists at '$resolvedOutput'. Pass -Overwrite to replace it."
    }
}
$temporaryPath = Join-Path $outputDirectory (
    "." + [IO.Path]::GetFileName($resolvedOutput) + "." + [Guid]::NewGuid().ToString("N") + ".tmp"
)
$backupPath = $temporaryPath + ".backup"
try {
    Write-JsonFile -Path $temporaryPath -Value $catalog
    if (-not $SkipModelValidation) {
        $python = Resolve-PythonExecutable -RequestedPath $PythonPath
        Invoke-CatalogModelValidation `
            -Python $python `
            -CatalogPath $temporaryPath `
            -ExpectedReleaseCount $releases.Count
    }

    if (Test-Path -LiteralPath $resolvedOutput -PathType Leaf) {
        [IO.File]::Replace($temporaryPath, $resolvedOutput, $backupPath)
        Remove-Item -LiteralPath $backupPath -Force
    }
    else {
        [IO.File]::Move($temporaryPath, $resolvedOutput)
    }
}
finally {
    if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
    if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
        Remove-Item -LiteralPath $backupPath -Force
    }
}

Write-Host ("Runtime catalog ready: {0} ({1} releases; GitHub tag {2})" -f $resolvedOutput, $releases.Count, $ReleaseTag) -ForegroundColor Green
