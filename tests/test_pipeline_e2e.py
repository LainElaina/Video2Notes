from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from video2notes.audio import (
    ASRSegment,
    ASRTranscript,
    ASRWord,
    TranscriptTimeline,
)
from video2notes.notes import EvidenceNoteComposer
from video2notes.ocr import (
    BackendOcrLine,
    BackendOcrOutput,
    OcrBox,
    OcrModelInvocation,
)
from video2notes.pipeline import (
    PipelineRequest,
    PipelineRuntime,
    Video2NotesPipeline,
)
from video2notes.sources import AcquisitionPolicy, SourceInput, SourceRegistry
from video2notes.system import GpuDevice, HardwareSnapshot, QualityMode


class FakeAsr:
    def __init__(
        self,
        *,
        text: str = "证据优先",
        confidence: float = 0.95,
        provider: str = "fake-asr",
    ) -> None:
        self.calls = 0
        self.text = text
        self.confidence = confidence
        self.provider = provider

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRTranscript:
        self.calls += 1
        self.assert_audio(audio_path)
        provider = self.provider
        model = "fixture"
        version = "1"
        word = ASRWord(
            start_us=100_000,
            end_us=700_000,
            text=self.text,
            language=language or "zh",
            raw_confidence=self.confidence,
            calibrated_confidence=self.confidence,
            confidence_method="fixture",
            provider=provider,
            model=model,
            version=version,
        )
        return ASRTranscript(
            provider=provider,
            model=model,
            version=version,
            language=language or "zh",
            timeline=TranscriptTimeline.AUDIO_FILE,
            segments=[
                ASRSegment(
                    id="segment-1",
                    start_us=0,
                    end_us=1_000_000,
                    text=self.text,
                    words=[word],
                    language=language or "zh",
                    calibrated_confidence=self.confidence,
                    confidence_method="fixture",
                    provider=provider,
                    model=model,
                    version=version,
                )
            ],
        )

    def assert_audio(self, audio_path: Path) -> None:
        if not audio_path.is_file():
            raise AssertionError("pipeline did not extract audio")


class FakeOcr:
    def __init__(self) -> None:
        self.calls = 0

    def recognize(
        self,
        image: Image.Image,
        *,
        language_hints=(),
    ) -> BackendOcrOutput:
        del language_hints
        self.calls += 1
        return BackendOcrOutput(
            lines=[
                BackendOcrLine(
                    raw_text="Evidence First",
                    box=OcrBox(
                        x=0,
                        y=0,
                        width=image.width,
                        height=image.height,
                    ),
                    confidence=0.98,
                    script="latin",
                    language="en",
                )
            ],
            invocation=OcrModelInvocation(
                engine="fake-ocr",
                version="1",
                backend="fixture",
            ),
        )


def make_media(path: Path) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=320x180:r=10:d=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=16000:duration=2",
        "-vf",
        "drawbox=x=20:y=20:w=280:h=80:color=black:t=fill",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True)


def fixture_hardware() -> HardwareSnapshot:
    return HardwareSnapshot(
        os_name="Windows",
        os_version="test",
        architecture="AMD64",
        cpu_name="fixture",
        logical_cores=8,
        memory_total_bytes=16 * 1024**3,
        gpus=(
            GpuDevice(
                name="fixture",
                vendor="NVIDIA",
                memory_total_bytes=8 * 1024**3,
            ),
        ),
        ffmpeg_hwaccels=("cuda",),
    )


class PipelineEndToEndTests(unittest.TestCase):
    def test_local_video_runs_to_markdown_html_and_reuses_every_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mp4"
            make_media(source)
            asr = FakeAsr()
            ocr = FakeOcr()
            runtime = PipelineRuntime(
                source_registry=SourceRegistry.default(),
                note_composer=EvidenceNoteComposer(),
                asr_backend=asr,
                ocr_backend=ocr,
                hardware=fixture_hardware(),
            )
            pipeline = Video2NotesPipeline(root / "runs", runtime=runtime)
            request = PipelineRequest(
                source=SourceInput.local(source),
                acquisition=AcquisitionPolicy(prefer_hardlink=False),
                quality_mode=QualityMode.FAST,
                language_hints=["zh"],
                include_screenshots=True,
                generate_pdf=False,
            )
            workspace = pipeline.create_run(request, run_id="e2e")
            first = pipeline.run(workspace, request)

            markdown_path = workspace.root / first.markdown.relative_path
            html_path = workspace.root / first.html.relative_path
            self.assertTrue(markdown_path.is_file())
            self.assertTrue(html_path.is_file())
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertIn("证据优先", markdown)
            self.assertIn("Evidence First", markdown)
            self.assertIn("video2notes://seek/", markdown)
            self.assertIn("data:image/jpeg;base64,", html_path.read_text(encoding="utf-8"))
            self.assertGreater(first.evidence_count, 1)
            self.assertGreaterEqual(first.visual_state_count, 1)
            self.assertEqual(asr.calls, 1)
            self.assertEqual(ocr.calls, first.visual_state_count)

            reloaded = type(workspace)(workspace.root)
            second = pipeline.run(reloaded, request)
            self.assertEqual(second.markdown.sha256, first.markdown.sha256)
            self.assertEqual(asr.calls, 1)
            self.assertEqual(ocr.calls, first.visual_state_count)
            self.assertTrue(
                all(record.attempt == 1 for record in reloaded.manifest.stages.values())
            )

    def test_accurate_mode_only_reruns_low_confidence_audio_window(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mp4"
            make_media(source)
            primary = FakeAsr(
                text="uncertain primary",
                confidence=0.3,
                provider="primary",
            )
            secondary = FakeAsr(
                text="confirmed secondary",
                confidence=0.98,
                provider="secondary",
            )
            runtime = PipelineRuntime(
                source_registry=SourceRegistry.default(),
                note_composer=EvidenceNoteComposer(),
                asr_backend=primary,
                secondary_asr_backend=secondary,
                hardware=fixture_hardware(),
            )
            pipeline = Video2NotesPipeline(root / "runs", runtime=runtime)
            request = PipelineRequest(
                source=SourceInput.local(source),
                acquisition=AcquisitionPolicy(prefer_hardlink=False),
                quality_mode=QualityMode.ACCURATE,
                include_screenshots=False,
                generate_pdf=False,
            )
            workspace = pipeline.create_run(request, run_id="secondary")

            outcome = pipeline.run(workspace, request)

            self.assertEqual(primary.calls, 1)
            self.assertEqual(secondary.calls, 1)
            decisions = (workspace.root / "asr" / "secondary-decisions.json").read_text(
                encoding="utf-8"
            )
            evidence = (workspace.root / "asr" / "asr-evidence.json").read_text(encoding="utf-8")
            self.assertIn('"status": "completed"', decisions)
            self.assertIn('"asr_pass": "selective_secondary"', evidence)
            self.assertGreaterEqual(outcome.evidence_count, 3)
