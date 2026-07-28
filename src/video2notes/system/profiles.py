"""Execution budgets composed from hardware capacity and user quality intent."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .hardware import HardwareSnapshot, HardwareTier, recommend_hardware_tier


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QualityMode(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    ACCURATE = "accurate"


class SecondaryAsrPolicy(StrEnum):
    OFF = "off"
    CONFLICTS_ONLY = "conflicts_only"
    UNCERTAIN_AND_CONFLICTS = "uncertain_and_conflicts"


class ExecutionPlan(ProfileModel):
    hardware_tier: HardwareTier
    quality_mode: QualityMode
    decode_backend: str
    concurrent_gpu_stages: int = Field(ge=0)
    analysis_width: int = Field(ge=320)
    cheap_scan_fps: float = Field(gt=0)
    expensive_scan_fps: float = Field(gt=0)
    ocr_model_class: str
    asr_model_class: str
    asr_compute_type: str
    secondary_asr: SecondaryAsrPolicy
    verification_passes: int = Field(ge=0)
    screenshot_budget_per_section: int = Field(ge=0)
    learned_scene_detector: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _HardwareBudget:
    decode_backend: str
    concurrent_gpu_stages: int
    analysis_width_cap: int
    ocr_model_cap: str
    asr_compute_type: str


@dataclass(frozen=True)
class _QualityBudget:
    analysis_width: int
    cheap_scan_fps: float
    expensive_scan_fps: float
    ocr_model_class: str
    asr_model_class: str
    secondary_asr: SecondaryAsrPolicy
    verification_passes: int
    screenshot_budget_per_section: int
    learned_scene_detector: bool


_HARDWARE_BUDGETS: dict[HardwareTier, _HardwareBudget] = {
    HardwareTier.CPU_IGPU: _HardwareBudget(
        decode_backend="software",
        concurrent_gpu_stages=0,
        analysis_width_cap=640,
        ocr_model_cap="mobile",
        asr_compute_type="int8",
    ),
    HardwareTier.GPU_8GB: _HardwareBudget(
        decode_backend="auto_hw",
        concurrent_gpu_stages=1,
        analysis_width_cap=768,
        ocr_model_cap="mobile",
        asr_compute_type="int8_float16",
    ),
    HardwareTier.GPU_12GB: _HardwareBudget(
        decode_backend="auto_hw",
        concurrent_gpu_stages=1,
        analysis_width_cap=960,
        ocr_model_cap="medium",
        asr_compute_type="float16",
    ),
    HardwareTier.GPU_24GB_PLUS: _HardwareBudget(
        decode_backend="auto_hw",
        concurrent_gpu_stages=3,
        analysis_width_cap=1280,
        ocr_model_cap="medium",
        asr_compute_type="float16",
    ),
}


_QUALITY_BUDGETS: dict[QualityMode, _QualityBudget] = {
    QualityMode.FAST: _QualityBudget(
        analysis_width=480,
        cheap_scan_fps=2.0,
        expensive_scan_fps=6.0,
        ocr_model_class="mobile",
        asr_model_class="small",
        secondary_asr=SecondaryAsrPolicy.OFF,
        verification_passes=0,
        screenshot_budget_per_section=0,
        learned_scene_detector=False,
    ),
    QualityMode.BALANCED: _QualityBudget(
        analysis_width=768,
        cheap_scan_fps=3.0,
        expensive_scan_fps=12.0,
        ocr_model_class="mobile",
        asr_model_class="large-v3-turbo",
        secondary_asr=SecondaryAsrPolicy.CONFLICTS_ONLY,
        verification_passes=1,
        screenshot_budget_per_section=2,
        learned_scene_detector=False,
    ),
    QualityMode.ACCURATE: _QualityBudget(
        analysis_width=1080,
        cheap_scan_fps=6.0,
        expensive_scan_fps=24.0,
        ocr_model_class="medium",
        asr_model_class="large-v3",
        secondary_asr=SecondaryAsrPolicy.UNCERTAIN_AND_CONFLICTS,
        verification_passes=2,
        screenshot_budget_per_section=4,
        learned_scene_detector=True,
    ),
}


def build_execution_plan(
    snapshot: HardwareSnapshot,
    quality_mode: QualityMode,
    *,
    hardware_tier: HardwareTier | None = None,
) -> ExecutionPlan:
    """Compose a safe plan without silently downgrading the requested quality."""

    tier = hardware_tier or recommend_hardware_tier(snapshot)
    hardware = _HARDWARE_BUDGETS[tier]
    quality = _QUALITY_BUDGETS[quality_mode]

    requested_width = quality.analysis_width
    width_cap = hardware.analysis_width_cap
    notes: list[str] = []
    if requested_width > width_cap:
        notes.append(
            "Requested quality is preserved, but visual analysis resolution is "
            f"capped at {width_cap}px for memory safety; uncertain regions escalate."
        )

    requested_ocr = quality.ocr_model_class
    ocr_cap = hardware.ocr_model_cap
    ocr_model = requested_ocr
    if requested_ocr == "medium" and ocr_cap == "mobile":
        ocr_model = "mobile"
        notes.append(
            "Medium OCR exceeds the concurrent memory budget; use mobile OCR "
            "for the first pass and medium/cloud OCR only for uncertain crops."
        )

    decode_backend = hardware.decode_backend
    if decode_backend == "auto_hw" and not snapshot.ffmpeg_hwaccels:
        decode_backend = "software"
        notes.append("FFmpeg reports no hardware decoder; software decoding will be used.")

    return ExecutionPlan(
        hardware_tier=tier,
        quality_mode=quality_mode,
        decode_backend=decode_backend,
        concurrent_gpu_stages=hardware.concurrent_gpu_stages,
        analysis_width=min(requested_width, width_cap),
        cheap_scan_fps=quality.cheap_scan_fps,
        expensive_scan_fps=quality.expensive_scan_fps,
        ocr_model_class=ocr_model,
        asr_model_class=quality.asr_model_class,
        asr_compute_type=hardware.asr_compute_type,
        secondary_asr=quality.secondary_asr,
        verification_passes=quality.verification_passes,
        screenshot_budget_per_section=quality.screenshot_budget_per_section,
        learned_scene_detector=quality.learned_scene_detector,
        notes=tuple(notes),
    )
