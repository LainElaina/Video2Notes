[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,
    [string]$OutputDirectory = "",
    [ValidateSet("Split", "Verify")]
    [string]$Mode = "Split",
    [ValidateRange(1, [long]::MaxValue)]
    [long]$MaxPartBytes = 1800MB,
    [switch]$Overwrite,
    [switch]$SkipVerification
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "packaging_common.ps1")

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Value
    )

    $json = $Value | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($Path, "$json`n", [Text.UTF8Encoding]::new($false))
}

function Complete-Sha256 {
    param(
        [Parameter(Mandatory = $true)]
        [Security.Cryptography.HashAlgorithm]$Algorithm
    )

    [void]$Algorithm.TransformFinalBlock([byte[]]::new(0), 0, 0)
    return (([BitConverter]::ToString($Algorithm.Hash) -replace "-", "").ToLowerInvariant())
}

function Get-PartFileName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ArchiveFileName,
        [Parameter(Mandatory = $true)]
        [int]$Index,
        [Parameter(Mandatory = $true)]
        [int]$Width
    )

    return "{0}.part{1}" -f $ArchiveFileName, $Index.ToString("D$Width")
}

function Get-ExistingPartFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,
        [Parameter(Mandatory = $true)]
        [string]$ArchiveFileName
    )

    $pattern = "^" + [Regex]::Escape($ArchiveFileName) + "\.part\d{3,}$"
    return @(
        Get-ChildItem -LiteralPath $Directory -File -Force |
            Where-Object { $_.Name -match $pattern } |
            Sort-Object Name
    )
}

function Assert-ManifestFileName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,
        [Parameter(Mandatory = $true)]
        [string]$Purpose
    )

    if (
        -not $Value -or
        $Value -ne [IO.Path]::GetFileName($Value) -or
        [IO.Path]::IsPathRooted($Value) -or
        $Value.Contains("/") -or
        $Value.Contains("\")
    ) {
        throw "$Purpose contains an unsafe file name: '$Value'."
    }
}

