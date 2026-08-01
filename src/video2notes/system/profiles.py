"""Execution budgets composed from hardware capacity and user quality intent."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .hardware import HardwareSnapshot, HardwareTier, recommend_hardware_tier
from .resources import (
    GIB,
    ExperienceMode,
    ResourceBudget,
    ResourcePreference,
    ResourceReserve,
    recommend_resources,
)


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
    experience_mode: ExperienceMode
    resource_preference: ResourcePreference
    resource_budget: ResourceBudget
    decode_backend: str
    concurrent_gpu_stages: int = Field(ge=0)
    cpu_workers: int = Field(ge=1)
    remote_model_concurrency: int = Field(ge=1)
    visual_decode_threads: int = Field(ge=1)
    max_fixed_samples: int = Field(ge=1)
    analysis_width: int = Field(ge=320)
    ocr_inference_max_width: int = Field(ge=320)
    cheap_scan_fps: float = Field(gt=0)
    expensive_scan_fps: float = Field(gt=0)
    ocr_model_class: str
    ocr_device: str
    ocr_batch_size: int = Field(ge=1)
    ocr_cpu_threads: int = Field(ge=1)
    asr_model_class: str
    asr_device: str
    asr_compute_type: str
    asr_batch_size: int = Field(ge=1)
    asr_cpu_threads: int = Field(ge=1)
    asr_beam_size: int = Field(ge=1)
    secondary_asr: SecondaryAsrPolicy
    verification_passes: int = Field(ge=0)
    screenshot_budget_per_section: int = Field(ge=0)
    learned_scene_detector: bool
    notes: tuple[str, ...] = ()


class PerformanceOverrides(ProfileModel):
    """Validated professional controls; machine-unsafe values are visibly clamped."""

    decode_backend: Literal["software", "auto_hw"] | None = None
    concurrent_gpu_stages: int | None = Field(default=None, ge=0, le=8)
    cpu_workers: int | None = Field(default=None, ge=1, le=256)
    remote_model_concurrency: int | None = Field(default=None, ge=1, le=16)
    visual_decode_threads: int | None = Field(default=None, ge=1, le=64)
    max_fixed_samples: int | None = Field(default=None, ge=1, le=20_000)
    analysis_width: int | None = Field(default=None, ge=320, le=1920)
    ocr_inference_max_width: int | None = Field(default=None, ge=320, le=4096)
    cheap_scan_fps: float | None = Field(default=None, ge=0.25, le=30)
    expensive_scan_fps: float | None = Field(default=None, ge=0.25, le=60)
    ocr_model_class: Literal["mobile", "medium"] | None = None
    ocr_device: Literal["auto", "cpu", "cuda"] | None = None
    ocr_batch_size: int | None = Field(default=None, ge=1, le=64)
    ocr_cpu_threads: int | None = Field(default=None, ge=1, le=64)
    asr_model_class: str | None = Field(default=None, min_length=1, max_length=100)
    asr_device: Literal["auto", "cpu", "cuda"] | None = None
    asr_compute_type: Literal[
        "default",
        "int8",
        "int8_float16",
        "float16",
        "float32",
    ] | None = None
    asr_batch_size: int | None = Field(default=None, ge=1, le=64)
    asr_cpu_threads: int | None = Field(default=None, ge=1, le=64)
    asr_beam_size: int | None = Field(default=None, ge=1, le=10)
    verification_passes: int | None = Field(default=None, ge=0, le=4)
    screenshot_budget_per_section: int | None = Field(default=None, ge=0, le=16)
    learned_scene_detector: bool | None = None

    @model_validator(mode="after")
    def validate_scan_rates(self) -> PerformanceOverrides:
        if (
            self.cheap_scan_fps is not None
            and self.expensive_scan_fps is not None
            and self.expensive_scan_fps < self.cheap_scan_fps
        ):
            raise ValueError("expensive_scan_fps must be at least cheap_scan_fps")
        unsupported = {
            "decode_backend": self.decode_backend,
            "ocr_model_class": self.ocr_model_class,
            "ocr_batch_size": self.ocr_batch_size,
            "asr_model_class": self.asr_model_class,
            "asr_batch_size": self.asr_batch_size,
            "learned_scene_detector": self.learned_scene_detector,
        }
        selected = [name for name, value in unsupported.items() if value is not None]
        if selected:
            raise ValueError(
                "these overrides are unavailable until their execution adapter is "
                f"installed: {', '.join(selected)}"
            )
        return self


@dataclass(frozen=True)
class _HardwareBudget:
    decode_backend: str
    concurrent_gpu_stages: int
    analysis_width_cap: int
    ocr_inference_width_cap: int
    ocr_model_cap: str
    asr_model_cap: str
    asr_compute_type: str


@dataclass(frozen=True)
class _QualityBudget:
    analysis_width: int
    ocr_inference_max_width: int
    cheap_scan_fps: float
    expensive_scan_fps: float
    ocr_model_class: str
    asr_model_class: str
    secondary_asr: SecondaryAsrPolicy
    verification_passes: int
    screenshot_budget_per_section: int
    learned_scene_detector: bool
    asr_beam_size: int
    max_fixed_samples: int


_HARDWARE_BUDGETS: dict[HardwareTier, _HardwareBudget] = {
    HardwareTier.CPU_IGPU: _HardwareBudget(
        decode_backend="software",
        concurrent_gpu_stages=0,
        analysis_width_cap=640,
        ocr_inference_width_cap=1280,
        ocr_model_cap="mobile",
        asr_model_cap="small",
        asr_compute_type="int8",
    ),
    HardwareTier.GPU_8GB: _HardwareBudget(
        decode_backend="software",
        concurrent_gpu_stages=1,
        analysis_width_cap=768,
        ocr_inference_width_cap=1600,
        ocr_model_cap="mobile",
        asr_model_cap="large-v3",
        asr_compute_type="int8_float16",
    ),
    HardwareTier.GPU_12GB: _HardwareBudget(
        decode_backend="software",
        concurrent_gpu_stages=1,
        analysis_width_cap=960,
        ocr_inference_width_cap=1920,
        ocr_model_cap="server",
        asr_model_cap="large-v3",
        asr_compute_type="float16",
    ),
    HardwareTier.GPU_24GB_PLUS: _HardwareBudget(
        decode_backend="software",
        concurrent_gpu_stages=3,
        analysis_width_cap=1280,
        ocr_inference_width_cap=2560,
        ocr_model_cap="server",
        asr_model_cap="large-v3",
        asr_compute_type="float16",
    ),
}


_QUALITY_BUDGETS: dict[QualityMode, _QualityBudget] = {
    QualityMode.FAST: _QualityBudget(
        analysis_width=480,
        ocr_inference_max_width=720,
        cheap_scan_fps=2.0,
        expensive_scan_fps=6.0,
        ocr_model_class="mobile",
        asr_model_class="small",
        secondary_asr=SecondaryAsrPolicy.OFF,
        verification_passes=0,
        screenshot_budget_per_section=0,
        learned_scene_detector=False,
        asr_beam_size=1,
        max_fixed_samples=1_000,
    ),
    QualityMode.BALANCED: _QualityBudget(
        analysis_width=768,
        ocr_inference_max_width=1280,
        cheap_scan_fps=3.0,
        expensive_scan_fps=12.0,
        ocr_model_class="mobile",
        asr_model_class="large-v3-turbo",
        secondary_asr=SecondaryAsrPolicy.CONFLICTS_ONLY,
        verification_passes=1,
        screenshot_budget_per_section=2,
        learned_scene_detector=False,
        asr_beam_size=5,
        max_fixed_samples=3_000,
    ),
    QualityMode.ACCURATE: _QualityBudget(
        analysis_width=1080,
        ocr_inference_max_width=2560,
        cheap_scan_fps=6.0,
        expensive_scan_fps=24.0,
        ocr_model_class="server",
        asr_model_class="large-v3",
        secondary_asr=SecondaryAsrPolicy.UNCERTAIN_AND_CONFLICTS,
        verification_passes=2,
        screenshot_budget_per_section=4,
        learned_scene_detector=False,
        asr_beam_size=5,
        max_fixed_samples=5_000,
    ),
}


def _clamp_override(
    name: str,
    requested: int,
    safe_maximum: int,
    notes: list[str],
) -> int:
    if requested <= safe_maximum:
        return requested
    notes.append(
        f"Professional override {name}={requested} was clamped to the current "
        f"safe budget of {safe_maximum}."
    )
    return safe_maximum


def _safe_batch_size(budget: ResourceBudget) -> int:
    if budget.gpu_stage_slots < 1 or budget.vram_budget_bytes is None:
        return 1
    if budget.vram_budget_bytes >= 12 * GIB:
        return 8
    if budget.vram_budget_bytes >= 8 * GIB:
        return 4
    if budget.vram_budget_bytes >= 4 * GIB:
        return 2
    return 1


def _safe_analysis_width_cap(
    hardware_cap: int,
    budget: ResourceBudget,
) -> int:
    if budget.memory_budget_bytes is None:
        return hardware_cap
    if budget.memory_budget_bytes < 2 * GIB:
        return min(hardware_cap, 480)
    if budget.memory_budget_bytes < 4 * GIB:
        return min(hardware_cap, 640)
    return hardware_cap


def _safe_ocr_inference_width_cap(
    hardware_cap: int,
    budget: ResourceBudget,
) -> int:
    """Keep OCR readable while scaling peak image tensors to live RAM headroom."""

    if budget.memory_budget_bytes is None:
        return hardware_cap
    if budget.memory_budget_bytes < 2 * GIB:
        return min(hardware_cap, 720)
    if budget.memory_budget_bytes < 4 * GIB:
        return min(hardware_cap, 960)
    if budget.memory_budget_bytes < 6 * GIB:
        return min(hardware_cap, 1280)
    if budget.memory_budget_bytes < 8 * GIB:
        return min(hardware_cap, 1600)
    return hardware_cap


def _capped_model_class(
    requested: str,
    maximum: str,
    *,
    order: tuple[str, ...],
) -> str:
    try:
        requested_rank = order.index(requested)
        maximum_rank = order.index(maximum)
    except ValueError:
        return requested
    return order[min(requested_rank, maximum_rank)]


def build_execution_plan(
    snapshot: HardwareSnapshot,
    quality_mode: QualityMode,
    *,
    hardware_tier: HardwareTier | None = None,
    experience_mode: ExperienceMode = ExperienceMode.GUIDED,
    preference: ResourcePreference = ResourcePreference.BALANCED,
    reserve: ResourceReserve | None = None,
    overrides: PerformanceOverrides | None = None,
) -> ExecutionPlan:
    """Compose a safe plan without silently downgrading the requested quality."""

    tier = hardware_tier or recommend_hardware_tier(snapshot)
    hardware = _HARDWARE_BUDGETS[tier]
    quality = _QUALITY_BUDGETS[quality_mode]
    if (
        overrides is not None
        and any(value is not None for value in overrides.model_dump().values())
        and experience_mode is not ExperienceMode.PROFESSIONAL
    ):
        raise ValueError("performance overrides require professional experience mode")
    recommendation = recommend_resources(
        snapshot,
        experience_mode=experience_mode,
        preference=preference,
        reserve=reserve,
    )
    budget = recommendation.budget

    requested_width = (
        overrides.analysis_width
        if overrides is not None and overrides.analysis_width is not None
        else quality.analysis_width
    )
    width_cap = _safe_analysis_width_cap(hardware.analysis_width_cap, budget)
    notes: list[str] = list(recommendation.notes)
    if requested_width > width_cap:
        notes.append(
            f"Requested visual analysis width {requested_width}px was capped at "
            f"{width_cap}px for current memory safety; uncertain regions escalate."
        )

    requested_ocr_width = (
        overrides.ocr_inference_max_width
        if overrides is not None and overrides.ocr_inference_max_width is not None
        else quality.ocr_inference_max_width
    )
    ocr_width_cap = _safe_ocr_inference_width_cap(
        hardware.ocr_inference_width_cap,
        budget,
    )
    if requested_ocr_width > ocr_width_cap:
        notes.append(
            f"Requested OCR inference width {requested_ocr_width}px was capped at "
            f"{ocr_width_cap}px for current memory safety; scene detection resolution "
            "remains independent."
        )

    requested_ocr = (
        overrides.ocr_model_class
        if overrides is not None and overrides.ocr_model_class is not None
        else quality.ocr_model_class
    )
    ocr_cap = hardware.ocr_model_cap
    if budget.memory_budget_bytes is not None and budget.memory_budget_bytes < 4 * GIB:
        ocr_cap = "mobile"
    effective_ocr_model = _capped_model_class(
        requested_ocr,
        ocr_cap,
        order=("mobile", "server"),
    )
    if effective_ocr_model != requested_ocr:
        notes.append(
            f"Requested OCR model class {requested_ocr} was capped at "
            f"{effective_ocr_model} for current hardware and memory safety."
        )

    requested_asr_model = (
        overrides.asr_model_class
        if overrides is not None and overrides.asr_model_class is not None
        else quality.asr_model_class
    )
    effective_asr_model = _capped_model_class(
        requested_asr_model,
        hardware.asr_model_cap,
        order=("small", "large-v3-turbo", "large-v3"),
    )
    if effective_asr_model != requested_asr_model:
        notes.append(
            f"Requested ASR model class {requested_asr_model} was capped at "
            f"{effective_asr_model} for current hardware safety."
        )

    decode_backend = (
        overrides.decode_backend
        if overrides is not None and overrides.decode_backend is not None
        else hardware.decode_backend
    )
    if decode_backend == "auto_hw":
        decode_backend = "software"
        notes.append(
            "Hardware video decode is not exposed by the current PTS-preserving "
            "PyAV adapter; software decoding is used."
        )

    # The current pipeline runs ASR and OCR serially and owns one local engine
    # lease per task. More than one simultaneous GPU stage would overstate the
    # implemented scheduler even on a large GPU.
    safe_gpu_stages = min(hardware.concurrent_gpu_stages, budget.gpu_stage_slots, 1)
    requested_gpu_stages = (
        overrides.concurrent_gpu_stages
        if overrides is not None and overrides.concurrent_gpu_stages is not None
        else safe_gpu_stages
    )
    gpu_stages = _clamp_override(
        "concurrent_gpu_stages",
        requested_gpu_stages,
        safe_gpu_stages,
        notes,
    )

    requested_cpu_workers = (
        overrides.cpu_workers
        if overrides is not None and overrides.cpu_workers is not None
        else budget.cpu_workers
    )
    cpu_workers = _clamp_override(
        "cpu_workers",
        requested_cpu_workers,
        budget.cpu_workers,
        notes,
    )
    requested_remote_concurrency = (
        overrides.remote_model_concurrency
        if overrides is not None and overrides.remote_model_concurrency is not None
        else budget.remote_model_concurrency
    )
    remote_concurrency = _clamp_override(
        "remote_model_concurrency",
        requested_remote_concurrency,
        budget.remote_model_concurrency,
        notes,
    )
    safe_decode_threads = max(1, min(cpu_workers, 8))
    requested_decode_threads = (
        overrides.visual_decode_threads
        if overrides is not None and overrides.visual_decode_threads is not None
        else safe_decode_threads
    )
    decode_threads = _clamp_override(
        "visual_decode_threads",
        requested_decode_threads,
        safe_decode_threads,
        notes,
    )

    safe_fixed_samples = quality.max_fixed_samples
    if budget.memory_budget_bytes is not None and budget.memory_budget_bytes < 2 * GIB:
        safe_fixed_samples = min(safe_fixed_samples, 1_000)
    if budget.disk_budget_bytes is not None and budget.disk_budget_bytes < 2 * GIB:
        safe_fixed_samples = min(safe_fixed_samples, 1_000)
    requested_fixed_samples = (
        overrides.max_fixed_samples
        if overrides is not None and overrides.max_fixed_samples is not None
        else safe_fixed_samples
    )
    fixed_samples = _clamp_override(
        "max_fixed_samples",
        requested_fixed_samples,
        safe_fixed_samples,
        notes,
    )

    # The installed faster-whisper and Paddle adapters are single-item APIs.
    # Keep the effective batch at one until a real batched adapter is selected.
    safe_batch_size = 1
    requested_ocr_batch = (
        overrides.ocr_batch_size
        if overrides is not None and overrides.ocr_batch_size is not None
        else safe_batch_size
    )
    ocr_batch = _clamp_override(
        "ocr_batch_size",
        requested_ocr_batch,
        safe_batch_size,
        notes,
    )
    requested_asr_batch = (
        overrides.asr_batch_size
        if overrides is not None and overrides.asr_batch_size is not None
        else safe_batch_size
    )
    asr_batch = _clamp_override(
        "asr_batch_size",
        requested_asr_batch,
        safe_batch_size,
        notes,
    )

    safe_ocr_threads = max(1, min(cpu_workers, 4))
    requested_ocr_threads = (
        overrides.ocr_cpu_threads
        if overrides is not None and overrides.ocr_cpu_threads is not None
        else safe_ocr_threads
    )
    ocr_threads = _clamp_override(
        "ocr_cpu_threads",
        requested_ocr_threads,
        safe_ocr_threads,
        notes,
    )
    safe_asr_threads = max(1, min(cpu_workers, 8))
    requested_asr_threads = (
        overrides.asr_cpu_threads
        if overrides is not None and overrides.asr_cpu_threads is not None
        else safe_asr_threads
    )
    asr_threads = _clamp_override(
        "asr_cpu_threads",
        requested_asr_threads,
        safe_asr_threads,
        notes,
    )

    default_device = "cuda" if gpu_stages > 0 else "cpu"

    def resolve_device(name: str, requested: str | None) -> str:
        if requested in {None, "auto"}:
            return default_device
        if requested == "cuda" and gpu_stages == 0:
            notes.append(
                f"Professional override {name}=cuda was clamped to cpu because no safe "
                "GPU stage slot is currently available."
            )
            return "cpu"
        assert requested is not None
        return requested

    ocr_device = resolve_device(
        "ocr_device",
        overrides.ocr_device if overrides is not None else None,
    )
    asr_device = resolve_device(
        "asr_device",
        overrides.asr_device if overrides is not None else None,
    )
    requested_compute_type = (
        overrides.asr_compute_type
        if overrides is not None and overrides.asr_compute_type is not None
        else hardware.asr_compute_type
    )
    asr_compute_type = requested_compute_type
    if asr_device == "cpu" and requested_compute_type in {"float16", "int8_float16"}:
        asr_compute_type = "int8"
        notes.append(
            f"ASR compute type {requested_compute_type} was clamped to int8 for CPU execution."
        )

    cheap_scan_fps = (
        overrides.cheap_scan_fps
        if overrides is not None and overrides.cheap_scan_fps is not None
        else quality.cheap_scan_fps
    )
    expensive_scan_fps = (
        overrides.expensive_scan_fps
        if overrides is not None and overrides.expensive_scan_fps is not None
        else quality.expensive_scan_fps
    )
    if expensive_scan_fps < cheap_scan_fps:
        raise ValueError("resolved expensive_scan_fps must be at least cheap_scan_fps")

    return ExecutionPlan(
        hardware_tier=tier,
        quality_mode=quality_mode,
        experience_mode=experience_mode,
        resource_preference=preference,
        resource_budget=budget,
        decode_backend=decode_backend,
        concurrent_gpu_stages=gpu_stages,
        cpu_workers=cpu_workers,
        remote_model_concurrency=remote_concurrency,
        visual_decode_threads=decode_threads,
        max_fixed_samples=fixed_samples,
        analysis_width=min(requested_width, width_cap),
        ocr_inference_max_width=min(requested_ocr_width, ocr_width_cap),
        cheap_scan_fps=cheap_scan_fps,
        expensive_scan_fps=expensive_scan_fps,
        ocr_model_class=effective_ocr_model,
        ocr_device=ocr_device,
        ocr_batch_size=ocr_batch,
        ocr_cpu_threads=ocr_threads,
        asr_model_class=effective_asr_model,
        asr_device=asr_device,
        asr_compute_type=asr_compute_type,
        asr_batch_size=asr_batch,
        asr_cpu_threads=asr_threads,
        asr_beam_size=(
            overrides.asr_beam_size
            if overrides is not None and overrides.asr_beam_size is not None
            else quality.asr_beam_size
        ),
        secondary_asr=quality.secondary_asr,
        verification_passes=(
            overrides.verification_passes
            if overrides is not None and overrides.verification_passes is not None
            else quality.verification_passes
        ),
        screenshot_budget_per_section=(
            overrides.screenshot_budget_per_section
            if overrides is not None and overrides.screenshot_budget_per_section is not None
            else quality.screenshot_budget_per_section
        ),
        learned_scene_detector=(
            overrides.learned_scene_detector
            if overrides is not None and overrides.learned_scene_detector is not None
            else quality.learned_scene_detector
        ),
        notes=tuple(dict.fromkeys(notes)),
    )
