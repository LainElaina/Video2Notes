"""FFmpeg audio extraction with an explicit source-to-canonical time mapping."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from video2notes.domain import MediaManifest, MediaStream

from .models import AudioExtractionResult

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class AudioExtractionError(RuntimeError):
    """Raised when deterministic PCM extraction cannot be completed."""


def select_audio_stream(
    manifest: MediaManifest,
    *,
    stream_index: int | None = None,
) -> MediaStream:
    candidates = [stream for stream in manifest.streams if stream.codec_type == "audio"]
    if stream_index is None:
        if not candidates:
            raise AudioExtractionError("media manifest does not contain an audio stream")
        return candidates[0]
    for stream in candidates:
        if stream.index == stream_index:
            return stream
    raise AudioExtractionError(f"audio stream index {stream_index} is not present")


def build_audio_extraction_command(
    manifest: MediaManifest,
    output_path: str | Path,
    *,
    ffmpeg_executable: str,
    stream_index: int | None = None,
) -> list[str]:
    """Return a shell-free FFmpeg argument array for 16 kHz mono PCM."""

    stream = select_audio_stream(manifest, stream_index=stream_index)
    return [
        ffmpeg_executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        manifest.source_path,
        "-map",
        f"0:{stream.index}",
        "-vn",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(Path(output_path).expanduser().resolve()),
    ]


def extract_audio(
    manifest: MediaManifest,
    output_path: str | Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    stream_index: int | None = None,
    runner: CommandRunner | None = None,
) -> AudioExtractionResult:
    """Extract one audio stream without inferring any time from video frames."""

    source_path = Path(manifest.source_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"media does not exist: {source_path}")
    executable = shutil.which(ffmpeg_path)
    if executable is None:
        raise FileNotFoundError(f"required executable '{ffmpeg_path}' was not found on PATH")

    stream = select_audio_stream(manifest, stream_index=stream_index)
    if stream.start_time_us is None:
        raise AudioExtractionError(
            "audio stream start_time_us is required for canonical timestamp mapping"
        )
    canonical_start_us = stream.start_time_us - manifest.timeline_origin_us
    if canonical_start_us < 0:
        raise AudioExtractionError(
            "audio stream starts before the manifest timeline origin; manifest is inconsistent"
        )

    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = build_audio_extraction_command(
        manifest,
        destination,
        ffmpeg_executable=executable,
        stream_index=stream.index,
    )
    completed = (runner or _run_command)(command)
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        suffix = f": {detail}" if detail else ""
        raise AudioExtractionError(f"ffmpeg audio extraction failed{suffix}")
    if not destination.is_file():
        raise AudioExtractionError("ffmpeg reported success but did not create the WAV output")

    duration_us = stream.duration_us
    if duration_us is None:
        duration_us = max(0, manifest.duration_us - canonical_start_us)
    return AudioExtractionResult(
        input_path=str(source_path.expanduser().resolve()),
        output_path=str(destination),
        audio_stream_index=stream.index,
        source_stream_start_us=stream.start_time_us,
        timeline_origin_us=manifest.timeline_origin_us,
        output_time_zero_canonical_us=canonical_start_us,
        duration_us=duration_us,
    )


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
