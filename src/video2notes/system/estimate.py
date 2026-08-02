"""Conservative local processing estimates for preflight UI.

These ranges are engineering budgets, not accuracy or speed guarantees. A run
manifest records measured stage wall times so later releases can replace the
budget with per-machine calibration.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from video2notes.domain import ProcessingScope

from .hardware import HardwareSnapshot, HardwareTier, recommend_hardware_tier
from .profiles import QualityMode


class EstimateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProcessingEstimate(EstimateModel):
    hardware_tier: HardwareTier
    quality_mode: QualityMode
    processing_scope: ProcessingScope = ProcessingScope.AUDIO_VISUAL
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
    # These conservative ranges are anchored by the complete BV12hsEz3ELL
    # CPU-OCR benchmark (1.17x / 3.68x / 6.66x on 9950X3D + RTX 5090).
    # Lower tiers are deliberately broad extrapolations until each tier has
    # enough machine-local completed runs for a learned calibration.
    (HardwareTier.CPU_IGPU, QualityMode.FAST): (3.00, 8.00),
    (HardwareTier.CPU_IGPU, QualityMode.BALANCED): (7.00, 18.00),
    (HardwareTier.CPU_IGPU, QualityMode.ACCURATE): (12.00, 30.00),
    (HardwareTier.GPU_8GB, QualityMode.FAST): (1.60, 3.40),
    (HardwareTier.GPU_8GB, QualityMode.BALANCED): (4.20, 9.00),
    (HardwareTier.GPU_8GB, QualityMode.ACCURATE): (7.50, 15.00),
    (HardwareTier.GPU_12GB, QualityMode.FAST): (1.20, 2.60),
    (HardwareTier.GPU_12GB, QualityMode.BALANCED): (3.40, 7.00),
    (HardwareTier.GPU_12GB, QualityMode.ACCURATE): (6.00, 12.00),
    (HardwareTier.GPU_24GB_PLUS, QualityMode.FAST): (0.85, 1.80),
    (HardwareTier.GPU_24GB_PLUS, QualityMode.BALANCED): (2.60, 5.20),
    (HardwareTier.GPU_24GB_PLUS, QualityMode.ACCURATE): (4.80, 9.00),
}

# Audio-only budgets deliberately do not reuse the audio-visual ranges. They
# cover source/media setup, audio extraction, captions, ASR, fusion and note
# rendering, while excluding visual decoding, change detection and OCR. The
# intervals stay broad because the selected ASR model and selective secondary
# windows dominate cost more than source resolution or frame rate.
_AUDIO_ONLY_RTF_BUDGETS: dict[
    tuple[HardwareTier, QualityMode],
    tuple[float, float],
] = {
    (HardwareTier.CPU_IGPU, QualityMode.FAST): (0.10, 0.50),
    (HardwareTier.CPU_IGPU, QualityMode.BALANCED): (0.25, 1.10),
    (HardwareTier.CPU_IGPU, QualityMode.ACCURATE): (0.50, 2.00),
    (HardwareTier.GPU_8GB, QualityMode.FAST): (0.05, 0.18),
    (HardwareTier.GPU_8GB, QualityMode.BALANCED): (0.08, 0.45),
    (HardwareTier.GPU_8GB, QualityMode.ACCURATE): (0.15, 0.75),
    (HardwareTier.GPU_12GB, QualityMode.FAST): (0.04, 0.12),
    (HardwareTier.GPU_12GB, QualityMode.BALANCED): (0.07, 0.25),
    (HardwareTier.GPU_12GB, QualityMode.ACCURATE): (0.12, 0.50),
    (HardwareTier.GPU_24GB_PLUS, QualityMode.FAST): (0.03, 0.08),
    (HardwareTier.GPU_24GB_PLUS, QualityMode.BALANCED): (0.05, 0.16),
    (HardwareTier.GPU_24GB_PLUS, QualityMode.ACCURATE): (0.09, 0.35),
}

_PRECISION_INTENT = {
    QualityMode.FAST: ("快速证据初稿：较低视觉预算与单路识别，适合先判断内容价值。"),
    QualityMode.BALANCED: ("默认高质量：自适应视觉、完整 OCR/ASR 与一次证据约束的笔记整理。"),
    QualityMode.ACCURATE: ("高精度：更密集的疑难区域分析、选择性复核与更高验证预算。"),
}

_AUDIO_ONLY_PRECISION_INTENT = {
    QualityMode.FAST: ("快速音频初稿：优先较小 ASR 与 beam 1；适合清晰语音的快速转写。"),
    QualityMode.BALANCED: ("均衡音频识别：保留配置的较优 ASR、beam 5 与冲突片段选择性复核。"),
    QualityMode.ACCURATE: ("高精度音频意图：保留配置的最高 ASR、beam 5，并复核不确定或冲突片段。"),
}


def estimate_processing_time(
    media_duration_seconds: float,
    snapshot: HardwareSnapshot,
    quality_mode: QualityMode,
    *,
    source_height: int | None = None,
    source_fps: float | None = None,
    hardware_tier: HardwareTier | None = None,
    processing_scope: ProcessingScope = ProcessingScope.AUDIO_VISUAL,
) -> ProcessingEstimate:
    """Estimate a broad interval without inventing pseudo-precise completion time."""

    if media_duration_seconds < 0:
        raise ValueError("media duration cannot be negative")
    tier = hardware_tier or recommend_hardware_tier(snapshot)
    if processing_scope is ProcessingScope.AUDIO_ONLY:
        lower_rtf, upper_rtf = _AUDIO_ONLY_RTF_BUDGETS[(tier, quality_mode)]
        notes = [
            "区间包含来源与媒体准备、音轨提取、平台字幕、ASR、证据融合与笔记渲染。",
            "画面扫描、变化侦测、OCR 与截图被明确排除。",
            (
                "这是 ASR-only 工程估算，不是速度或准确率保证；实际耗时取决于 "
                "ASR 模型、语言、音质、平台字幕与选择性二次识别窗口。"
            ),
            (
                "分辨率与帧率不会触发视觉解码/OCR 乘数；但来源下载体积、容器读取和"
                "远程 API 排队仍可能放大总耗时。"
            ),
        ]
        precision_intent = _AUDIO_ONLY_PRECISION_INTENT[quality_mode]
        basis = "engineering_budget_audio_only_v1"
    else:
        lower_rtf, upper_rtf = _RTF_BUDGETS[(tier, quality_mode)]
        notes = [
            "区间包含解码、视觉扫描、OCR、ASR、证据融合与笔记渲染。",
            "实际速度取决于模型、视频变化密度、噪声、网络下载和 API 排队。",
            (
                "基础区间由 BV12hsEz3ELL 的完整 CPU-OCR 实测校准；低档硬件为保守外推，"
                "且基准 worker 未计入 PDF 导出。"
            ),
        ]
        precision_intent = _PRECISION_INTENT[quality_mode]
        basis = "bv12hsEz3ELL_cpu_ocr_calibrated_v1"
    multiplier = 1.0
    if (
        processing_scope is ProcessingScope.AUDIO_VISUAL
        and source_height is not None
        and source_height >= 2160
    ):
        multiplier *= 1.8
        notes.append("4K 输入按更高解码与视觉成本扩大了区间。")
    elif (
        processing_scope is ProcessingScope.AUDIO_VISUAL
        and source_height is not None
        and source_height >= 1440
    ):
        multiplier *= 1.3
        notes.append("1440p 输入按更高视觉成本扩大了区间。")
    if (
        processing_scope is ProcessingScope.AUDIO_VISUAL
        and source_fps is not None
        and source_fps > 30
    ):
        multiplier *= 1.2
        notes.append("高帧率输入按更高变化侦测成本扩大了区间。")

    lower_rtf *= multiplier
    upper_rtf *= multiplier
    startup_overhead = 5.0 if media_duration_seconds == 0 else 15.0
    return ProcessingEstimate(
        hardware_tier=tier,
        quality_mode=quality_mode,
        processing_scope=processing_scope,
        media_duration_seconds=media_duration_seconds,
        lower_seconds=startup_overhead + media_duration_seconds * lower_rtf,
        upper_seconds=startup_overhead + media_duration_seconds * upper_rtf,
        lower_realtime_factor=lower_rtf,
        upper_realtime_factor=upper_rtf,
        basis=basis,
        precision_intent=precision_intent,
        notes=tuple(notes),
    )
