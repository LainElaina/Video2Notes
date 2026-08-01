from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video2notes.audio import FasterWhisperBackend, FasterWhisperConfig
from video2notes.domain import ArtifactKind, VisualState
from video2notes.ocr import (
    OcrEvidenceBundle,
    OcrTrackingResult,
    PaddleOcrBackend,
    PaddleOcrConfig,
    ScrollFrameSelection,
)
from video2notes.pipeline import PipelineRequest, Video2NotesPipeline
from video2notes.pipeline.runner import _align_execution_plan_with_backends
from video2notes.sources import CancellationToken, SourceInput
from video2notes.system import (
    GpuDevice,
    HardwareSnapshot,
    QualityMode,
    build_execution_plan,
)


def high_end_snapshot() -> HardwareSnapshot:
    return HardwareSnapshot(
        os_name="Windows",
        os_version="test",
        architecture="AMD64",
        cpu_name="fixture",
        logical_cores=24,
        memory_total_bytes=64 * 1024**3,
        memory_available_bytes=48 * 1024**3,
        disk_total_bytes=2 * 1024**4,
        disk_available_bytes=1024**4,
        gpus=(
            GpuDevice(
                name="fixture",
                vendor="NVIDIA",
                memory_total_bytes=24 * 1024**3,
                memory_free_bytes=20 * 1024**3,
            ),
        ),
        ffmpeg_hwaccels=("cuda",),
    )


def empty_ocr_bundle() -> OcrEvidenceBundle:
    return OcrEvidenceBundle(
        results=[],
        evidence=[],
        tracking=OcrTrackingResult(),
        scroll_selection=ScrollFrameSelection(
            all_unique_tokens=[],
            selected_frames=[],
            covered_tokens=[],
            uncovered_tokens=[],
            coverage_ratio=1.0,
            candidate_frame_count=0,
        ),
    )


class StubOcrBackend:
    model_id = "stub-ocr"


class PipelineExecutionPlanTests(unittest.TestCase):
    def test_effective_plan_reports_selected_local_model_classes(self) -> None:
        preferred = build_execution_plan(
            high_end_snapshot(),
            QualityMode.ACCURATE,
        )
        self.assertEqual(preferred.asr_model_class, "large-v3")
        self.assertEqual(preferred.ocr_model_class, "server")
        asr = FasterWhisperBackend(
            FasterWhisperConfig(
                model_path=r"D:\models\faster-whisper-small",
            )
        )
        detector = r"D:\models\paddleocr\PP-OCRv5_mobile_det_infer"
        recognizer = r"D:\models\paddleocr\PP-OCRv5_mobile_rec_infer"
        ocr = PaddleOcrBackend(
            PaddleOcrConfig(
                detection_model_dir=detector,
                recognition_model_dir=recognizer,
            )
        )

        effective = _align_execution_plan_with_backends(
            preferred,
            primary_asr_backend=asr,
            ocr_backend=ocr,
        )

        self.assertEqual(effective.asr_model_class, "small")
        self.assertEqual(effective.ocr_model_class, "mobile")
        self.assertTrue(
            any(
                "ASR model class was downgraded from 'large-v3' to 'small'" in note
                for note in effective.notes
            )
        )
        self.assertTrue(
            any(
                "OCR model class was downgraded from 'server' to 'mobile'" in note
                for note in effective.notes
            )
        )

    def test_unavailable_backends_are_explicit_in_effective_plan(self) -> None:
        preferred = build_execution_plan(
            high_end_snapshot(),
            QualityMode.BALANCED,
        )

        effective = _align_execution_plan_with_backends(
            preferred,
            primary_asr_backend=None,
            ocr_backend=None,
        )

        self.assertEqual(effective.asr_model_class, "unavailable")
        self.assertEqual(effective.ocr_model_class, "unavailable")
        self.assertEqual(sum("was downgraded" in note for note in effective.notes), 2)

    def test_ocr_inference_width_is_passed_and_invalidates_stage_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pipeline = Video2NotesPipeline(root / "runs")
            request = PipelineRequest(source=SourceInput.local(root / "input.mp4"))
            workspace = pipeline.create_run(request, run_id="ocr-width")
            visual_manifest = workspace.artifact_path("vision", "visual-states.json")
            visual_manifest.write_text("[]", encoding="utf-8")
            visual_ref = workspace.ref_for(visual_manifest, kind=ArtifactKind.VISUAL)
            states = [
                VisualState(
                    id="state-1",
                    run_id=workspace.manifest.run_id,
                    start_us=0,
                    end_us=1_000_000,
                    transition_us=0,
                    stable_keyframe_us=500_000,
                    change_reason="fixture",
                )
            ]
            preferred = build_execution_plan(
                high_end_snapshot(),
                QualityMode.BALANCED,
            )
            backend = StubOcrBackend()

            with patch(
                "video2notes.pipeline.runner.extract_ocr_evidence",
                return_value=empty_ocr_bundle(),
            ) as extract:
                pipeline._extract_ocr(
                    workspace,
                    states,
                    visual_ref,
                    request,
                    backend,
                    preferred.model_copy(update={"ocr_inference_max_width": 640}),
                    CancellationToken(),
                    lambda *args, **kwargs: None,
                )
                pipeline._extract_ocr(
                    workspace,
                    states,
                    visual_ref,
                    request,
                    backend,
                    preferred.model_copy(update={"ocr_inference_max_width": 768}),
                    CancellationToken(),
                    lambda *args, **kwargs: None,
                )

            self.assertEqual(extract.call_count, 2)
            self.assertEqual(
                [call.kwargs["config"].inference_max_width for call in extract.call_args_list],
                [640, 768],
            )
            stage = workspace.manifest.stages["ocr.extract"]
            self.assertEqual(stage.stage_version, "4")
            self.assertEqual(stage.attempt, 2)


if __name__ == "__main__":
    unittest.main()
