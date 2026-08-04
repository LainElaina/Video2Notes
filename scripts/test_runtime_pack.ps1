[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,
    [Parameter(Mandatory = $true)]
    [string]$CatalogEntryPath,
    # Structural packaging tests can use a placeholder entrypoint. Production
    # builds never pass this switch and must start the frozen worker.
    [switch]$SkipWorkerProbe
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "packaging_common.ps1")

function Require-File {
    param([string]$Path, [string]$Purpose)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Purpose was not found at '$Path'."
    }
}

function Assert-SafeRelativePath {
    param([string]$Value, [string]$Field)
    $normalized = ([string]$Value) -replace "\\", "/"
    $segments = @($normalized.Split("/", [StringSplitOptions]::RemoveEmptyEntries))
    if (
        -not $normalized -or
        [IO.Path]::IsPathRooted($normalized) -or
        $normalized.Contains(":") -or
        $segments.Count -eq 0 -or
        $segments.Count -ne @($normalized.Split("/")).Count -or
        @($segments | Where-Object { $_ -in @(".", "..") }).Count -gt 0
    ) {
        throw "Runtime pack $Field contains an unsafe path: '$Value'."
    }
    return $normalized
}

$resolvedArchive = [IO.Path]::GetFullPath($ArchivePath)
$resolvedCatalogEntry = [IO.Path]::GetFullPath($CatalogEntryPath)
Require-File $resolvedArchive "Runtime pack archive"
Require-File $resolvedCatalogEntry "Runtime pack catalog entry"

