from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from video2notes.artifacts import RunWorkspace
from video2notes.audio import (
    ASRSegment,
    ASRTranscript,
    ASRWord,
    TranscriptTimeline,
)
from video2notes.notes import EvidenceNoteComposer, NoteDocument
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
from video2notes.vision import (
    SamplingMode,
    SamplingOverride,
    SamplingPlan,
    SamplingSpec,
    TimeRange,
)


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
        self.provider_id = provider
        self.model_id = "fixture"

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
    def test_visual_stage_executes_adaptive_fixed_and_skip_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mp4"
            make_media(source)
            runtime = PipelineRuntime(
                source_registry=SourceRegistry.default(),
                note_composer=EvidenceNoteComposer(),
                asr_backend=FakeAsr(),
                ocr_backend=None,
                hardware=fixture_hardware(),
            )
            pipeline = Video2NotesPipeline(root / "runs", runtime=runtime)
            request = PipelineRequest(
                source=SourceInput.local(source),
                acquisition=AcquisitionPolicy(prefer_hardlink=False),
                quality_mode=QualityMode.FAST,
                sampling_plan=SamplingPlan(
                    default=SamplingSpec(mode=SamplingMode.ADAPTIVE),
                    overrides=[
                        SamplingOverride(
                            range=TimeRange(start_us=500_000, end_us=1_000_000),
                            sampling=SamplingSpec(
                                mode=SamplingMode.FIXED_INTERVAL,
                                interval_us=100_000,
                            ),
                        ),
                        SamplingOverride(
                            range=TimeRange(start_us=1_000_000, end_us=1_500_000),
                            sampling=SamplingSpec(mode=SamplingMode.SKIP),
                        ),
                    ],
                ),
                include_screenshots=False,
                generate_pdf=False,
            )
            workspace = pipeline.create_run(request, run_id="segmented-sampling")

            pipeline.run(workspace, request)

            states = json.loads(
                (workspace.root / "vision" / "visual-states.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("fixed_interval", {item["change_reason"] for item in states})
            self.assertTrue(
                all(
                    not (item["start_us"] < 1_500_000 and item["end_us"] > 1_000_000)
                    for item in states
                )
            )
            fixed_states = [
                item for item in states if item["change_reason"] == "fixed_interval"
            ]
            self.assertGreaterEqual(len(fixed_states), 4)
            self.assertTrue(
                all(
                    item["quality"]["requested_interval_us"] == 100_000
                    and item["quality"]["sampling_mode"] == "fixed_interval"
                    for item in fixed_states
                )
            )
            record = RunWorkspace(workspace.root).manifest.stages["vision.scan"]
            self.assertEqual(record.metrics["fixed_interval_segment_count"], 1)
            self.assertEqual(record.metrics["skip_segment_count"], 1)
            self.assertEqual(record.metrics["adaptive_segment_count"], 2)

    def test_visual_scan_keeps_a_representative_when_ocr_abstains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mp4"
            make_media(source)
            runtime = PipelineRuntime(
                source_registry=SourceRegistry.default(),
                note_composer=EvidenceNoteComposer(),
                asr_backend=FakeAsr(),
                ocr_backend=None,
                hardware=fixture_hardware(),
            )
            pipeline = Video2NotesPipeline(root / "runs", runtime=runtime)
            request = PipelineRequest(
                source=SourceInput.local(source),
                acquisition=AcquisitionPolicy(prefer_hardlink=False),
                quality_mode=QualityMode.BALANCED,
                include_screenshots=True,
                generate_pdf=False,
            )
            workspace = pipeline.create_run(request, run_id="visual-fallback")

            pipeline.run(workspace, request)

            note = NoteDocument.model_validate_json(
                (workspace.root / "notes" / "document.json").read_text(encoding="utf-8")
            )
            screenshots = [
                screenshot
                for section in note.sections
                for screenshot in section.screenshots
            ]
            self.assertEqual(len(screenshots), 1)
            self.assertEqual(
                screenshots[0].caption,
                "内容自适应扫描选出的稳定代表画面",
            )
            self.assertTrue((workspace.root / screenshots[0].relative_path).is_file())

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
                quality_mode=QualityMode.BALANCED,
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
            self.assertTrue((workspace.root / "render" / "outcome.json").is_file())
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertIn("证据优先", markdown)
            self.assertIn("Evidence First", markdown)
            self.assertIn("video2notes://seek/", markdown)
            document = (workspace.root / "notes" / "document.json").read_text(
                encoding="utf-8"
            )
            self.assertIn('"relative_path": "notes/assets/', document)
            self.assertIn("](assets/", markdown)
            self.assertTrue(any((workspace.root / "notes" / "assets").iterdir()))
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

    def test_quality_specific_backends_and_effective_plan_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.mp4"
            make_media(source)
            fallback_asr = FakeAsr(text="fallback", provider="fallback-asr")
            accurate_asr = FakeAsr(text="accurate", provider="accurate-asr")
            fallback_ocr = FakeOcr()
            accurate_ocr = FakeOcr()
            runtime = PipelineRuntime(
                source_registry=SourceRegistry.default(),
                note_composer=EvidenceNoteComposer(),
                asr_backend=fallback_asr,
                ocr_backend=fallback_ocr,
                asr_backends_by_quality={QualityMode.ACCURATE: accurate_asr},
                ocr_backends_by_quality={QualityMode.ACCURATE: accurate_ocr},
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
            workspace = pipeline.create_run(request, run_id="profiled-backends")

            pipeline.run(workspace, request)

            self.assertEqual(fallback_asr.calls, 0)
            self.assertEqual(fallback_ocr.calls, 0)
            self.assertEqual(accurate_asr.calls, 1)
            self.assertGreater(accurate_ocr.calls, 0)
            plan_path = workspace.root / "system" / "execution-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(plan["quality_mode"], "accurate")
            self.assertEqual(
                plan["actual_backends"]["asr_primary"]["provider_id"],
                "accurate-asr",
            )
            self.assertIn("secondary_asr_unavailable", plan["degraded_features"])
            self.assertIn("note_verifier_unavailable", plan["degraded_features"])
            manifest = RunWorkspace(workspace.root).manifest
            system_record = manifest.stages["system.plan"]
            self.assertEqual(system_record.metrics["degraded_feature_count"], 2)
            self.assertEqual(system_record.outputs[0].kind.value, "system")
