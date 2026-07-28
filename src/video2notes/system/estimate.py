"""Conservative local processing estimates for preflight UI.

These ranges are engineering budgets, not accuracy or speed guarantees. A run
manifest records measured stage wall times so later releases can replace the
budget with per-machine calibration.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .hardware import HardwareSnapshot, HardwareTier, recommend_hardware_tier
from .profiles import QualityMode


class EstimateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProcessingEstimate(EstimateModel):
    hardware_tier: HardwareTier
    quality_mode: QualityMode
    media_duration_seconds: float = Field(ge=0)
    lower_seconds: float = Field(ge=0)
    upper_seconds: float = Field(ge=0)
    lower_realtime_factor: float = Field(ge=0)
    upper_realtime_factor: float = Field(ge=0)
    basis: str = "engineering_budget_v1"
    precision_intent: str
    notes: tuple[str, ...] = ()


_RTF_BUDGETS: dict[
    tuple[HardwareTier, QualityMode],
    tuple[float, float],
] = {
    (HardwareTier.CPU_IGPU, QualityMode.FAST): (0.17, 0.83),
    (HardwareTier.CPU_IGPU, QualityMode.BALANCED): (0.50, 2.00),
    (HardwareTier.CPU_IGPU, QualityMode.ACCURATE): (1.00, 3.00),
    (HardwareTier.GPU_8GB, QualityMode.FAST): (0.07, 0.23),
    (HardwareTier.GPU_8GB, QualityMode.BALANCED): (0.17, 0.58),
    (HardwareTier.GPU_8GB, QualityMode.ACCURATE): (0.33, 1.00),
    (HardwareTier.GPU_12GB, QualityMode.FAST): (0.05, 0.15),
    (HardwareTier.GPU_12GB, QualityMode.BALANCED): (0.10, 0.33),
    (HardwareTier.GPU_12GB, QualityMode.ACCURATE): (0.20, 0.67),
    (HardwareTier.GPU_24GB_PLUS, QualityMode.FAST): (0.03, 0.10),
    (HardwareTier.GPU_24GB_PLUS, QualityMode.BALANCED): (0.05, 0.20),
    (HardwareTier.GPU_24GB_PLUS, QualityMode.ACCURATE): (0.10, 0.42),
}

_PRECISION_INTENT = {
    QualityMode.FAST: ("快速证据初稿：较低视觉预算与单路识别，适合先判断内容价值。"),
    QualityMode.BALANCED: ("默认高质量：自适应视觉、完整 OCR/ASR 与一次证据约束的笔记整理。"),
    QualityMode.ACCURATE: ("高精度：更密集的疑难区域分析、选择性复核与更高验证预算。"),
}


def estimate_processing_time(
    media_duration_seconds: float,
    snapshot: HardwareSnapshot,
    quality_mode: QualityMode,
    *,
    source_height: int | None = None,
    source_fps: float | None = None,
    hardware_tier: HardwareTier | None = None,
) -> ProcessingEstimate:
    """Estimate a broad interval without inventing pseudo-precise completion time."""

    if media_duration_seconds < 0:
        raise ValueError("media duration cannot be negative")
    tier = hardware_tier or recommend_hardware_tier(snapshot)
    lower_rtf, upper_rtf = _RTF_BUDGETS[(tier, quality_mode)]
    multiplier = 1.0
    notes = [
        "区间包含解码、视觉扫描、OCR、ASR、证据融合与笔记渲染。",
        "实际速度取决于模型、视频变化密度、噪声、网络下载和 API 排队。",
    ]
    if source_height is not None and source_height >= 2160:
        multiplier *= 1.8
        notes.append("4K 输入按更高解码与视觉成本扩大了区间。")
    elif source_height is not None and source_height >= 1440:
        multiplier *= 1.3
        notes.append("1440p 输入按更高视觉成本扩大了区间。")
    if source_fps is not None and source_fps > 30:
        multiplier *= 1.2
        notes.append("高帧率输入按更高变化侦测成本扩大了区间。")

    lower_rtf *= multiplier
    upper_rtf *= multiplier
    startup_overhead = 5.0 if media_duration_seconds == 0 else 15.0
    return ProcessingEstimate(
        hardware_tier=tier,
        quality_mode=quality_mode,
        media_duration_seconds=media_duration_seconds,
        lower_seconds=startup_overhead + media_duration_seconds * lower_rtf,
        upper_seconds=startup_overhead + media_duration_seconds * upper_rtf,
        lower_realtime_factor=lower_rtf,
        upper_realtime_factor=upper_rtf,
        precision_intent=_PRECISION_INTENT[quality_mode],
        notes=tuple(notes),
    )
