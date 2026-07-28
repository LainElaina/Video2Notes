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
