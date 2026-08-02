from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video2notes.system.acceleration as acceleration_module
from video2notes.system import (
    AccelerationCapabilities,
    EngineAcceleration,
    GpuDevice,
    HardwareSnapshot,
    QualityMode,
    align_execution_plan_with_acceleration,
    build_execution_plan,
)
from video2notes.system.acceleration import detect_acceleration_capabilities


class _FakeCTranslate2:
    @staticmethod
    def get_cuda_device_count() -> int:
        return 1

    @staticmethod
    def get_supported_compute_types(_device: str) -> set[str]:
        return {"float16", "int8_float16"}


class _CpuPaddleDevice:
    class cuda:
        @staticmethod
        def device_count() -> int:
            raise AssertionError("a CPU Paddle build must not query CUDA devices")

    @staticmethod
    def is_compiled_with_cuda() -> bool:
        return False


class _CpuPaddle:
    device = _CpuPaddleDevice()


class _GpuPaddleDevice:
    class cuda:
        @staticmethod
        def device_count() -> int:
            return 1

    @staticmethod
    def is_compiled_with_cuda() -> bool:
        return True


class _GpuPaddle:
    device = _GpuPaddleDevice()


def _gpu_hardware() -> HardwareSnapshot:
    return HardwareSnapshot(
        os_name="Windows",
        os_version="test",
        architecture="AMD64",
        cpu_name="fixture",
        logical_cores=16,
        memory_total_bytes=32 * 1024**3,
        memory_available_bytes=24 * 1024**3,
        disk_total_bytes=1024**4,
        disk_available_bytes=512 * 1024**3,
        gpus=(
            GpuDevice(
                name="NVIDIA fixture",
                vendor="NVIDIA",
                memory_total_bytes=24 * 1024**3,
                memory_free_bytes=20 * 1024**3,
            ),
        ),
        ffmpeg_hwaccels=("cuda",),
    )


def _engine(
    engine: str,
    *,
    cuda_available: bool,
    reason: str,
) -> EngineAcceleration:
    return EngineAcceleration(
        engine=engine,
        cuda_available=cuda_available,
        device_count=1 if cuda_available else 0,
        supported_compute_types=("float16",) if cuda_available else (),
        reason=reason,
    )


