"""Live-resource budgeting kept independent from the requested quality mode."""

from __future__ import annotations

import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .hardware import GpuDevice, HardwareSnapshot, HardwareTier, recommend_hardware_tier

GIB = 1024**3
_MAX_RESERVE_BYTES = 2 * 1024**4


class ResourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperienceMode(StrEnum):
    """How much scheduling detail the user wants to control."""

    GUIDED = "guided"
    # Backward-compatible source alias for early callers; serialized settings
    # use the same "guided" vocabulary as the desktop experience switch.
    SIMPLE = "guided"
    PROFESSIONAL = "professional"


class ResourcePreference(StrEnum):
    """Independent machine-use intent; this never chooses note quality."""

    RESPONSIVE = "responsive"
    BALANCED = "balanced"
    THROUGHPUT = "throughput"


class ResourceReserve(ResourceModel):
    """Headroom protected from Video2Notes before safety margins are applied."""

    cpu_reserve_ratio: float = Field(default=0.25, ge=0, le=0.90)
    memory_reserve_ratio: float = Field(default=0.25, ge=0, le=0.90)
    gpu_reserve_ratio: float = Field(default=0.20, ge=0, le=0.90)
    vram_reserve_ratio: float = Field(default=0.20, ge=0, le=0.90)
    disk_reserve_ratio: float = Field(default=0.10, ge=0, le=0.90)
    cpu_reserve_cores: int = Field(default=1, ge=0, le=256)
    memory_reserve_bytes: int = Field(default=3 * GIB, ge=0, le=_MAX_RESERVE_BYTES)
    vram_reserve_bytes: int = Field(default=1 * GIB, ge=0, le=_MAX_RESERVE_BYTES)
    disk_reserve_bytes: int = Field(default=10 * GIB, ge=0, le=_MAX_RESERVE_BYTES)
    cpu_safety_factor: float = Field(default=0.90, ge=0.50, le=1.0)
    memory_safety_factor: float = Field(default=0.90, ge=0.50, le=1.0)
    gpu_safety_factor: float = Field(default=0.90, ge=0.50, le=1.0)
    vram_safety_factor: float = Field(default=0.85, ge=0.50, le=1.0)
    disk_safety_factor: float = Field(default=0.90, ge=0.50, le=1.0)

    @classmethod
    def for_preference(cls, preference: ResourcePreference) -> ResourceReserve:
        if preference is ResourcePreference.RESPONSIVE:
            return cls(
                cpu_reserve_ratio=0.45,
                memory_reserve_ratio=0.40,
                gpu_reserve_ratio=0.35,
                vram_reserve_ratio=0.30,
                cpu_reserve_cores=2,
                memory_reserve_bytes=4 * GIB,
                vram_reserve_bytes=1 * GIB,
                disk_reserve_bytes=20 * GIB,
            )
        if preference is ResourcePreference.THROUGHPUT:
            return cls(
                cpu_reserve_ratio=0.10,
                memory_reserve_ratio=0.10,
                gpu_reserve_ratio=0.10,
                vram_reserve_ratio=0.10,
                memory_reserve_bytes=2 * GIB,
                vram_reserve_bytes=512 * 1024**2,
                disk_reserve_bytes=5 * GIB,
            )
        return cls()


class ResourceBudget(ResourceModel):
    """Effective capacity available to new work at one measurement instant."""

    cpu_available_equivalent: float = Field(ge=0)
    cpu_budget_equivalent: float = Field(ge=0)
    cpu_workers: int = Field(ge=1)
    memory_available_bytes: int | None = Field(default=None, ge=0)
    memory_budget_bytes: int | None = Field(default=None, ge=0)
    disk_available_bytes: int | None = Field(default=None, ge=0)
    disk_budget_bytes: int | None = Field(default=None, ge=0)
    gpu_name: str | None = None
    gpu_compute_available_ratio: float | None = Field(default=None, ge=0, le=1)
    gpu_compute_budget_ratio: float | None = Field(default=None, ge=0, le=1)
    vram_available_bytes: int | None = Field(default=None, ge=0)
    vram_budget_bytes: int | None = Field(default=None, ge=0)
    gpu_stage_slots: int = Field(ge=0)
    remote_model_concurrency: int = Field(ge=1)


