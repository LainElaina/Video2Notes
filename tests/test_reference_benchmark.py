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
from video2notes.system import AccelerationCapabilities, EngineAcceleration


def _model_directory(root: Path, name: str, payload_name: str) -> Path:
    directory = root / name
    directory.mkdir()
    (directory / payload_name).write_bytes(f"payload-{name}".encode())
    return directory


def _mixed_acceleration() -> AccelerationCapabilities:
    return AccelerationCapabilities(
        asr=EngineAcceleration(
            engine="faster-whisper/CTranslate2",
            cuda_available=True,
            device_count=1,
            supported_compute_types=("float16",),
            reason="fixture CUDA ready",
        ),
        ocr=EngineAcceleration(
            engine="PaddleOCR/PaddlePaddle",
            cuda_available=False,
            reason="fixture CPU Paddle runtime",
        ),
    )


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
                patch(
                    "video2notes.evaluation.reference_benchmark.write_reference_analysis",
                    return_value=(
                        root / "session" / "detailed-comparison.json",
                        root / "session" / "detailed-comparison.md",
                    ),
                ) as detailed,
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
            self.assertEqual(detailed.call_count, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "# comparison\n")
            manifest = json.loads(
                (root / "session" / "benchmark-manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["resource_policy"]["force_cpu"])
            self.assertEqual(manifest["resource_policy"]["max_cpu_ratio"], 0.25)
            self.assertIn("python", manifest["runtime"])
            self.assertIn("git_commit", manifest["runtime"])
            self.assertIn("asr", manifest["acceleration_probe"])
            self.assertIn("ocr", manifest["acceleration_probe"])

    def test_cuda_asr_session_keeps_cpu_ocr_and_gpu_watchdog(self) -> None:
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
                run_directory = root / "session" / "runs" / profile / f"run-{profile}"
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
                config = kwargs["config"]
                self.assertFalse(config.force_cpu)  # type: ignore[attr-defined]
                self.assertEqual(  # type: ignore[attr-defined]
                    config.gpu_watchdog_percent,
                    97.0,
                )
                self.assertEqual(config.gpu_breach_samples, 12)  # type: ignore[attr-defined]
                report = Mock()
                report.exit_code = 0
                report.termination_reason = None
                report.model_dump_json.return_value = "{}"
                return report

            with (
                patch(
                    "video2notes.evaluation.reference_benchmark.run_guarded_benchmark",
                    side_effect=guarded,
                ),
                patch(
                    "video2notes.evaluation.reference_benchmark.compare_runs",
                    return_value=object(),
                ),
                patch(
                    "video2notes.evaluation.reference_benchmark.render_json",
                    return_value="{}",
                ),
                patch(
                    "video2notes.evaluation.reference_benchmark.render_comparison_markdown",
                    return_value="# comparison",
                ),
                patch(
                    "video2notes.evaluation.reference_benchmark.write_reference_analysis",
                    return_value=(
                        root / "session" / "detailed-comparison.json",
                        root / "session" / "detailed-comparison.md",
                    ),
                ),
                patch(
                    "video2notes.evaluation.reference_benchmark.detect_acceleration_capabilities",
                    return_value=_mixed_acceleration(),
                ),
            ):
                run_reference_session(
                    source=source,
                    session_root=root / "session",
                    asr_model_dir=asr,
                    ocr_detection_model_dir=detection,
                    ocr_recognition_model_dir=recognition,
                    python_executable=Path(__file__),
                    working_directory=root,
                    asr_device="cuda",
                    asr_compute_type="float16",
                    ocr_device="cpu",
                    gpu_watchdog_percent=97.0,
                    gpu_breach_samples=12,
                )

            self.assertEqual(len(commands), 3)
            for command in commands:
                self.assertEqual(command[command.index("--asr-device") + 1], "cuda")
                self.assertEqual(
                    command[command.index("--asr-compute-type") + 1],
                    "float16",
                )
                self.assertEqual(command[command.index("--ocr-device") + 1], "cpu")

            registry = ModelRegistry.load(
                root / "session" / "data" / "config" / "providers.json"
            )
            self.assertEqual(registry.models["faster-whisper"].settings["device"], "cuda")
            self.assertEqual(
                registry.models["faster-whisper"].settings["compute_type"],
                "float16",
            )
            self.assertEqual(registry.models["paddleocr"].settings["device"], "cpu")
            manifest = json.loads(
                (root / "session" / "benchmark-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            policy = manifest["resource_policy"]
            self.assertFalse(policy["force_cpu"])
            self.assertEqual(policy["requested_asr_device"], "cuda")
            self.assertEqual(policy["requested_asr_compute_type"], "float16")
            self.assertEqual(policy["requested_ocr_device"], "cpu")
            self.assertTrue(manifest["acceleration_probe"]["asr"]["cuda_available"])
            self.assertFalse(manifest["acceleration_probe"]["ocr"]["cuda_available"])

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
