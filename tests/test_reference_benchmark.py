from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

from video2notes.evaluation.reference_benchmark import (
    ReferenceBenchmarkError,
    prepare_local_registry,
    run_reference_session,
)
from video2notes.providers import ModelRegistry


def _model_directory(root: Path, name: str, payload_name: str) -> Path:
    directory = root / name
    directory.mkdir()
    (directory / payload_name).write_bytes(f"payload-{name}".encode())
    return directory


class ReferenceBenchmarkTests(unittest.TestCase):
    def test_prepare_registry_uses_validated_local_cpu_models(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asr = _model_directory(root, "asr", "model.bin")
            detection = _model_directory(root, "det", "inference.pdiparams")
            recognition = _model_directory(root, "rec", "inference.pdiparams")

            path = prepare_local_registry(
                root / "data" / "config" / "providers.json",
                asr_model_dir=asr,
                ocr_detection_model_dir=detection,
                ocr_recognition_model_dir=recognition,
            )

            registry = ModelRegistry.load(path)
            self.assertEqual(
                registry.models["faster-whisper"].settings["model_path"],
                str(asr.resolve()),
            )
            self.assertEqual(registry.models["faster-whisper"].settings["device"], "cpu")
            self.assertEqual(registry.models["paddleocr"].settings["language"], "ch")
            self.assertEqual(registry.models["paddleocr"].settings["device"], "cpu")

    def test_prepare_registry_rejects_incomplete_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asr = root / "asr"
            asr.mkdir()
            detection = _model_directory(root, "det", "inference.pdiparams")
            recognition = _model_directory(root, "rec", "inference.pdiparams")

            with self.assertRaises(FileNotFoundError):
                prepare_local_registry(
                    root / "providers.json",
                    asr_model_dir=asr,
                    ocr_detection_model_dir=detection,
                    ocr_recognition_model_dir=recognition,
                )

    def test_session_runs_profiles_serially_and_writes_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"media")
            asr = _model_directory(root, "asr", "model.bin")
            detection = _model_directory(root, "det", "inference.pdiparams")
            recognition = _model_directory(root, "rec", "inference.pdiparams")
            commands: list[list[str]] = []

            def guarded(command: list[str], **kwargs: object) -> Mock:
                commands.append(command)
                profile = command[command.index("--profile") + 1]
                result = Path(command[command.index("--result") + 1])
                run_directory = root / "session" / "runs" / profile / f"reference-{profile}"
                run_directory.mkdir(parents=True)
                result.parent.mkdir(parents=True, exist_ok=True)
                result.write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "profile": profile,
                            "run_directory": str(run_directory),
                        }
                    ),
                    encoding="utf-8",
                )
                report = Mock()
                report.exit_code = 0
                report.termination_reason = None
                report.model_dump_json.return_value = '{"force_cpu":true}'
                config = kwargs["config"]
                self.assertTrue(config.force_cpu)  # type: ignore[attr-defined]
                self.assertLessEqual(config.max_cpu_ratio, 0.5)  # type: ignore[attr-defined]
                return report

            with (
                patch(
                    "video2notes.evaluation.reference_benchmark.run_guarded_benchmark",
                    side_effect=guarded,
                ),
                patch(
                    "video2notes.evaluation.reference_benchmark.compare_runs",
                    return_value=object(),
                ) as compare,
                patch(
                    "video2notes.evaluation.reference_benchmark.render_json",
                    return_value="{}",
                ),
                patch(
                    "video2notes.evaluation.reference_benchmark.render_comparison_markdown",
                    return_value="# comparison",
                ),
            ):
                output = run_reference_session(
                    source=source,
                    session_root=root / "session",
                    asr_model_dir=asr,
                    ocr_detection_model_dir=detection,
                    ocr_recognition_model_dir=recognition,
                    max_cpu_ratio=0.25,
                    python_executable=Path(__file__),
                    working_directory=root,
                )

            self.assertEqual(
                [command[command.index("--profile") + 1] for command in commands],
                ["fast", "balanced", "accurate"],
            )
            self.assertEqual(compare.call_count, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "# comparison\n")
            manifest = json.loads(
                (root / "session" / "benchmark-manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["resource_policy"]["force_cpu"])
            self.assertEqual(manifest["resource_policy"]["max_cpu_ratio"], 0.25)

    def test_session_rejects_cpu_ratio_above_half(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"media")
            asr = _model_directory(root, "asr", "model.bin")
            detection = _model_directory(root, "det", "inference.pdiparams")
            recognition = _model_directory(root, "rec", "inference.pdiparams")

            with self.assertRaises(ValidationError):
                run_reference_session(
                    source=source,
                    session_root=root / "session",
                    asr_model_dir=asr,
                    ocr_detection_model_dir=detection,
                    ocr_recognition_model_dir=recognition,
                    max_cpu_ratio=0.51,
                )

    def test_failed_worker_stops_before_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"media")
            asr = _model_directory(root, "asr", "model.bin")
            detection = _model_directory(root, "det", "inference.pdiparams")
            recognition = _model_directory(root, "rec", "inference.pdiparams")

            def failed(command: list[str], **_: object) -> Mock:
                result = Path(command[command.index("--result") + 1])
                result.parent.mkdir(parents=True, exist_ok=True)
                result.write_text(
                    json.dumps(
                        {
                            "status": "failed",
                            "error_type": "FixtureError",
                            "run_directory": None,
                        }
                    ),
                    encoding="utf-8",
                )
                report = Mock()
                report.exit_code = 1
                report.termination_reason = None
                report.model_dump_json.return_value = "{}"
                return report

            with (
                patch(
                    "video2notes.evaluation.reference_benchmark.run_guarded_benchmark",
                    side_effect=failed,
                ),
                patch(
                    "video2notes.evaluation.reference_benchmark.compare_runs"
                ) as compare,
                self.assertRaisesRegex(ReferenceBenchmarkError, "FixtureError"),
            ):
                run_reference_session(
                    source=source,
                    session_root=root / "session",
                    asr_model_dir=asr,
                    ocr_detection_model_dir=detection,
                    ocr_recognition_model_dir=recognition,
                )
            compare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
