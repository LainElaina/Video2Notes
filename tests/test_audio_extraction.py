from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from video2notes.audio import (
    AudioExtractionError,
    build_audio_extraction_command,
    extract_audio,
)
from video2notes.domain import MediaManifest, MediaStream, Rational


def make_manifest(source_path: Path, *, audio_start_us: int | None = 2_250_000) -> MediaManifest:
    return MediaManifest(
        source_path=str(source_path),
        source_sha256="a" * 64,
        container_format="matroska",
        file_size=source_path.stat().st_size,
        duration_us=10_000_000,
        timeline_origin_us=2_000_000,
        streams=[
            MediaStream(
                index=0,
                codec_type="video",
                time_base=Rational(numerator=1, denominator=90_000),
                start_time_us=2_000_000,
                duration_us=10_000_000,
                avg_frame_rate=Rational(numerator=30_000, denominator=1_001),
            ),
            MediaStream(
                index=1,
                codec_type="audio",
                codec_name="aac",
                time_base=Rational(numerator=1, denominator=48_000),
                start_time_us=audio_start_us,
                duration_us=9_500_000,
                sample_rate=48_000,
                channels=2,
            ),
        ],
    )


class AudioExtractionTests(unittest.TestCase):
    def test_command_selects_audio_and_never_uses_video_fps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mkv"
            source.write_bytes(b"fixture")
            manifest = make_manifest(source)
            command = build_audio_extraction_command(
                manifest,
                root / "speech.wav",
                ffmpeg_executable="ffmpeg.exe",
            )

            self.assertEqual(command[command.index("-map") + 1], "0:1")
            self.assertEqual(command[command.index("-ac") + 1], "1")
            self.assertEqual(command[command.index("-ar") + 1], "16000")
            self.assertEqual(command[command.index("-c:a") + 1], "pcm_s16le")
            self.assertNotIn("-r", command)

    def test_extract_records_exact_canonical_sample_zero_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mkv"
            output = root / "stage" / "speech.wav"
            source.write_bytes(b"fixture")
            manifest = make_manifest(source)
            captured: list[list[str]] = []

            def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
                captured.append(command)
                Path(command[-1]).write_bytes(b"RIFF-fixture")
                return subprocess.CompletedProcess(command, 0, "", "")

            executable = shutil.which(Path(sys.executable).name) or sys.executable
            result = extract_audio(
                manifest,
                output,
                ffmpeg_path=executable,
                runner=fake_runner,
            )

            self.assertEqual(result.source_stream_start_us, 2_250_000)
            self.assertEqual(result.timeline_origin_us, 2_000_000)
            self.assertEqual(result.output_time_zero_canonical_us, 250_000)
            self.assertEqual(result.duration_us, 9_500_000)
            self.assertEqual(result.audio_stream_index, 1)
            self.assertEqual(captured[0][captured[0].index("-map") + 1], "0:1")
            self.assertTrue(output.is_file())

    def test_extract_fails_when_stream_start_cannot_be_mapped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mkv"
            source.write_bytes(b"fixture")
            manifest = make_manifest(source, audio_start_us=None)
            executable = shutil.which(Path(sys.executable).name) or sys.executable
            with self.assertRaisesRegex(AudioExtractionError, "start_time_us is required"):
                extract_audio(
                    manifest,
                    root / "speech.wav",
                    ffmpeg_path=executable,
                    runner=lambda command: subprocess.CompletedProcess(command, 0, "", ""),
                )

    def test_extract_reports_ffmpeg_stderr_without_shell_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mkv"
            source.write_bytes(b"fixture")
            executable = shutil.which(Path(sys.executable).name) or sys.executable

            def failed(command: list[str]) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(command, 2, "", "decoder failed")

            with self.assertRaisesRegex(AudioExtractionError, "decoder failed"):
                extract_audio(
                    make_manifest(source),
                    root / "speech.wav",
                    ffmpeg_path=executable,
                    runner=failed,
                )


if __name__ == "__main__":
    unittest.main()
