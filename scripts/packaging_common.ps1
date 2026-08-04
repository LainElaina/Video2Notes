function Get-Video2NotesFileSha256 {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    # Use the framework implementation directly instead of relying on
    # Microsoft.PowerShell.Utility being available through module autoloading.
    # Portable builds also call this inside a guarded, profile-free child shell.
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
    try {
        $bytes = $algorithm.ComputeHash($stream)
        return ([BitConverter]::ToString($bytes) -replace "-", "")
    }
    finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

function Get-Video2NotesReleaseProfile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory = $true)]
        [string]$ProfileId
    )

    $catalogPath = Join-Path `
        ([IO.Path]::GetFullPath($RepositoryRoot)) `
        "packaging\runtime-packs\release-profiles.json"
    if (-not (Test-Path -LiteralPath $catalogPath -PathType Leaf)) {
        throw "Release profile catalog was not found at '$catalogPath'."
    }
    $catalog = Get-Content -LiteralPath $catalogPath -Raw | ConvertFrom-Json
    if ($catalog.schema -ne 1 -or -not $catalog.profiles) {
        throw "Release profile catalog '$catalogPath' is unsupported or empty."
    }
    $matches = @($catalog.profiles | Where-Object { $_.id -eq $ProfileId })
    if ($matches.Count -ne 1) {
        throw "Release profile '$ProfileId' must resolve to exactly one catalog entry."
    }
    $profile = $matches[0]
    if ($profile.sidecar_flavor -notin @("core-only", "full")) {
        throw "Release profile '$ProfileId' has an unsupported sidecar flavor."
    }
    if ($null -eq $profile.runtime_package_ids) {
        throw "Release profile '$ProfileId' does not declare runtime_package_ids."
    }
    return $profile
}

function Get-Video2NotesSidecarSourceFingerprint {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot
    )

    $resolvedRoot = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd("\")
    $sourceRoot = Join-Path $resolvedRoot "src\video2notes"
    if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
        throw "Video2Notes Python source directory was not found at '$sourceRoot'."
    }

    # Include every source/data format that can become part of the Python
    # package, while deliberately excluding volatile __pycache__ output.
    $sourceFiles = @(
        Get-ChildItem -LiteralPath $sourceRoot -Recurse -File |
            Where-Object {
                $_.Extension.ToLowerInvariant() -in @(
                    ".py", ".pyi", ".json", ".yaml", ".yml", ".toml"
                ) -or $_.Name -eq "py.typed"
            }
    )
    $packagingFiles = @(
        "pyproject.toml",
        "packaging\runtime-packs\catalog.json",
        "packaging\runtime-packs\release-profiles.json",
        "scripts\build_sidecar.ps1",
        "scripts\packaging_common.ps1",
        "scripts\pyinstaller_runtime_hook.py",
        "scripts\sidecar_entry.py"
    ) | ForEach-Object {
        $candidate = Join-Path $resolvedRoot $_
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Sidecar fingerprint input was not found at '$candidate'."
        }
        Get-Item -LiteralPath $candidate
    }

    $inputs = @($sourceFiles) + @($packagingFiles)
    if ($inputs.Count -eq 0) {
        throw "No files were available for the sidecar source fingerprint."
    }

    $lines = @(
        $inputs |
            Sort-Object FullName |
            ForEach-Object {
                $relativePath = $_.FullName.Substring($resolvedRoot.Length).TrimStart("\") -replace "\\", "/"
                $fileHash = (Get-Video2NotesFileSha256 -Path $_.FullName).ToLowerInvariant()
                "{0}`0{1}" -f $relativePath, $fileHash
            }
    )
    $payload = [Text.UTF8Encoding]::new($false).GetBytes(($lines -join "`n"))
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($algorithm.ComputeHash($payload)) -replace "-", "").ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}