function Test-ArchiveParts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedArchive,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedOutput,
        [Parameter(Mandatory = $true)]
        [object]$Manifest
    )

    if ([int]$Manifest.schema -ne 1) {
        throw "Release archive part manifest schema '$($Manifest.schema)' is unsupported."
    }
    if ($null -eq $Manifest.archive -or $null -eq $Manifest.parts) {
        throw "Release archive part manifest is missing archive or parts metadata."
    }

    $archiveItem = Get-Item -LiteralPath $ResolvedArchive
    $archiveFileName = [string]$Manifest.archive.file_name
    Assert-ManifestFileName $archiveFileName "Release archive manifest"
    if ($archiveFileName -cne $archiveItem.Name) {
        throw "Release archive manifest targets '$archiveFileName', not '$($archiveItem.Name)'."
    }
    if ([long]$Manifest.archive.size_bytes -ne [long]$archiveItem.Length) {
        throw "Release archive size does not match the part manifest."
    }

    $declaredArchiveHash = ([string]$Manifest.archive.sha256).ToLowerInvariant()
    if ($declaredArchiveHash -notmatch "^[0-9a-f]{64}$") {
        throw "Release archive manifest contains an invalid SHA-256 value."
    }
    $actualArchiveHash = (Get-Video2NotesFileSha256 -Path $ResolvedArchive).ToLowerInvariant()
    if ($actualArchiveHash -cne $declaredArchiveHash) {
        throw "Release archive SHA-256 does not match the part manifest."
    }

    $parts = @($Manifest.parts)
    if ($parts.Count -lt 2 -or [int]$Manifest.part_count -ne $parts.Count) {
        throw "Release archive part manifest must contain at least two contiguous parts."
    }
    $partNumberWidth = [Math]::Max(3, $parts.Count.ToString().Length)
    $maxPartBytes = [long]$Manifest.max_part_size_bytes
    if ($maxPartBytes -lt 1) {
        throw "Release archive part manifest has an invalid maximum part size."
    }

    $buffer = [byte[]]::new(4MB)
    $reassembledHasher = [Security.Cryptography.SHA256]::Create()
    $reassembledBytes = [long]0
    try {
        for ($offset = 0; $offset -lt $parts.Count; $offset++) {
            $part = $parts[$offset]
            $expectedIndex = $offset + 1
            if ([int]$part.index -ne $expectedIndex) {
                throw "Release archive parts are not numbered contiguously at index $expectedIndex."
            }

            $partFileName = [string]$part.file_name
            Assert-ManifestFileName $partFileName "Release archive part $expectedIndex"
            $expectedPartFileName = Get-PartFileName `
                -ArchiveFileName $archiveFileName `
                -Index $expectedIndex `
                -Width $partNumberWidth
            if ($partFileName -cne $expectedPartFileName) {
                throw "Release archive part $expectedIndex has an unexpected file name."
            }
            $partPath = Join-Path $ResolvedOutput $partFileName
            if (-not (Test-Path -LiteralPath $partPath -PathType Leaf)) {
                throw "Release archive part $expectedIndex was not found at '$partPath'."
            }

            $partItem = Get-Item -LiteralPath $partPath
            $declaredPartBytes = [long]$part.size_bytes
            if (
                $declaredPartBytes -lt 1 -or
                $declaredPartBytes -gt $maxPartBytes -or
                [long]$partItem.Length -ne $declaredPartBytes
            ) {
                throw "Release archive part $expectedIndex has an invalid size."
            }
            if ($expectedIndex -lt $parts.Count -and $declaredPartBytes -ne $maxPartBytes) {
                throw "Release archive part $expectedIndex is not filled to the declared maximum size."
            }

            $declaredPartHash = ([string]$part.sha256).ToLowerInvariant()
            if ($declaredPartHash -notmatch "^[0-9a-f]{64}$") {
                throw "Release archive part $expectedIndex has an invalid SHA-256 value."
            }

            $partHasher = [Security.Cryptography.SHA256]::Create()
            $partStream = [IO.File]::OpenRead($partPath)
            try {
                while (($read = $partStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    [void]$partHasher.TransformBlock($buffer, 0, $read, $null, 0)
                    [void]$reassembledHasher.TransformBlock($buffer, 0, $read, $null, 0)
                    $reassembledBytes += [long]$read
                }
                $actualPartHash = Complete-Sha256 -Algorithm $partHasher
            }
            finally {
                $partStream.Dispose()
                $partHasher.Dispose()
            }
            if ($actualPartHash -cne $declaredPartHash) {
                throw "Release archive part $expectedIndex SHA-256 does not match the manifest."
            }
        }

        $reassembledHash = Complete-Sha256 -Algorithm $reassembledHasher
    }
    finally {
        $reassembledHasher.Dispose()
    }

    if ($reassembledBytes -ne [long]$archiveItem.Length) {
        throw "Reassembled release archive size does not match the original ZIP."
    }
    if ($reassembledHash -cne $actualArchiveHash) {
        throw "Reassembled release archive SHA-256 does not match the original ZIP."
    }

    return [ordered]@{
        verified = $true
        size_bytes = $reassembledBytes
        sha256 = $reassembledHash
    }
}

$resolvedArchive = [IO.Path]::GetFullPath($ArchivePath)
if (-not (Test-Path -LiteralPath $resolvedArchive -PathType Leaf)) {
    throw "Release archive was not found at '$resolvedArchive'."
}
$archiveItem = Get-Item -LiteralPath $resolvedArchive
if ($archiveItem.Extension -ne ".zip") {
    throw "Release archive must be a .zip file."
}

if (-not $OutputDirectory) {
    $OutputDirectory = $archiveItem.DirectoryName
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null
$manifestPath = Join-Path $resolvedOutput ($archiveItem.Name + ".parts.json")

if ($Mode -eq "Verify") {
    if ($SkipVerification) {
        throw "-SkipVerification cannot be used with -Mode Verify."
    }
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Release archive part manifest was not found at '$manifestPath'."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $verification = Test-ArchiveParts `
        -ResolvedArchive $resolvedArchive `
        -ResolvedOutput $resolvedOutput `
        -Manifest $manifest
    Write-Host ("Release archive parts verified: {0} ({1} parts; SHA-256 {2})" -f $resolvedArchive, @($manifest.parts).Count, $verification.sha256) -ForegroundColor Green
    return
}

if ([long]$archiveItem.Length -le $MaxPartBytes) {
    throw "Release archive is not larger than MaxPartBytes ($MaxPartBytes); multipart output is unnecessary."
}

$calculatedPartCount = [Math]::Ceiling(
    [double]$archiveItem.Length / [double]$MaxPartBytes
)
if ($calculatedPartCount -gt [int]::MaxValue) {
    throw "MaxPartBytes is too small for this archive."
}
$partCount = [int]$calculatedPartCount
$partNumberWidth = [Math]::Max(3, $partCount.ToString().Length)
$existingParts = @(Get-ExistingPartFiles -Directory $resolvedOutput -ArchiveFileName $archiveItem.Name)
$existingOutputs = @($existingParts | ForEach-Object { $_.FullName })
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    $existingOutputs += $manifestPath
}
if ($existingOutputs.Count -gt 0 -and -not $Overwrite) {
    throw "Release archive part output already exists. Pass -Overwrite to replace: $($existingOutputs -join ', ')"
}
if ($Overwrite) {
    foreach ($existingPart in $existingParts) {
        Remove-Item -LiteralPath $existingPart.FullName -Force
    }
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        Remove-Item -LiteralPath $manifestPath -Force
    }
}