$catalogEntry = Get-Content -LiteralPath $resolvedCatalogEntry -Raw | ConvertFrom-Json
if ($catalogEntry.schema -ne 1 -or -not $catalogEntry.package_id -or -not $catalogEntry.files) {
    throw "Runtime pack catalog entry is unsupported or incomplete."
}
if ($catalogEntry.archive.file_name -ne [IO.Path]::GetFileName($resolvedArchive)) {
    throw "Runtime pack archive filename does not match its catalog entry."
}
$archiveItem = Get-Item -LiteralPath $resolvedArchive
if ([long]$catalogEntry.archive.size_bytes -ne [long]$archiveItem.Length) {
    throw "Runtime pack archive size does not match its catalog entry."
}
$actualArchiveHash = (Get-Video2NotesFileSha256 -Path $resolvedArchive).ToLowerInvariant()
if ($actualArchiveHash -ne ([string]$catalogEntry.archive.sha256).ToLowerInvariant()) {
    throw "Runtime pack archive SHA-256 does not match its catalog entry."
}
if ($catalogEntry.archive.source_url -and -not ([string]$catalogEntry.archive.source_url).StartsWith("https://", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Published runtime pack source_url must use HTTPS."
}

$temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("video2notes-runtime-pack-" + [Guid]::NewGuid().ToString("N"))
$resolvedTemporary = [IO.Path]::GetFullPath($temporaryRoot)
if (
    -not $resolvedTemporary.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase) -or
    -not ([IO.Path]::GetFileName($resolvedTemporary)).StartsWith("video2notes-runtime-pack-")
) {
    throw "Runtime pack validation directory escaped the Windows temporary directory."
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($resolvedArchive)
try {
    $archivePaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($zipEntry in $archive.Entries) {
        if (-not $zipEntry.Name -and $zipEntry.FullName.EndsWith("/")) {
            continue
        }
        $relativePath = Assert-SafeRelativePath ([string]$zipEntry.FullName) "archive entry"
        if (-not $archivePaths.Add($relativePath)) {
            throw "Runtime pack archive contains duplicate path '$relativePath'."
        }
        $unixFileType = (($zipEntry.ExternalAttributes -shr 16) -band 0xF000)
        $windowsAttributes = ($zipEntry.ExternalAttributes -band 0xFFFF)
        if (
            $unixFileType -eq 0xA000 -or
            ($windowsAttributes -band [int][IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "Runtime pack archive contains a symbolic link or reparse point: '$relativePath'."
        }
    }
}
finally {
    $archive.Dispose()
}

try {
    New-Item -ItemType Directory -Path $resolvedTemporary | Out-Null
    [IO.Compression.ZipFile]::ExtractToDirectory($resolvedArchive, $resolvedTemporary)
    $manifestPath = Join-Path $resolvedTemporary "runtime-package.json"
    Require-File $manifestPath "Runtime pack internal manifest"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    foreach ($field in @("package_id", "version", "target_triple", "runtime_protocol_version")) {
        if ([string]$manifest.$field -ne [string]$catalogEntry.$field) {
            throw "Runtime pack internal manifest field '$field' differs from its catalog entry."
        }
    }
    if ($manifest.schema -ne 1 -or $manifest.user_model_weights_included -ne $false) {
        throw "Runtime pack internal manifest does not declare the supported schema and model-weight boundary."
    }
    $manifestPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $manifestBytes = [long]0
    foreach ($file in @($manifest.files)) {
        $relativePath = Assert-SafeRelativePath ([string]$file.relative_path) "manifest file"
        if ($relativePath -eq "runtime-package.json") {
            throw "Runtime pack internal manifest must not attempt to hash itself."
        }
        if (-not $manifestPaths.Add($relativePath)) {
            throw "Runtime pack internal manifest contains duplicate file '$relativePath'."
        }
        $candidate = Join-Path $resolvedTemporary ($relativePath -replace "/", "\")
        Require-File $candidate "Runtime pack manifest file"
        $item = Get-Item -LiteralPath $candidate
        if ([long]$item.Length -ne [long]$file.size_bytes) {
            throw "Runtime pack manifest size mismatch for '$relativePath'."
        }
        $actualHash = (Get-Video2NotesFileSha256 -Path $candidate).ToLowerInvariant()
        if ($actualHash -ne ([string]$file.sha256).ToLowerInvariant()) {
            throw "Runtime pack manifest SHA-256 mismatch for '$relativePath'."
        }
        $manifestBytes += [long]$item.Length
    }
    if ($manifestBytes -ne [long]$manifest.payload_size_bytes) {
        throw "Runtime pack manifest payload size does not match its file list."
    }

    $expectedPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $installedBytes = [long]0
    foreach ($file in @($catalogEntry.files)) {
        $relativePath = Assert-SafeRelativePath ([string]$file.relative_path) "catalog file"
        if (-not $expectedPaths.Add($relativePath)) {
            throw "Runtime pack catalog contains duplicate file '$relativePath'."
        }
        $candidate = Join-Path $resolvedTemporary ($relativePath -replace "/", "\")
        Require-File $candidate "Runtime pack catalog file"
        $item = Get-Item -LiteralPath $candidate
        if ([long]$item.Length -ne [long]$file.size_bytes) {
            throw "Runtime pack installed size mismatch for '$relativePath'."
        }
        $actualHash = (Get-Video2NotesFileSha256 -Path $candidate).ToLowerInvariant()
        if ($actualHash -ne ([string]$file.sha256).ToLowerInvariant()) {
            throw "Runtime pack installed SHA-256 mismatch for '$relativePath'."
        }
        $installedBytes += [long]$item.Length
    }
    if ($installedBytes -ne [long]$catalogEntry.installed_size_bytes) {
        throw "Runtime pack total installed size does not match its catalog entry."
    }
    $catalogPayloadPaths = @(
        $catalogEntry.files |
            Where-Object { $_.relative_path -ne "runtime-package.json" } |
            ForEach-Object { [string]$_.relative_path }
    )
    if (
        $catalogPayloadPaths.Count -ne $manifestPaths.Count -or
        @($catalogPayloadPaths | Where-Object { -not $manifestPaths.Contains($_) }).Count -gt 0
    ) {
        throw "Runtime pack internal manifest and trusted catalog describe different payload files."
    }

    $unexpectedFiles = @(
        Get-ChildItem -LiteralPath $resolvedTemporary -Recurse -File |
            Where-Object {
                $prefix = $resolvedTemporary.TrimEnd("\") + "\"
                $relative = $_.FullName.Substring($prefix.Length) -replace "\\", "/"
                -not $expectedPaths.Contains($relative)
            }
    )
    if ($unexpectedFiles.Count -gt 0) {
        throw "Runtime pack contains files outside its trusted catalog: $($unexpectedFiles.FullName -join ', ')."
    }

    foreach ($capability in @($catalogEntry.capabilities)) {
        if ($capability.transport -notin @("worker", "executable")) {
            throw "Runtime capability '$($capability.capability_id)' uses an unsupported transport."
        }
        $entrypoint = Assert-SafeRelativePath ([string]$capability.entrypoint) "entrypoint"
        Require-File (Join-Path $resolvedTemporary ($entrypoint -replace "/", "\")) "Runtime capability entrypoint"
        if ([int]$capability.protocol_version -ne [int]$catalogEntry.runtime_protocol_version) {
            throw "Runtime capability '$($capability.capability_id)' has an incompatible protocol version."
        }
    }

    if (-not $SkipWorkerProbe) {
        $workerEntrypoints = @(
            $catalogEntry.capabilities |
                Where-Object { $_.transport -eq "worker" } |
                ForEach-Object { [string]$_.entrypoint } |
                Select-Object -Unique
        )
        foreach ($entrypoint in $workerEntrypoints) {
            $workerPath = Join-Path $resolvedTemporary ($entrypoint -replace "/", "\")
            $probeOutput = (& $workerPath probe --package-root $resolvedTemporary 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) {
                throw "Runtime worker probe failed for '$entrypoint' with exit code $LASTEXITCODE."
            }
            try {
                $probe = $probeOutput | ConvertFrom-Json
            }
            catch {
                throw "Runtime worker probe for '$entrypoint' did not return valid JSON."
            }
            if (
                [string]$probe.package_id -ne [string]$catalogEntry.package_id -or
                [string]$probe.package_version -ne [string]$catalogEntry.version
            ) {
                throw "Runtime worker probe identity differs from its trusted catalog entry."
            }
            $expectedCapabilities = @(
                $catalogEntry.capabilities | ForEach-Object { [string]$_.capability_id } | Sort-Object
            )
            $actualCapabilities = @(
                $probe.capabilities | ForEach-Object { [string]$_ } | Sort-Object
            )
            if (($expectedCapabilities -join "`n") -ne ($actualCapabilities -join "`n")) {
                throw "Runtime worker probe capabilities differ from its trusted catalog entry."
            }
        }
    }

    Write-Host "Runtime pack verified: $($catalogEntry.package_id) $($catalogEntry.version)" -ForegroundColor Green
}
finally {
    if (Test-Path -LiteralPath $resolvedTemporary -PathType Container) {
        Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
    }
}