class ResourceRecommendation(ResourceModel):
    experience_mode: ExperienceMode
    preference: ResourcePreference
    reserve: ResourceReserve
    budget: ResourceBudget
    notes: tuple[str, ...] = ()


def _current_bytes(
    total: int | None,
    available: int | None,
    load_percent: float | None = None,
) -> int | None:
    if available is not None:
        return min(available, total) if total is not None else available
    if total is None:
        return None
    if load_percent is not None:
        return max(0, round(total * (1.0 - load_percent / 100.0)))
    # Older snapshots did not carry live telemetry. Treating total capacity as
    # available preserves their historical conservative tier defaults.
    return total


def _byte_budget(
    available: int | None,
    *,
    reserve_ratio: float,
    reserve_floor: int,
    safety_factor: float,
) -> int | None:
    if available is None:
        return None
    protected = max(reserve_floor, round(available * reserve_ratio))
    return max(0, math.floor((available - protected) * safety_factor))


def _select_gpu(snapshot: HardwareSnapshot) -> GpuDevice | None:
    if not snapshot.gpus:
        return None
    return max(
        snapshot.gpus,
        key=lambda item: (
            item.memory_free_bytes
            if item.memory_free_bytes is not None
            else item.memory_total_bytes or 0,
            item.memory_total_bytes or 0,
        ),
    )


def _gpu_slot_cap(tier: HardwareTier) -> int:
    if tier is HardwareTier.GPU_24GB_PLUS:
        return 3
    if tier in {HardwareTier.GPU_8GB, HardwareTier.GPU_12GB}:
        return 1
    return 0


def _slots_from_vram(vram_budget: int | None) -> int:
    if vram_budget is None or vram_budget < 2 * GIB:
        return 0
    if vram_budget < 8 * GIB:
        return 1
    if vram_budget < 12 * GIB:
        return 2
    return 3


def _slots_from_compute(compute_budget: float | None) -> int:
    if compute_budget is None or compute_budget < 0.10:
        return 0
    if compute_budget < 0.40:
        return 1
    if compute_budget < 0.65:
        return 2
    return 3


def _remote_concurrency(
    *,
    cpu_workers: int,
    memory_budget: int | None,
    preference: ResourcePreference,
) -> int:
    if cpu_workers <= 2 or (memory_budget is not None and memory_budget < 4 * GIB):
        capacity = 1
    elif cpu_workers <= 6 or (memory_budget is not None and memory_budget < 12 * GIB):
        capacity = 2
    else:
        capacity = 4
    preference_cap = {
        ResourcePreference.RESPONSIVE: 1,
        ResourcePreference.BALANCED: 2,
        ResourcePreference.THROUGHPUT: 4,
    }[preference]
    return max(1, min(capacity, preference_cap))


