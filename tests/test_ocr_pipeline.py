from __future__ import annotations

import hashlib
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import ValidationError

from video2notes.domain import (
    ArtifactKind,
    ArtifactRef,
    Rational,
    VisualState,
)
from video2notes.ocr import (
    BackendOcrLine,
    BackendOcrOutput,
    FilesystemArtifactImageLoader,
    OcrArtifactError,
    OcrBox,
    OcrFrameStatus,
    OcrLineDecision,
    OcrModelInvocation,
    OcrPipelineConfig,
    extract_ocr_evidence,
)


class FakeBackend:
    def __init__(self, outputs: list[BackendOcrOutput]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[tuple[int, int], tuple[str, ...]]] = []

    def recognize(
        self,
        image: Image.Image,
        *,
        language_hints: Sequence[str] = (),
    ) -> BackendOcrOutput:
        self.calls.append((image.size, tuple(language_hints)))
        return self.outputs[len(self.calls) - 1]


def _artifact(name: str = "frames/keyframe.png", digest: str = "a" * 64) -> ArtifactRef:
    return ArtifactRef(
        kind=ArtifactKind.VISUAL,
        relative_path=name,
        sha256=digest,
        size_bytes=123,
        media_type="image/png",
    )


def _state(
    state_id: str,
    *,
    start_us: int,
    end_us: int,
    keyframe_us: int,
    pts: int | None,
    artifact: ArtifactRef | None = None,
) -> VisualState:
    return VisualState(
        id=state_id,
        run_id="run-ocr",
        start_us=start_us,
        end_us=end_us,
        transition_us=start_us,
        stable_keyframe_us=keyframe_us,
        transition_pts=pts,
        keyframe_pts=pts,
        stream_time_base=Rational(numerator=1, denominator=90_000),
        keyframe_artifact=artifact if artifact is not None else _artifact(),
        change_reason="text_or_ui_change",
    )


def _invocation() -> OcrModelInvocation:
    return OcrModelInvocation(
        engine="fake-ocr",
        version="1.2.3",
        backend="deterministic-fake",
        detection_model="fake-det",
        recognition_model="fake-rec",
        device="cpu",
        local_models_only=True,
    )


