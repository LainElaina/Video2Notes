from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from video2notes.ocr import (
    OcrConfigurationError,
    OcrDependencyError,
    PaddleOcrBackend,
    PaddleOcrConfig,
)


class _FakeV2Engine:
    def ocr(self, _image: object, *, cls: bool) -> object:
        if cls:
            raise AssertionError("angle classifier must stay disabled")
        return [
            [
                [
                    [[10, 20], [110, 20], [110, 50], [10, 50]],
                    ("local text", 0.97),
                ]
            ]
        ]


class _FakeV3Engine:
    def predict(self, _image: object) -> object:
        return [
            {
                "res": {
                    "rec_texts": ["本地模型"],
                    "rec_scores": [0.96],
                    "rec_polys": [
                        [[5, 6], [105, 6], [105, 36], [5, 36]],
                    ],
                }
            }
        ]


def _model_dirs(root: Path) -> tuple[Path, Path]:
    detector = root / "detector"
    recognizer = root / "recognizer"
    detector.mkdir()
    recognizer.mkdir()
    (detector / "inference.pdmodel").write_bytes(b"local-detector")
    (recognizer / "inference.pdmodel").write_bytes(b"local-recognizer")
    return detector, recognizer


class PaddleOcrBackendTests(unittest.TestCase):
    def test_constructor_is_lazy_and_missing_models_fail_without_import(self) -> None:
        backend = PaddleOcrBackend(
            PaddleOcrConfig(
                detection_model_dir="missing-detector",
                recognition_model_dir="missing-recognizer",
            )
        )
        self.assertFalse(backend.loaded)
        with patch("video2notes.ocr.paddle.importlib.import_module") as import_module:
            with self.assertRaises(OcrConfigurationError):
                backend.recognize(Image.new("RGB", (20, 20)))
            import_module.assert_not_called()

    def test_missing_optional_dependency_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            detector, recognizer = _model_dirs(Path(temporary))
            backend = PaddleOcrBackend(
                PaddleOcrConfig(
                    detection_model_dir=str(detector),
                    recognition_model_dir=str(recognizer),
                )
            )
            with (
                patch(
                    "video2notes.ocr.paddle.importlib.import_module",
                    side_effect=ImportError("not installed"),
                ),
                self.assertRaisesRegex(
                    OcrDependencyError,
                    "will not download weights automatically",
                ),
            ):
                backend.recognize(Image.new("RGB", (20, 20)))

    def test_auto_api_falls_back_to_v2_with_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            detector, recognizer = _model_dirs(Path(temporary))
            calls: list[Mapping[str, object]] = []

            def factory(arguments: Mapping[str, object]) -> object:
                calls.append(arguments)
                if "text_detection_model_dir" in arguments:
                    raise TypeError("v3 keywords unsupported")
                return _FakeV2Engine()

            backend = PaddleOcrBackend(
                PaddleOcrConfig(
                    detection_model_dir=str(detector),
                    recognition_model_dir=str(recognizer),
                    api_family="auto",
                ),
                engine_factory=factory,
            )
            with patch.object(
                PaddleOcrBackend,
                "_image_array",
                return_value=object(),
            ):
                output = backend.recognize(Image.new("RGB", (160, 80)))

            self.assertTrue(backend.loaded)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1]["det_model_dir"], str(detector.resolve()))
            self.assertEqual(calls[1]["rec_model_dir"], str(recognizer.resolve()))
            self.assertFalse(calls[1]["use_angle_cls"])
            self.assertEqual(output.lines[0].raw_text, "local text")
            self.assertEqual(output.lines[0].box.width, 100)
            self.assertEqual(output.invocation.backend, "paddleocr-v2")
            self.assertTrue(output.invocation.local_models_only)

    def test_v3_payload_parser_retains_text_box_and_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            detector, recognizer = _model_dirs(Path(temporary))
            backend = PaddleOcrBackend(
                PaddleOcrConfig(
                    detection_model_dir=str(detector),
                    recognition_model_dir=str(recognizer),
                    api_family="v3",
                ),
                engine_factory=lambda _: _FakeV3Engine(),
            )

            with patch.object(
                PaddleOcrBackend,
                "_image_array",
                return_value=object(),
            ):
                output = backend.recognize(Image.new("RGB", (160, 80)))

            self.assertEqual(output.lines[0].raw_text, "本地模型")
            self.assertEqual(output.lines[0].confidence, 0.96)
            self.assertEqual(output.lines[0].box.x, 5)
            self.assertEqual(output.lines[0].box.height, 30)
            self.assertEqual(output.invocation.backend, "paddleocr-v3")


if __name__ == "__main__":
    unittest.main()
