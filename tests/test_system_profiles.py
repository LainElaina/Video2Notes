from __future__ import annotations

import subprocess
import unittest
from collections.abc import Sequence

from pydantic import ValidationError

from video2notes.system import (
    ExperienceMode,
    GpuDevice,
    HardwareSnapshot,
    HardwareTier,
    PerformanceOverrides,
    QualityMode,
    ResourcePreference,
    ResourceReserve,
    SecondaryAsrPolicy,
    build_execution_plan,
    detect_hardware,
    estimate_processing_time,
    recommend_hardware_tier,
    recommend_resources,
)


def snapshot(
    *,
    vram_gib: int | None,
    hwaccels: tuple[str, ...] = ("cuda",),
    free_vram_gib: float | None = None,
    gpu_utilization: float | None = None,
    cpu_load: float | None = None,
    memory_available_gib: float | None = None,
) -> HardwareSnapshot:
    gpus = (
        (
            GpuDevice(
                name="Test GPU",
                vendor="NVIDIA",
                memory_total_bytes=vram_gib * 1024**3,
                memory_free_bytes=(
                    round(free_vram_gib * 1024**3)
                    if free_vram_gib is not None
                    else None
                ),
                utilization_percent=gpu_utilization,
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
        cpu_load_percent=cpu_load,
        memory_total_bytes=32 * 1024**3,
        memory_available_bytes=(
            round(memory_available_gib * 1024**3)
            if memory_available_gib is not None
            else None
        ),
        disk_total_bytes=1024 * 1024**3,
        disk_available_bytes=512 * 1024**3,
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
        gpu = result.primary_gpu
        self.assertIsNotNone(gpu)
        assert gpu is not None
        self.assertEqual(gpu.name, "NVIDIA GeForce RTX 5090 D v2")
        self.assertEqual(
            gpu.memory_total_bytes,
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

    def test_live_nvidia_headroom_is_parsed_when_available(self) -> None:
        def runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if command[0] == "nvidia-smi":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "Test GPU, 12288, 9216, 3072, 37, 600.1\n",
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "cuda\n", "")

        result = detect_hardware(runner=runner)
        gpu = result.primary_gpu
        self.assertIsNotNone(gpu)
        assert gpu is not None
        self.assertEqual(gpu.memory_free_bytes, 9216 * 1024 * 1024)
        self.assertEqual(gpu.memory_used_bytes, 3072 * 1024 * 1024)
        self.assertEqual(gpu.utilization_percent, 37)


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

    def test_same_machine_with_low_live_headroom_reduces_concurrency(self) -> None:
        idle = snapshot(
            vram_gib=24,
            free_vram_gib=22,
            gpu_utilization=5,
            cpu_load=5,
            memory_available_gib=28,
        )
        busy = snapshot(
            vram_gib=24,
            free_vram_gib=4,
            gpu_utilization=85,
            cpu_load=85,
            memory_available_gib=5,
        )

        idle_plan = build_execution_plan(idle, QualityMode.BALANCED)
        busy_plan = build_execution_plan(busy, QualityMode.BALANCED)

        self.assertGreater(idle_plan.concurrent_gpu_stages, busy_plan.concurrent_gpu_stages)
        self.assertGreater(idle_plan.cpu_workers, busy_plan.cpu_workers)
        self.assertGreater(
            idle_plan.resource_budget.memory_budget_bytes or 0,
            busy_plan.resource_budget.memory_budget_bytes or 0,
        )

    def test_larger_user_reserve_reduces_effective_budget(self) -> None:
        machine = snapshot(
            vram_gib=24,
            free_vram_gib=20,
            gpu_utilization=0,
            cpu_load=0,
            memory_available_gib=24,
        )
        low_reserve = ResourceReserve(
            cpu_reserve_ratio=0.10,
            memory_reserve_ratio=0.10,
            gpu_reserve_ratio=0.10,
            vram_reserve_ratio=0.10,
        )
        high_reserve = ResourceReserve(
            cpu_reserve_ratio=0.60,
            memory_reserve_ratio=0.60,
            gpu_reserve_ratio=0.60,
            vram_reserve_ratio=0.60,
        )

        low = recommend_resources(machine, reserve=low_reserve).budget
        high = recommend_resources(machine, reserve=high_reserve).budget

        self.assertGreater(low.cpu_workers, high.cpu_workers)
        self.assertGreater(low.memory_budget_bytes or 0, high.memory_budget_bytes or 0)
        self.assertGreater(low.vram_budget_bytes or 0, high.vram_budget_bytes or 0)
        self.assertGreater(low.gpu_stage_slots, high.gpu_stage_slots)

    def test_professional_overrides_apply_when_inside_safe_budget(self) -> None:
        machine = snapshot(
            vram_gib=24,
            free_vram_gib=22,
            gpu_utilization=0,
            cpu_load=0,
            memory_available_gib=28,
        )
        overrides = PerformanceOverrides(
            concurrent_gpu_stages=1,
            cpu_workers=4,
            remote_model_concurrency=1,
            visual_decode_threads=3,
            analysis_width=640,
            cheap_scan_fps=1.5,
            expensive_scan_fps=5,
            ocr_batch_size=2,
            asr_batch_size=2,
            asr_beam_size=8,
            verification_passes=3,
            screenshot_budget_per_section=6,
        )

        plan = build_execution_plan(
            machine,
            QualityMode.ACCURATE,
            experience_mode=ExperienceMode.PROFESSIONAL,
            preference=ResourcePreference.THROUGHPUT,
            overrides=overrides,
        )

        self.assertEqual(plan.concurrent_gpu_stages, 1)
        self.assertEqual(plan.cpu_workers, 4)
        self.assertEqual(plan.remote_model_concurrency, 1)
        self.assertEqual(plan.visual_decode_threads, 3)
        self.assertEqual(plan.analysis_width, 640)
        self.assertEqual(plan.cheap_scan_fps, 1.5)
        self.assertEqual(plan.expensive_scan_fps, 5)
        self.assertEqual(plan.ocr_batch_size, 2)
        self.assertEqual(plan.asr_batch_size, 2)
        self.assertEqual(plan.asr_beam_size, 8)
        self.assertEqual(plan.verification_passes, 3)
        self.assertEqual(plan.screenshot_budget_per_section, 6)

    def test_unsafe_professional_override_is_clamped_with_warning(self) -> None:
        machine = snapshot(
            vram_gib=8,
            free_vram_gib=3,
            gpu_utilization=80,
            cpu_load=75,
            memory_available_gib=5,
        )
        plan = build_execution_plan(
            machine,
            QualityMode.BALANCED,
            experience_mode=ExperienceMode.PROFESSIONAL,
            overrides=PerformanceOverrides(
                concurrent_gpu_stages=8,
                cpu_workers=256,
                analysis_width=1920,
                ocr_device="cuda",
            ),
        )

        self.assertLess(plan.cpu_workers, 256)
        self.assertEqual(plan.concurrent_gpu_stages, 0)
        self.assertLess(plan.analysis_width, 1920)
        self.assertEqual(plan.ocr_device, "cpu")
        self.assertTrue(any("clamped" in note for note in plan.notes))

    def test_invalid_resource_and_override_ranges_fail_validation(self) -> None:
        with self.assertRaises(ValidationError):
            ResourceReserve(cpu_reserve_ratio=0.95)
        with self.assertRaises(ValidationError):
            PerformanceOverrides(cheap_scan_fps=10, expensive_scan_fps=5)
        with self.assertRaises(ValueError):
            build_execution_plan(
                snapshot(vram_gib=24),
                QualityMode.BALANCED,
                overrides=PerformanceOverrides(cpu_workers=2),
            )
