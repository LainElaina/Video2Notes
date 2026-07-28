[CmdletBinding()]
param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$ffmpeg = Get-Command ffmpeg -ErrorAction Stop
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot "..\samples\evidence-demo.mp4"
}
$target = [System.IO.Path]::GetFullPath($OutputPath)
$targetDirectory = Split-Path -Parent $target
New-Item -ItemType Directory -Force -Path $targetDirectory | Out-Null

$videoFilter = @(
    "drawbox=x=0:y=0:w=iw:h=ih:color=0xF4F5F1:t=fill",
    "drawbox=x=0:y=0:w=iw:h=ih:color=0xE8F0EE:t=fill:enable='between(t,3,5.999)'",
    "drawbox=x=0:y=0:w=iw:h=ih:color=0xD6E1F0:t=fill:enable='gte(t,6)'",
    "drawbox=x=32:y=28:w=6:h=304:color=0x176C70:t=fill",
    "drawtext=fontfile='C\:/Windows/Fonts/consola.ttf':text='VIDEO2NOTES':x=62:y=58:fontsize=42:fontcolor=0x172125",
    "drawtext=fontfile='C\:/Windows/Fonts/consola.ttf':text='EVIDENCE FIRST':x=62:y=130:fontsize=29:fontcolor=0x176C70:enable='between(t,0,2.999)'",
    "drawtext=fontfile='C\:/Windows/Fonts/consola.ttf':text='SCREEN TEXT CHANGED':x=62:y=130:fontsize=29:fontcolor=0xD75A43:enable='between(t,3,5.999)'",
    "drawtext=fontfile='C\:/Windows/Fonts/consola.ttf':text='ASR + OCR ALIGNED':x=62:y=130:fontsize=29:fontcolor=0x176C70:enable='gte(t,6)'",
    "drawtext=fontfile='C\:/Windows/Fonts/consola.ttf':text='PTS 0000 - 0009':x=62:y=205:fontsize=22:fontcolor=0x59676A",
    "drawtext=fontfile='C\:/Windows/Fonts/consola.ttf':text='adaptive visual change sample':x=62:y=268:fontsize=18:fontcolor=0x59676A"
) -join ","

$arguments = @(
    "-hide_banner",
    "-loglevel", "error",
    "-y",
    "-f", "lavfi",
    "-i", "color=c=black:s=640x360:r=12:d=9",
    "-f", "lavfi",
    "-i", "sine=frequency=523:sample_rate=16000:duration=9",
    "-vf", $videoFilter,
    "-af", "volume=0.06",
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "26",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac",
    "-b:a", "48k",
    "-shortest",
    $target
)

& $ffmpeg.Source @arguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw "ffmpeg did not create the demo media."
}

$size = (Get-Item -LiteralPath $target).Length
Write-Host "Created redistributable demo media: $target ($size bytes)"
