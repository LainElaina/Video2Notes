[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RecipePath,
    [Parameter(Mandatory = $true)]
    [string]$PayloadRoot,
    [string]$OutputDirectory = "",
    [string]$SourceUrl = "",
    [switch]$Overwrite,
    [switch]$SkipValidation
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "packaging_common.ps1")

function Require-File {
    param([string]$Path, [string]$Purpose)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Purpose was not found at '$Path'."
    }
}

function Assert-SafePackageToken {
    param([string]$Value, [string]$Field)
    if (-not $Value -or $Value -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$") {
        throw "Runtime pack $Field '$Value' is invalid."
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
        throw "Runtime pack $Field contains an unsafe relative path: '$Value'."
    }
    return $normalized
}

function ConvertTo-PackageRelativePath {
    param([string]$Root, [string]$Path)
    $rootPrefix = [IO.Path]::GetFullPath($Root).TrimEnd("\") + "\"
    $resolved = [IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Runtime pack payload escaped its declared root: '$resolved'."
    }
    return (Assert-SafeRelativePath $resolved.Substring($rootPrefix.Length) "file")
}

function Write-JsonFile {
    param([string]$Path, [object]$Value)
    $json = $Value | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText($Path, "$json`n", [Text.UTF8Encoding]::new($false))
}

$resolvedRecipe = [IO.Path]::GetFullPath($RecipePath)
$resolvedPayload = [IO.Path]::GetFullPath($PayloadRoot).TrimEnd("\")
Require-File $resolvedRecipe "Runtime pack recipe"
if (-not (Test-Path -LiteralPath $resolvedPayload -PathType Container)) {
    throw "Runtime pack payload directory was not found at '$resolvedPayload'."
}
if (((Get-Item -LiteralPath $resolvedPayload).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Runtime pack payload root must not be a junction or symbolic link."
}

$recipe = Get-Content -LiteralPath $resolvedRecipe -Raw | ConvertFrom-Json
if ($recipe.schema -ne 1) {
    throw "Runtime pack recipe schema '$($recipe.schema)' is unsupported."
}
Assert-SafePackageToken ([string]$recipe.package_id) "package_id"
Assert-SafePackageToken ([string]$recipe.version) "version"
Assert-SafePackageToken ([string]$recipe.target_triple) "target_triple"
if ([int]$recipe.runtime_protocol_version -lt 1) {
    throw "Runtime pack runtime_protocol_version must be a positive integer."
}
if (-not $recipe.display_name -or -not $recipe.capabilities) {
    throw "Runtime pack recipe must declare display_name and at least one capability."
}
if (
    -not $recipe.upstream_sources -or
    @(
        $recipe.upstream_sources |
            Where-Object { -not ([string]$_).StartsWith("https://", [StringComparison]::OrdinalIgnoreCase) }
    ).Count -gt 0
) {
    throw "Runtime pack recipe must disclose at least one HTTPS upstream source."
}
if ($SourceUrl -and -not $SourceUrl.StartsWith("https://", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Published runtime pack SourceUrl must use HTTPS. Leave it empty for an offline-only pack."
}

$capabilityIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($capability in @($recipe.capabilities)) {
    Assert-SafePackageToken ([string]$capability.capability_id) "capability_id"
    Assert-SafePackageToken ([string]$capability.engine_id) "engine_id"
    if (-not $capabilityIds.Add([string]$capability.capability_id)) {
        throw "Runtime pack recipe declares capability '$($capability.capability_id)' more than once."
    }
    if ([int]$capability.protocol_version -ne [int]$recipe.runtime_protocol_version) {
        throw "Capability '$($capability.capability_id)' uses a different protocol version."
    }
    if ($capability.transport -notin @("worker", "executable")) {
        throw "Capability '$($capability.capability_id)' has an unsupported transport."
    }
    $entrypoint = Assert-SafeRelativePath ([string]$capability.entrypoint) "entrypoint"
    if ([IO.Path]::GetExtension($entrypoint) -ne ".exe") {
        throw "Capability '$($capability.capability_id)' must use a fixed Windows .exe entrypoint."
    }
    Require-File (Join-Path $resolvedPayload ($entrypoint -replace "/", "\")) "Runtime worker entrypoint"
    if (-not $capability.supported_devices) {
        throw "Capability '$($capability.capability_id)' must declare supported_devices."
    }
    foreach ($device in @($capability.supported_devices)) {
        if ($device -notin @("cpu", "cuda")) {
            throw "Capability '$($capability.capability_id)' declares unsupported device '$device'."
        }
    }
    if (@($capability.supported_devices | Select-Object -Unique).Count -ne @($capability.supported_devices).Count) {
        throw "Capability '$($capability.capability_id)' declares a device more than once."
    }
}

if (-not $recipe.licenses) {
    throw "Runtime pack recipe must retain at least one license or third-party notice file."
}
foreach ($license in @($recipe.licenses)) {
    if (-not ([string]$license.name).Trim()) {
        throw "Runtime pack license entries must declare a display name."
    }
    $licensePath = Assert-SafeRelativePath ([string]$license.relative_path) "license path"
    Require-File (Join-Path $resolvedPayload ($licensePath -replace "/", "\")) "Runtime pack license"
}

$payloadEntries = @(Get-ChildItem -LiteralPath $resolvedPayload -Recurse -Force)
$reparseEntries = @(
    $payloadEntries |
        Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 }
)
if ($reparseEntries.Count -gt 0) {
    throw "Runtime pack payload contains a junction or symbolic link: $($reparseEntries.FullName -join ', ')."
}
if (Test-Path -LiteralPath (Join-Path $resolvedPayload "runtime-package.json")) {
    throw "Payload root must not contain runtime-package.json; the build writes the trusted manifest."
}

$allowedRuntimeAssets = if ($null -eq $recipe.allowed_runtime_assets) {
    @()
}
else {
    @(
        $recipe.allowed_runtime_assets |
            ForEach-Object { Assert-SafeRelativePath ([string]$_) "allowed runtime asset" }
    )
}
$forbiddenFiles = @(
    $payloadEntries |
        Where-Object { -not $_.PSIsContainer } |
        Where-Object {
            $relative = ConvertTo-PackageRelativePath $resolvedPayload $_.FullName
            $extension = $_.Extension.ToLowerInvariant()
            $_.Name -match "(?i)(^|\.)(cookies?\.txt|keyring\.json)$" -or
            $_.Name -match "(?i)^\.env(?:\.|$)" -or
            $_.Name -match "(?i)^(model|pytorch_model|adapter_model)\.bin$" -or
            (
                $extension -in @(
                    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv",
                    ".cookies", ".sqlite", ".sqlite3", ".db", ".db-shm", ".db-wal",
                    ".safetensors", ".gguf", ".pt", ".pth", ".ckpt",
                    ".onnx", ".pdmodel", ".pdiparams", ".pdparams", ".pdopt"
                ) -and
                $relative -notin $allowedRuntimeAssets
            )
        }
)
if ($forbiddenFiles.Count -gt 0) {
    throw "Runtime pack payload contains user media, credentials, or model weights: $($forbiddenFiles.FullName -join ', ')."
}

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $RepoRoot "artifacts\runtime-packs"
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd("\")
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null
$archiveLeaf = "$($recipe.package_id)-$($recipe.version)-$($recipe.target_triple).zip"
$archivePath = Join-Path $resolvedOutput $archiveLeaf
$catalogEntryPath = "$archivePath.catalog-entry.json"
foreach ($outputPath in @($archivePath, $catalogEntryPath)) {
    if (Test-Path -LiteralPath $outputPath) {
        if (-not $Overwrite) {
            throw "Runtime pack output already exists at '$outputPath'. Pass -Overwrite to replace it."
        }
        Remove-Item -LiteralPath $outputPath -Force
    }
}

$stagingRoot = Join-Path $resolvedOutput (".staging-" + [Guid]::NewGuid().ToString("N"))
$resolvedStaging = [IO.Path]::GetFullPath($stagingRoot)
$outputPrefix = $resolvedOutput + "\"
if (
    -not $resolvedStaging.StartsWith($outputPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not ([IO.Path]::GetFileName($resolvedStaging)).StartsWith(".staging-")
) {
    throw "Runtime pack staging directory escaped its output root."
}

try {
    New-Item -ItemType Directory -Path $resolvedStaging | Out-Null
    Get-ChildItem -LiteralPath $resolvedPayload -Force |
        Copy-Item -Destination $resolvedStaging -Recurse -Force

    $payloadFiles = @(
        Get-ChildItem -LiteralPath $resolvedStaging -Recurse -File |
            Sort-Object FullName |
            ForEach-Object {
                [ordered]@{
                    relative_path = ConvertTo-PackageRelativePath $resolvedStaging $_.FullName
                    size_bytes = [long]$_.Length
                    sha256 = (Get-Video2NotesFileSha256 -Path $_.FullName).ToLowerInvariant()
                }
            }
    )
    $payloadBytes = [long](
        ($payloadFiles | ForEach-Object { [long]$_['size_bytes'] } | Measure-Object -Sum).Sum
    )
    $internalManifest = [ordered]@{
        schema = 1
        package_id = [string]$recipe.package_id
        version = [string]$recipe.version
        display_name = [string]$recipe.display_name
        target_triple = [string]$recipe.target_triple
        runtime_protocol_version = [int]$recipe.runtime_protocol_version
        capabilities = @($recipe.capabilities)
        licenses = @($recipe.licenses)
        upstream_sources = @($recipe.upstream_sources)
        payload_size_bytes = $payloadBytes
        user_model_weights_included = $false
        files = $payloadFiles
    }
    Write-JsonFile (Join-Path $resolvedStaging "runtime-package.json") $internalManifest

    $installedFiles = @(
        Get-ChildItem -LiteralPath $resolvedStaging -Recurse -File |
            Sort-Object FullName |
            ForEach-Object {
                [ordered]@{
                    relative_path = ConvertTo-PackageRelativePath $resolvedStaging $_.FullName
                    size_bytes = [long]$_.Length
                    sha256 = (Get-Video2NotesFileSha256 -Path $_.FullName).ToLowerInvariant()
                }
            }
    )
    $installedBytes = [long](
        ($installedFiles | ForEach-Object { [long]$_['size_bytes'] } | Measure-Object -Sum).Sum
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory(
        $resolvedStaging,
        $archivePath,
        [IO.Compression.CompressionLevel]::Optimal,
        $false
    )
    $archiveItem = Get-Item -LiteralPath $archivePath
    $catalogEntry = [ordered]@{
        schema = 1
        package_id = [string]$recipe.package_id
        version = [string]$recipe.version
        display_name = [string]$recipe.display_name
        target_triple = [string]$recipe.target_triple
        runtime_protocol_version = [int]$recipe.runtime_protocol_version
        capabilities = @($recipe.capabilities)
        archive = [ordered]@{
            file_name = $archiveLeaf
            source_url = if ($SourceUrl) { $SourceUrl } else { $null }
            size_bytes = [long]$archiveItem.Length
            sha256 = (Get-Video2NotesFileSha256 -Path $archivePath).ToLowerInvariant()
            offline_only = [bool](-not $SourceUrl)
        }
        installed_size_bytes = $installedBytes
        files = $installedFiles
        licenses = @($recipe.licenses)
        upstream_sources = @($recipe.upstream_sources)
    }
    Write-JsonFile $catalogEntryPath $catalogEntry

    if (-not $SkipValidation) {
        & (Join-Path $PSScriptRoot "test_runtime_pack.ps1") `
            -ArchivePath $archivePath `
            -CatalogEntryPath $catalogEntryPath
    }

    Write-Host ("Runtime pack ready: {0} ({1:N1} MiB compressed; {2:N1} MiB installed)" -f $archivePath, ($archiveItem.Length / 1MB), ($installedBytes / 1MB)) -ForegroundColor Green
    Write-Host "Catalog entry: $catalogEntryPath"
}
finally {
    if (Test-Path -LiteralPath $resolvedStaging -PathType Container) {
        Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
    }
}