class OcrPipelineTests(unittest.TestCase):
    def test_strict_box_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            OcrBox.model_validate(
                {
                    "x": 1,
                    "y": 2,
                    "width": 3,
                    "height": 4,
                    "guessed_text": "forbidden",
                }
            )

    def test_converts_only_accepted_lines_to_pts_bound_evidence(self) -> None:
        image = Image.new("RGB", (200, 100), "white")
        draw = ImageDraw.Draw(image)
        for x in range(12, 90, 8):
            draw.rectangle((x, 10, x + 3, 32), fill="black")
        backend = FakeBackend(
            [
                BackendOcrOutput(
                    invocation=_invocation(),
                    lines=[
                        BackendOcrLine(
                            raw_text="  Hello   世界  ",
                            box=OcrBox(x=8, y=7, width=88, height=30),
                            confidence=0.98,
                        ),
                        BackendOcrLine(
                            raw_text="uncertain words",
                            box=OcrBox(x=8, y=7, width=88, height=30),
                            confidence=0.31,
                        ),
                        BackendOcrLine(
                            raw_text="engine guessed on blank crop",
                            box=OcrBox(x=110, y=60, width=70, height=20),
                            confidence=0.99,
                        ),
                    ],
                )
            ]
        )
        state = _state(
            "state-irregular",
            start_us=2_013_111,
            end_us=7_910_333,
            keyframe_us=3_777_123,
            pts=339_941,
        )

        bundle = extract_ocr_evidence(
            [state],
            backend=backend,
            image_loader=lambda _: image,
            language_hints=("zh-Hans",),
        )

        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(len(bundle.results), 1)
        result = bundle.results[0]
        self.assertEqual(result.keyframe_pts, 339_941)
        self.assertEqual(result.keyframe_us, 3_777_123)
        self.assertEqual(result.status, OcrFrameStatus.PROCESSED)
        self.assertEqual(
            [line.decision for line in result.lines],
            [
                OcrLineDecision.ACCEPTED,
                OcrLineDecision.ABSTAINED,
                OcrLineDecision.ABSTAINED,
            ],
        )
        self.assertEqual(result.lines[0].raw_text, "  Hello   世界  ")
        self.assertEqual(result.lines[0].normalized_text, "Hello 世界")
        self.assertEqual(result.lines[0].script, "mixed")
        self.assertEqual(result.lines[1].abstain_reason, "low_confidence")
        self.assertEqual(result.lines[2].abstain_reason, "unreadable_crop")

        self.assertEqual(len(bundle.evidence), 1)
        span = bundle.evidence[0]
        self.assertEqual(span.start_us, state.start_us)
        self.assertEqual(span.end_us, state.end_us)
        self.assertEqual(span.language, "zh-Hans")
        self.assertEqual(span.raw_text, "  Hello   世界  ")
        self.assertEqual(span.provenance["keyframe_pts"], 339_941)
        self.assertEqual(
            span.provenance["keyframe_time_base"],
            {"numerator": 1, "denominator": 90_000},
        )
        self.assertTrue(span.provenance["local_models_only"])
        self.assertEqual(len(result.abstentions), 2)

    def test_uses_each_visual_state_once_at_nonuniform_times(self) -> None:
        image = Image.new("RGB", (100, 50), "black")
        times = [111_111, 3_777_123, 19_345_009]
        states = [
            _state(
                f"state-{index}",
                start_us=max(0, timestamp - 100_000),
                end_us=timestamp + 200_000,
                keyframe_us=timestamp,
                pts=timestamp // 10,
            )
            for index, timestamp in enumerate(times)
        ]
        outputs = [
            BackendOcrOutput(
                invocation=_invocation(),
                lines=[
                    BackendOcrLine(
                        raw_text=f"frame {index}",
                        box=OcrBox(x=5, y=5, width=80, height=30),
                        confidence=0.99,
                    )
                ],
            )
            for index in range(3)
        ]
        backend = FakeBackend(outputs)

        bundle = extract_ocr_evidence(
            states,
            backend=backend,
            image_loader=lambda _: image,
            config=OcrPipelineConfig(minimum_crop_readability=0),
        )

        self.assertEqual(len(backend.calls), 3)
        self.assertEqual([result.keyframe_us for result in bundle.results], times)
        self.assertEqual(len(bundle.evidence), 3)

    def test_merges_stable_adjacent_text_with_complete_observation_provenance(self) -> None:
        image = Image.new("RGB", (200, 100), "black")
        states = [
            _state(
                "state-a",
                start_us=0,
                end_us=1_000_000,
                keyframe_us=500_000,
                pts=45_000,
                artifact=_artifact("frames/a.png", "a" * 64),
            ),
            _state(
                "state-b",
                start_us=1_000_000,
                end_us=2_000_000,
                keyframe_us=1_500_000,
                pts=135_000,
                artifact=_artifact("frames/b.png", "b" * 64),
            ),
        ]
        backend = FakeBackend(
            [
                BackendOcrOutput(
                    invocation=_invocation(),
                    lines=[
                        BackendOcrLine(
                            raw_text="Start streaming",
                            box=OcrBox(x=10, y=10, width=120, height=24),
                            confidence=0.91,
                        )
                    ],
                ),
                BackendOcrOutput(
                    invocation=_invocation(),
                    lines=[
                        BackendOcrLine(
                            raw_text="Start streamlng",
                            box=OcrBox(x=12, y=11, width=121, height=24),
                            confidence=0.97,
                        )
                    ],
                ),
            ]
        )

        bundle = extract_ocr_evidence(
            states,
            backend=backend,
            image_loader=lambda _: image,
            config=OcrPipelineConfig(minimum_crop_readability=0),
        )

        self.assertEqual(len(bundle.results), 2)
        self.assertEqual(len(bundle.evidence), 1)
        merged = bundle.evidence[0]
        self.assertEqual(
            merged.id,
            "ocr-track-00001-aggregate-a425fa2fe0e9bd68-evidence",
        )
        self.assertEqual((merged.start_us, merged.end_us), (0, 2_000_000))
        self.assertEqual(merged.raw_text, "Start streamlng")
        self.assertEqual(merged.confidence, 0.97)
        self.assertEqual(len(merged.artifact_refs), 2)
        self.assertEqual(len(merged.bounding_boxes), 2)
        self.assertEqual(merged.provenance["observation_count"], 2)
        self.assertEqual(
            merged.provenance["source_evidence_ids"],
            [
                "ocr-state-a-line-0000-evidence",
                "ocr-state-b-line-0000-evidence",
            ],
        )
        self.assertEqual(
            merged.provenance["representative_evidence_id"],
            "ocr-state-b-line-0000-evidence",
        )
        self.assertNotIn(merged.id, merged.provenance["source_evidence_ids"])
        observations = merged.provenance["observations"]
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0]["provenance"]["keyframe_pts"], 45_000)
        self.assertEqual(observations[1]["provenance"]["keyframe_pts"], 135_000)

    def test_downscales_inference_and_maps_pixel_boxes_back_to_original_frame(self) -> None:
        image = Image.new("RGB", (400, 200), "black")
        backend = FakeBackend(
            [
                BackendOcrOutput(
                    invocation=_invocation(),
                    lines=[
                        BackendOcrLine(
                            raw_text="mapped",
                            box=OcrBox(x=10, y=5, width=80, height=20),
                            confidence=0.99,
                        )
                    ],
                )
            ]
        )
        state = _state(
            "scaled",
            start_us=0,
            end_us=1_000_000,
            keyframe_us=500_000,
            pts=45_000,
        )

        bundle = extract_ocr_evidence(
            [state],
            backend=backend,
            image_loader=lambda _: image,
            config=OcrPipelineConfig(
                minimum_crop_readability=0,
                inference_max_width=100,
            ),
        )

        self.assertEqual(backend.calls[0][0], (100, 50))
        line_box = bundle.results[0].accepted_lines[0].box
        self.assertEqual(
            (line_box.x, line_box.y, line_box.width, line_box.height),
            (40.0, 20.0, 320.0, 80.0),
        )
        evidence_box = bundle.evidence[0].bounding_boxes[0]
        self.assertEqual(
            (evidence_box.x, evidence_box.y, evidence_box.width, evidence_box.height),
            (40.0, 20.0, 320.0, 80.0),
        )
        self.assertEqual(bundle.evidence[0].provenance["frame_width"], 400)
        self.assertEqual(bundle.evidence[0].provenance["inference_width"], 100)

    def test_does_not_merge_across_unobserved_time_or_distant_layout_slots(self) -> None:
        image = Image.new("RGB", (300, 120), "black")
        states = [
            _state(
                "state-a",
                start_us=0,
                end_us=1_000_000,
                keyframe_us=500_000,
                pts=45_000,
            ),
            _state(
                "state-b",
                start_us=2_000_000,
                end_us=3_000_000,
                keyframe_us=2_500_000,
                pts=225_000,
            ),
            _state(
                "state-c",
                start_us=3_000_000,
                end_us=4_000_000,
                keyframe_us=3_500_000,
                pts=315_000,
            ),
        ]
        backend = FakeBackend(
            [
                BackendOcrOutput(
                    invocation=_invocation(),
                    lines=[
                        BackendOcrLine(
                            raw_text="Stable label",
                            box=OcrBox(x=10, y=10, width=100, height=20),
                            confidence=0.99,
                        )
                    ],
                ),
                BackendOcrOutput(
                    invocation=_invocation(),
                    lines=[
                        BackendOcrLine(
                            raw_text="Stable label",
                            box=OcrBox(x=12, y=10, width=100, height=20),
                            confidence=0.99,
                        )
                    ],
                ),
                BackendOcrOutput(
                    invocation=_invocation(),
                    lines=[
                        BackendOcrLine(
                            raw_text="Stable label",
                            box=OcrBox(x=180, y=80, width=100, height=20),
                            confidence=0.99,
                        )
                    ],
                ),
            ]
        )

        bundle = extract_ocr_evidence(
            states,
            backend=backend,
            image_loader=lambda _: image,
            config=OcrPipelineConfig(minimum_crop_readability=0),
        )

        self.assertEqual(len(bundle.evidence), 3)
        self.assertEqual(
            [(item.start_us, item.end_us) for item in bundle.evidence],
            [(0, 1_000_000), (2_000_000, 3_000_000), (3_000_000, 4_000_000)],
        )

    def test_missing_pts_abstains_without_calling_backend(self) -> None:
        backend = FakeBackend([])
        state = _state(
            "missing-pts",
            start_us=0,
            end_us=1_000_000,
            keyframe_us=500_000,
            pts=None,
        )

        bundle = extract_ocr_evidence(
            [state],
            backend=backend,
            image_loader=lambda _: Image.new("RGB", (10, 10)),
        )

        self.assertEqual(backend.calls, [])
        self.assertEqual(bundle.evidence, [])
        self.assertEqual(bundle.results[0].status, OcrFrameStatus.ABSTAINED)
        self.assertEqual(
            bundle.results[0].abstentions[0].reason,
            "missing_exact_keyframe_timestamp",
        )

    def test_filesystem_loader_verifies_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "frames" / "one.png"
            path.parent.mkdir()
            Image.new("RGB", (16, 12), "navy").save(path)
            payload = path.read_bytes()
            artifact = _artifact(
                "frames/one.png",
                hashlib.sha256(payload).hexdigest(),
            )
            loader = FilesystemArtifactImageLoader(root)

            self.assertEqual(loader(artifact).size, (16, 12))
            invalid = artifact.model_copy(update={"sha256": "0" * 64})
            with self.assertRaises(OcrArtifactError):
                loader(invalid)


if __name__ == "__main__":
    unittest.main()