$createdPartPaths = @()
$partMetadata = @()
$archiveHasher = [Security.Cryptography.SHA256]::Create()
$archiveStream = [IO.File]::OpenRead($resolvedArchive)
$buffer = [byte[]]::new(4MB)
try {
    for ($partIndex = 1; $partIndex -le $partCount; $partIndex++) {
        $partFileName = Get-PartFileName `
            -ArchiveFileName $archiveItem.Name `
            -Index $partIndex `
            -Width $partNumberWidth
        $partPath = Join-Path $resolvedOutput $partFileName
        $createdPartPaths += $partPath
        $partHasher = [Security.Cryptography.SHA256]::Create()
        $partStream = [IO.File]::Open(
            $partPath,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None
        )
        $partBytes = [long]0
        try {
            while ($partBytes -lt $MaxPartBytes) {
                $requested = [int][Math]::Min(
                    [long]$buffer.Length,
                    $MaxPartBytes - $partBytes
                )
                $read = $archiveStream.Read($buffer, 0, $requested)
                if ($read -eq 0) {
                    break
                }
                $partStream.Write($buffer, 0, $read)
                [void]$partHasher.TransformBlock($buffer, 0, $read, $null, 0)
                [void]$archiveHasher.TransformBlock($buffer, 0, $read, $null, 0)
                $partBytes += [long]$read
            }
            $partStream.Flush()
            $partHash = Complete-Sha256 -Algorithm $partHasher
        }
        finally {
            $partStream.Dispose()
            $partHasher.Dispose()
        }

        if ($partBytes -lt 1) {
            throw "Release archive ended before part $partIndex could be written."
        }
        $partMetadata += [ordered]@{
            index = $partIndex
            file_name = $partFileName
            size_bytes = $partBytes
            sha256 = $partHash
        }
    }

    if ($archiveStream.Position -ne $archiveStream.Length) {
        throw "Release archive still contains unread bytes after writing $partCount parts."
    }
    $archiveHash = Complete-Sha256 -Algorithm $archiveHasher
}
catch {
    foreach ($createdPartPath in $createdPartPaths) {
        if (Test-Path -LiteralPath $createdPartPath -PathType Leaf) {
            Remove-Item -LiteralPath $createdPartPath -Force
        }
    }
    throw
}
finally {
    $archiveStream.Dispose()
    $archiveHasher.Dispose()
}

$manifest = [ordered]@{
    schema = 1
    archive = [ordered]@{
        file_name = $archiveItem.Name
        size_bytes = [long]$archiveItem.Length
        sha256 = $archiveHash
    }
    max_part_size_bytes = $MaxPartBytes
    part_count = $partCount
    parts = @($partMetadata)
    reassembly = [ordered]@{
        verified = $false
        size_bytes = [long]$archiveItem.Length
        sha256 = $archiveHash
    }
}

try {
    if (-not $SkipVerification) {
        $verification = Test-ArchiveParts `
            -ResolvedArchive $resolvedArchive `
            -ResolvedOutput $resolvedOutput `
            -Manifest $manifest
        $manifest.reassembly = $verification
    }
    Write-JsonFile -Path $manifestPath -Value $manifest
}
catch {
    foreach ($createdPartPath in $createdPartPaths) {
        if (Test-Path -LiteralPath $createdPartPath -PathType Leaf) {
            Remove-Item -LiteralPath $createdPartPath -Force
        }
    }
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        Remove-Item -LiteralPath $manifestPath -Force
    }
    throw
}

Write-Host ("Release archive parts ready: {0} ({1} parts; maximum {2:N1} MiB each)" -f $resolvedOutput, $partCount, ($MaxPartBytes / 1MB)) -ForegroundColor Green
Write-Host "Part manifest: $manifestPath"