class AccelerationCapabilityTests(unittest.TestCase):
    def tearDown(self) -> None:
        detect_acceleration_capabilities.cache_clear()

    def test_engine_probes_are_independent_for_cuda_asr_and_cpu_ocr(self) -> None:
        import_order: list[str] = []

        def import_module(name: str) -> object:
            import_order.append(name)
            if name == "ctranslate2":
                return _FakeCTranslate2()
            if name == "paddle":
                return _CpuPaddle()
            raise AssertionError(f"unexpected import: {name}")

        detect_acceleration_capabilities.cache_clear()
        with (
            patch.object(
                acceleration_module,
                "prepare_nvidia_cuda_runtime",
                return_value=(),
            ),
            patch.object(
                acceleration_module.importlib,
                "import_module",
                side_effect=import_module,
            ),
            patch.object(acceleration_module, "_load_required_windows_dlls"),
        ):
            result = detect_acceleration_capabilities()

        self.assertTrue(result.asr.cuda_available)
        self.assertEqual(result.asr.device_count, 1)
        self.assertEqual(
            set(result.asr.supported_compute_types),
            {"float16", "int8_float16"},
        )
        self.assertFalse(result.ocr.cuda_available)
        self.assertIn("CPU build", result.ocr.reason)
        self.assertEqual(import_order, ["ctranslate2", "paddle"])

    def test_missing_nvidia_namespace_does_not_break_runtime_discovery(self) -> None:
        with (
            patch.dict(
                acceleration_module.os.environ,
                {"VIDEO2NOTES_NVIDIA_RUNTIME_ROOT": ""},
            ),
            patch.object(
                acceleration_module.importlib.util,
                "find_spec",
                side_effect=ModuleNotFoundError("No module named 'nvidia'"),
            ) as find_spec,
            patch.object(Path, "is_dir", return_value=False),
        ):
            directories = acceleration_module._nvidia_runtime_directories()

        self.assertEqual(directories, ())
        self.assertEqual(
            find_spec.call_count,
            len(acceleration_module._NVIDIA_RUNTIME_PACKAGES),
        )

    def test_complete_explicit_nvidia_runtime_tree_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = []
            for package in acceleration_module._NVIDIA_RUNTIME_PACKAGES:
                directory = root / package / "bin"
                directory.mkdir(parents=True)
                expected.append(directory.resolve())

            with (
                patch.dict(
                    acceleration_module.os.environ,
                    {"VIDEO2NOTES_NVIDIA_RUNTIME_ROOT": str(root)},
                ),
                patch.object(
                    acceleration_module.importlib.util,
                    "find_spec",
                    return_value=None,
                ),
            ):
                directories = acceleration_module._nvidia_runtime_directories()

        self.assertEqual(directories[: len(expected)], tuple(expected))

    def test_gpu_paddle_reports_actual_runtime_directories(self) -> None:
        runtime_directories = (Path("C:/bundled/nvidia/cublas/bin"),)

        def import_module(name: str) -> object:
            if name == "ctranslate2":
                return _FakeCTranslate2()
            if name == "paddle":
                return _GpuPaddle()
            raise AssertionError(f"unexpected import: {name}")

        with (
            patch.object(
                acceleration_module,
                "prepare_nvidia_cuda_runtime",
                return_value=runtime_directories,
            ),
            patch.object(
                acceleration_module.importlib,
                "import_module",
                side_effect=import_module,
            ),
            patch.object(acceleration_module, "_load_required_windows_dlls"),
        ):
            result = detect_acceleration_capabilities()

        self.assertTrue(result.ocr.cuda_available)
        self.assertEqual(
            result.ocr.runtime_directories,
            (str(runtime_directories[0]),),
        )

    def test_ctranslate2_preload_happens_after_runtime_prep(self) -> None:
        calls: list[str] = []

        with (
            patch.object(
                acceleration_module,
                "prepare_nvidia_cuda_runtime",
                side_effect=lambda: calls.append("runtime") or (),
            ),
            patch.object(
                acceleration_module.importlib.util,
                "find_spec",
                return_value=object(),
            ),
            patch.object(
                acceleration_module.importlib,
                "import_module",
                side_effect=lambda name: calls.append(name) or _FakeCTranslate2(),
            ),
        ):
            acceleration_module.preload_ctranslate2_before_paddle()

        self.assertEqual(calls, ["runtime", "ctranslate2"])

    def test_mixed_plan_keeps_cuda_asr_and_falls_back_only_ocr(self) -> None:
        preferred = build_execution_plan(_gpu_hardware(), QualityMode.ACCURATE)
        self.assertEqual(preferred.asr_device, "cuda")
        self.assertEqual(preferred.ocr_device, "cuda")
        capabilities = AccelerationCapabilities(
            asr=_engine(
                "faster-whisper/CTranslate2",
                cuda_available=True,
                reason="ready",
            ),
            ocr=_engine(
                "PaddleOCR/PaddlePaddle",
                cuda_available=False,
                reason="CPU runtime",
            ),
        )

        effective = align_execution_plan_with_acceleration(preferred, capabilities)

        self.assertEqual(effective.asr_device, "cuda")
        self.assertEqual(effective.ocr_device, "cpu")
        self.assertEqual(effective.concurrent_gpu_stages, 1)
        self.assertTrue(any("OCR CUDA" in note for note in effective.notes))
        self.assertFalse(any("ASR CUDA" in note for note in effective.notes))

    def test_no_cuda_runtime_disables_gpu_stages_and_uses_safe_asr_type(self) -> None:
        preferred = build_execution_plan(_gpu_hardware(), QualityMode.BALANCED)
        capabilities = AccelerationCapabilities(
            asr=_engine(
                "faster-whisper/CTranslate2",
                cuda_available=False,
                reason="missing cuBLAS",
            ),
            ocr=_engine(
                "PaddleOCR/PaddlePaddle",
                cuda_available=False,
                reason="CPU runtime",
            ),
        )

        effective = align_execution_plan_with_acceleration(preferred, capabilities)

        self.assertEqual(effective.asr_device, "cpu")
        self.assertEqual(effective.asr_compute_type, "int8")
        self.assertEqual(effective.ocr_device, "cpu")
        self.assertEqual(effective.concurrent_gpu_stages, 0)
        self.assertTrue(any("missing cuBLAS" in note for note in effective.notes))

    def test_unsupported_cuda_compute_type_selects_supported_fallback(self) -> None:
        preferred = build_execution_plan(
            _gpu_hardware(),
            QualityMode.ACCURATE,
        ).model_copy(update={"asr_compute_type": "float16"})
        capabilities = AccelerationCapabilities(
            asr=EngineAcceleration(
                engine="faster-whisper/CTranslate2",
                cuda_available=True,
                device_count=1,
                supported_compute_types=("int8",),
                reason="fixture supports CUDA int8 only",
            ),
            ocr=_engine(
                "PaddleOCR/PaddlePaddle",
                cuda_available=True,
                reason="ready",
            ),
        )

        effective = align_execution_plan_with_acceleration(preferred, capabilities)

        self.assertEqual(effective.asr_device, "cuda")
        self.assertEqual(effective.asr_compute_type, "int8")
        self.assertTrue(
            any(
                "compute type float16 is unsupported; using int8" in note
                for note in effective.notes
            )
        )


if __name__ == "__main__":
    unittest.main()