def recommend_resources(
    snapshot: HardwareSnapshot,
    *,
    experience_mode: ExperienceMode = ExperienceMode.GUIDED,
    preference: ResourcePreference = ResourcePreference.BALANCED,
    reserve: ResourceReserve | None = None,
) -> ResourceRecommendation:
    """Combine live headroom, user reserve, and safety factors deterministically."""

    selected_reserve = reserve or ResourceReserve.for_preference(preference)
    notes: list[str] = []

    current_cpu = snapshot.logical_cores * (
        1.0 - (snapshot.cpu_load_percent or 0.0) / 100.0
    )
    protected_cpu = max(
        float(selected_reserve.cpu_reserve_cores),
        current_cpu * selected_reserve.cpu_reserve_ratio,
    )
    cpu_budget = max(
        0.0,
        (current_cpu - protected_cpu) * selected_reserve.cpu_safety_factor,
    )
    cpu_workers = max(1, math.floor(cpu_budget))
    if snapshot.cpu_load_percent is not None and snapshot.cpu_load_percent >= 70:
        notes.append("Current CPU load is high; new local stages are limited to spare cores.")

    memory_available = _current_bytes(
        snapshot.memory_total_bytes,
        snapshot.memory_available_bytes,
        snapshot.memory_load_percent,
    )
    memory_budget = _byte_budget(
        memory_available,
        reserve_ratio=selected_reserve.memory_reserve_ratio,
        reserve_floor=selected_reserve.memory_reserve_bytes,
        safety_factor=selected_reserve.memory_safety_factor,
    )
    if memory_budget is not None and memory_budget < 2 * GIB:
        notes.append("Less than 2 GiB of safe RAM headroom remains; use serial lightweight stages.")

    disk_available = _current_bytes(
        snapshot.disk_total_bytes,
        snapshot.disk_available_bytes,
    )
    disk_budget = _byte_budget(
        disk_available,
        reserve_ratio=selected_reserve.disk_reserve_ratio,
        reserve_floor=selected_reserve.disk_reserve_bytes,
        safety_factor=selected_reserve.disk_safety_factor,
    )

    gpu = _select_gpu(snapshot)
    gpu_compute_available: float | None = None
    gpu_compute_budget: float | None = None
    vram_available: int | None = None
    vram_budget: int | None = None
    gpu_slots = 0
    if gpu is not None and gpu.memory_total_bytes is not None:
        vram_available = _current_bytes(
            gpu.memory_total_bytes,
            gpu.memory_free_bytes,
            (
                100.0 * gpu.memory_used_bytes / gpu.memory_total_bytes
                if gpu.memory_used_bytes is not None and gpu.memory_total_bytes
                else None
            ),
        )
        vram_budget = _byte_budget(
            vram_available,
            reserve_ratio=selected_reserve.vram_reserve_ratio,
            reserve_floor=selected_reserve.vram_reserve_bytes,
            safety_factor=selected_reserve.vram_safety_factor,
        )
        gpu_compute_available = 1.0 - (gpu.utilization_percent or 0.0) / 100.0
        gpu_compute_budget = max(
            0.0,
            (gpu_compute_available - selected_reserve.gpu_reserve_ratio)
            * selected_reserve.gpu_safety_factor,
        )
        gpu_slots = min(
            _gpu_slot_cap(recommend_hardware_tier(snapshot)),
            _slots_from_vram(vram_budget),
            _slots_from_compute(gpu_compute_budget),
        )
        if gpu.memory_free_bytes is not None and gpu_slots < _gpu_slot_cap(
            recommend_hardware_tier(snapshot)
        ):
            notes.append("Current GPU/VRAM headroom reduced local GPU stage concurrency.")

    remote_concurrency = _remote_concurrency(
        cpu_workers=cpu_workers,
        memory_budget=memory_budget,
        preference=preference,
    )
    return ResourceRecommendation(
        experience_mode=experience_mode,
        preference=preference,
        reserve=selected_reserve,
        budget=ResourceBudget(
            cpu_available_equivalent=current_cpu,
            cpu_budget_equivalent=cpu_budget,
            cpu_workers=cpu_workers,
            memory_available_bytes=memory_available,
            memory_budget_bytes=memory_budget,
            disk_available_bytes=disk_available,
            disk_budget_bytes=disk_budget,
            gpu_name=gpu.name if gpu is not None else None,
            gpu_compute_available_ratio=gpu_compute_available,
            gpu_compute_budget_ratio=gpu_compute_budget,
            vram_available_bytes=vram_available,
            vram_budget_bytes=vram_budget,
            gpu_stage_slots=gpu_slots,
            remote_model_concurrency=remote_concurrency,
        ),
        notes=tuple(notes),
    )
