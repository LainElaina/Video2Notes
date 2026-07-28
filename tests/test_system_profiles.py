from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence

from video2notes.system import (
    GpuDevice,
    HardwareSnapshot,
    HardwareTier,
    QualityMode,
    SecondaryAsrPolicy,
    build_execution_plan,
    detect_hardware,
    estimate_processing_time,
    recommend_hardware_tier,
)


def snapshot(
    *,
    vram_gib: int | None,
    hwaccels: tuple[str, ...] = ("cuda",),
) -> HardwareSnapshot:
    gpus = (
        (
            GpuDevice(
                name="Test GPU",
                vendor="NVIDIA",
                memory_total_bytes=vram_gib * 1024**3,
            ),
        )
        if vram_gib is not None
        else ()
    )
    return HardwareSnapshot(
        os_name="Windows",
        os_version="test",
        architecture="AMD64",
        cpu_name="Test CPU",
        logical_cores=16,
        memory_total_bytes=32 * 1024**3,
        gpus=gpus,
        ffmpeg_hwaccels=hwaccels,
    )


class HardwareDetectionTests(unittest.TestCase):
    def test_nvidia_csv_and_ffmpeg_accelerators_are_parsed(self) -> None:
        def runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if command[0] == "nvidia-smi":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "NVIDIA GeForce RTX 5090 D v2, 24455, 591.74\n",
                    "",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                "Hardware acceleration methods:\ncuda\nd3d11va\n",
                "",
            )

        result = detect_hardware(runner=runner)
        self.assertEqual(result.primary_gpu.name, "NVIDIA GeForce RTX 5090 D v2")
        self.assertEqual(
            result.primary_gpu.memory_total_bytes,
            24455 * 1024 * 1024,
        )
        self.assertEqual(result.ffmpeg_hwaccels, ("cuda", "d3d11va"))
        self.assertEqual(
            recommend_hardware_tier(result),
            HardwareTier.GPU_24GB_PLUS,
        )

    def test_missing_external_tools_is_a_valid_cpu_snapshot(self) -> None:
        def runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, "", "not found")

        result = detect_hardware(runner=runner)
        self.assertEqual(result.gpus, ())
        self.assertEqual(result.ffmpeg_hwaccels, ())


class ExecutionProfileTests(unittest.TestCase):
    def test_quality_is_independent_from_powerful_hardware(self) -> None:
        result = build_execution_plan(snapshot(vram_gib=24), QualityMode.FAST)
        self.assertEqual(result.hardware_tier, HardwareTier.GPU_24GB_PLUS)
        self.assertEqual(result.quality_mode, QualityMode.FAST)
        self.assertEqual(result.asr_model_class, "small")
        self.assertEqual(result.secondary_asr, SecondaryAsrPolicy.OFF)
        self.assertEqual(result.concurrent_gpu_stages, 3)

    def test_accurate_mode_on_cpu_preserves_intent_with_safe_escalation(self) -> None:
        result = build_execution_plan(
            snapshot(vram_gib=None, hwaccels=()),
            QualityMode.ACCURATE,
        )
        self.assertEqual(result.hardware_tier, HardwareTier.CPU_IGPU)
        self.assertEqual(result.quality_mode, QualityMode.ACCURATE)
        self.assertEqual(result.analysis_width, 640)
        self.assertEqual(result.ocr_model_class, "mobile")
        self.assertEqual(
            result.secondary_asr,
            SecondaryAsrPolicy.UNCERTAIN_AND_CONFLICTS,
        )
        self.assertEqual(result.verification_passes, 2)
        self.assertGreaterEqual(len(result.notes), 2)

    def test_hardware_decode_is_not_assumed_from_gpu_name(self) -> None:
        result = build_execution_plan(
            snapshot(vram_gib=12, hwaccels=()),
            QualityMode.BALANCED,
        )
        self.assertEqual(result.decode_backend, "software")
        self.assertTrue(any("hardware decoder" in note for note in result.notes))

    def test_estimate_is_a_range_and_keeps_quality_separate(self) -> None:
        machine = snapshot(vram_gib=24)
        fast = estimate_processing_time(3600, machine, QualityMode.FAST)
        accurate = estimate_processing_time(3600, machine, QualityMode.ACCURATE)

        self.assertEqual(fast.hardware_tier, HardwareTier.GPU_24GB_PLUS)
        self.assertLess(fast.upper_seconds, accurate.upper_seconds)
        self.assertLess(fast.lower_seconds, fast.upper_seconds)
        self.assertIn("快速", fast.precision_intent)
        self.assertIn("高精度", accurate.precision_intent)

    def test_estimate_expands_for_4k_high_framerate_video(self) -> None:
        machine = snapshot(vram_gib=8)
        baseline = estimate_processing_time(600, machine, QualityMode.BALANCED)
        difficult = estimate_processing_time(
            600,
            machine,
            QualityMode.BALANCED,
            source_height=2160,
            source_fps=60,
        )

        self.assertGreater(difficult.upper_seconds, baseline.upper_seconds)
        self.assertTrue(any("4K" in note for note in difficult.notes))
        self.assertTrue(any("高帧率" in note for note in difficult.notes))
