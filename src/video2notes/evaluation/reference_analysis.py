"""Detailed, ground-truth-aware analysis for the three reference benchmark tiers.

The metrics in this module deliberately separate measurable processing cost and
cross-tier consistency from recognition accuracy.  Without a human transcript
and frame-level OCR/event annotations, evidence volume and agreement between two
profiles are useful diagnostics, but they are not WER, CER, precision, or recall.
"""

from __future__ import annotations

import json
import os
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from .diagnostics import RunArtifactError, RunProfileSetError
from .models import EvaluationModel

_PROFILES = ("fast", "balanced", "accurate")
_OCR_TIME_TOLERANCE_US = 1_000_000
_OCR_TEXT_SIMILARITY_THRESHOLD = 0.8
_TIME_BUCKET_US = 5_000_000

JsonObject = dict[str, Any]


class ReferenceAnalysisError(ValueError):
    """A detailed reference analysis could not be produced."""


class StageCost(EvaluationModel):
    stage_name: str
    wall_time_seconds: float | None = Field(default=None, ge=0)


class EngineConfiguration(EvaluationModel):
    asr_device: str
    asr_compute_type: str
    asr_beam_size: int = Field(ge=1)
    asr_model_class: str
    ocr_device: str
    ocr_model_class: str


class ScanConfiguration(EvaluationModel):
    analysis_width: int = Field(ge=1)
    analysis_height: int | None = Field(default=None, ge=1)
    coarse_scan_fps: float = Field(ge=0)
    fine_scan_fps: float = Field(ge=0)
    max_fixed_samples: int = Field(ge=0)
    ocr_inference_max_width: int = Field(ge=1)
    verification_passes: int = Field(ge=0)
    screenshot_budget_per_section: int = Field(ge=0)


class ResourceSnapshot(EvaluationModel):
    sample_count: int = Field(ge=0)
    average_process_tree_cpu_percent: float | None = Field(default=None, ge=0)
    peak_process_tree_cpu_percent: float | None = Field(default=None, ge=0)
    average_process_tree_rss_bytes: int | None = Field(default=None, ge=0)
    peak_process_tree_rss_bytes: int | None = Field(default=None, ge=0)
    average_system_cpu_percent: float | None = Field(default=None, ge=0)
    peak_system_cpu_percent: float | None = Field(default=None, ge=0)
    average_nvidia_gpu_percent: float | None = Field(default=None, ge=0)
    peak_nvidia_gpu_percent: float | None = Field(default=None, ge=0)
    average_nvidia_vram_used_bytes: int | None = Field(default=None, ge=0)
    peak_nvidia_vram_used_bytes: int | None = Field(default=None, ge=0)


class ResourceCost(EvaluationModel):
    measurement_scope: Literal["nvidia_device_wide_not_process_attributed"] = (
        "nvidia_device_wide_not_process_attributed"
    )
    guarded_wall_time_seconds: float = Field(ge=0)
    baseline: ResourceSnapshot
    run: ResourceSnapshot


class TierMeasurements(EvaluationModel):
    profile: Literal["fast", "balanced", "accurate"]
    run_directory: str
    engine: EngineConfiguration
    scan: ScanConfiguration
    recorded_stage_wall_time_seconds: float = Field(ge=0)
    stages: tuple[StageCost, ...]
    asr_segment_count: int = Field(ge=0)
    asr_non_whitespace_character_count: int = Field(ge=0)
    ocr_processed_frame_count: int = Field(ge=0)
    ocr_raw_accepted_line_count: int = Field(ge=0)
    ocr_merged_evidence_count: int = Field(ge=0)
    ocr_unique_normalized_text_count: int = Field(ge=0)
    ocr_merged_non_whitespace_character_count: int = Field(ge=0)
    visual_event_count: int = Field(ge=0)
    visual_state_count: int = Field(ge=0)
    screenshot_count: int = Field(ge=0)
    fact_count: int = Field(ge=0)
    section_count: int = Field(ge=0)
    resource: ResourceCost


class DirectionalConsistency(EvaluationModel):
    source_profile: Literal["fast", "balanced"]
    target_profile: Literal["balanced", "accurate"]
    evaluation_kind: Literal["no_ground_truth_cross_tier_consistency"] = (
        "no_ground_truth_cross_tier_consistency"
    )
    accuracy_claim_supported: Literal[False] = False
    asr_full_text_similarity: float | None = Field(default=None, ge=0, le=1)
    visual_event_retained_within_500ms_count: int = Field(ge=0)
    visual_event_retained_within_500ms_ratio: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    visual_event_retained_within_1s_count: int = Field(ge=0)
    visual_event_retained_within_1s_ratio: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    ocr_time_tolerance_seconds: float = Field(ge=0)
    ocr_text_similarity_threshold: float = Field(ge=0, le=1)
    ocr_time_and_text_consistent_count: int = Field(ge=0)
    ocr_time_and_text_consistency_ratio: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    observed_changes: tuple[str, ...]


class ReferenceDetailedAnalysis(EvaluationModel):
    schema_version: int = 1
    evaluation_kind: Literal["intrinsic_cost_and_cross_tier_consistency_without_ground_truth"] = (
        "intrinsic_cost_and_cross_tier_consistency_without_ground_truth"
    )
    accuracy_claim_supported: Literal[False] = False
    benchmark_manifest_path: str
    disclaimers: tuple[str, ...]
    profiles: tuple[TierMeasurements, ...]
    transitions: tuple[DirectionalConsistency, ...]

    @model_validator(mode="after")
    def validate_profile_order(self) -> ReferenceDetailedAnalysis:
        actual = tuple(item.profile for item in self.profiles)
        if actual != _PROFILES:
            raise ValueError(f"profiles must be ordered as {_PROFILES!r}")
        transitions = tuple((item.source_profile, item.target_profile) for item in self.transitions)
        expected = (("fast", "balanced"), ("balanced", "accurate"))
        if transitions != expected:
            raise ValueError(f"transitions must be ordered as {expected!r}")
        return self


class _TierArtifacts:
    def __init__(
        self,
        *,
        measurement: TierMeasurements,
        asr_text: str,
        visual_event_times_us: tuple[int, ...],
        ocr_evidence: tuple[tuple[int, int, str], ...],
    ) -> None:
        self.measurement = measurement
        self.asr_text = asr_text
        self.visual_event_times_us = visual_event_times_us
        self.ocr_evidence = ocr_evidence


def analyze_reference_runs(
    session_root: str | Path,
    run_directories: Sequence[str | Path],
) -> ReferenceDetailedAnalysis:
    """Read three completed run directories and calculate detailed diagnostics."""

    session = Path(session_root).expanduser().resolve()
    manifest_path = session / "benchmark-manifest.json"
    _read_object(manifest_path)
    by_profile: dict[str, _TierArtifacts] = {}
    for run_directory in run_directories:
        artifacts = _analyze_tier(session, Path(run_directory).expanduser().resolve())
        profile = artifacts.measurement.profile
        if profile in by_profile:
            raise RunProfileSetError(f"duplicate detailed-analysis profile: {profile}")
        by_profile[profile] = artifacts

    actual_profiles = set(by_profile)
    expected_profiles = set(_PROFILES)
    if actual_profiles != expected_profiles:
        missing = ",".join(sorted(expected_profiles - actual_profiles))
        unexpected = ",".join(sorted(actual_profiles - expected_profiles))
        raise RunProfileSetError(
            "detailed analysis requires fast, balanced, and accurate runs "
            f"(missing={missing or '-'}; unexpected={unexpected or '-'})"
        )

    ordered = tuple(by_profile[profile] for profile in _PROFILES)
    transitions = (
        _compare_tiers(ordered[0], ordered[1]),
        _compare_tiers(ordered[1], ordered[2]),
    )
    return ReferenceDetailedAnalysis(
        benchmark_manifest_path=str(manifest_path),
        disclaimers=(
            (
                "本报告没有人工校对的逐字稿、逐帧文字标注或关键画面金标准，"
                "因此不能计算或声称 WER、CER、precision、recall 或准确率提升。"
            ),
            (
                "证据数、OCR 文本数、视觉事件数和截图数表示处理覆盖与输出密度；"
                "数量更多不等于内容更正确。"
            ),
            "相邻档的一致性是方向性的无金标准一致性：只说明较高档是否在相近时间找到相似结果，不表示该结果真实。",
            (
                "NVIDIA GPU 利用率和显存来自整张显卡的设备级遥测，包含桌面和其他进程，"
                "不能归因于本次 Video2Notes 进程。"
            ),
        ),
        profiles=tuple(item.measurement for item in ordered),
        transitions=transitions,
    )


def write_reference_analysis(
    session_root: str | Path,
    run_directories: Sequence[str | Path],
) -> tuple[Path, Path]:
    """Write ``detailed-comparison.json`` and its Chinese Markdown companion."""

    root = Path(session_root).expanduser().resolve()
    analysis = analyze_reference_runs(root, run_directories)
    json_path = root / "detailed-comparison.json"
    markdown_path = root / "detailed-comparison.md"
    _atomic_write_text(json_path, analysis.model_dump_json(indent=2))
    _atomic_write_text(markdown_path, render_reference_analysis_markdown(analysis))
    return json_path, markdown_path


def render_reference_analysis_markdown(result: ReferenceDetailedAnalysis) -> str:
    """Render a human-readable Chinese explanation of cost and observed changes."""

    profiles = result.profiles
    lines = [
        "# Fast / Balanced / Accurate 细化质量与成本对比",
        "",
        "> 结论边界：本报告没有人工金标准，不能把证据数量或档位间一致性解释成准确率。",
        "",
        "## 如何阅读",
        "",
    ]
    lines.extend(f"- {item}" for item in result.disclaimers)
    lines.extend(
        (
            "",
            "## 实际运行配置",
            "",
            "| 指标 | Fast | Balanced | Accurate |",
            "| --- | ---: | ---: | ---: |",
            _profile_row("ASR 设备", profiles, lambda item: item.engine.asr_device),
            _profile_row("ASR 模型等级", profiles, lambda item: item.engine.asr_model_class),
            _profile_row("ASR 计算类型", profiles, lambda item: item.engine.asr_compute_type),
            _profile_row("ASR beam", profiles, lambda item: str(item.engine.asr_beam_size)),
            _profile_row("OCR 设备", profiles, lambda item: item.engine.ocr_device),
            _profile_row("OCR 模型等级", profiles, lambda item: item.engine.ocr_model_class),
            _profile_row("粗扫 FPS", profiles, lambda item: _number(item.scan.coarse_scan_fps)),
            _profile_row("细扫 FPS", profiles, lambda item: _number(item.scan.fine_scan_fps)),
            _profile_row("画面分析宽度", profiles, lambda item: str(item.scan.analysis_width)),
            _profile_row(
                "OCR 推理宽度上限",
                profiles,
                lambda item: str(item.scan.ocr_inference_max_width),
            ),
            _profile_row(
                "核验轮次",
                profiles,
                lambda item: str(item.scan.verification_passes),
            ),
            _profile_row(
                "每章节截图预算",
                profiles,
                lambda item: str(item.scan.screenshot_budget_per_section),
            ),
            "",
            "## 处理成本与输出密度",
            "",
            "| 指标 | Fast | Balanced | Accurate |",
            "| --- | ---: | ---: | ---: |",
            _profile_row(
                "受保护进程总耗时",
                profiles,
                lambda item: _seconds(item.resource.guarded_wall_time_seconds),
            ),
            _profile_row(
                "阶段记录总耗时",
                profiles,
                lambda item: _seconds(item.recorded_stage_wall_time_seconds),
            ),
            _profile_row("ASR 片段", profiles, lambda item: str(item.asr_segment_count)),
            _profile_row(
                "ASR 非空白字符",
                profiles,
                lambda item: str(item.asr_non_whitespace_character_count),
            ),
            _profile_row(
                "OCR 已处理帧",
                profiles,
                lambda item: str(item.ocr_processed_frame_count),
            ),
            _profile_row(
                "OCR 原始接受行",
                profiles,
                lambda item: str(item.ocr_raw_accepted_line_count),
            ),
            _profile_row(
                "OCR 合并证据",
                profiles,
                lambda item: str(item.ocr_merged_evidence_count),
            ),
            _profile_row(
                "OCR 唯一规范文本",
                profiles,
                lambda item: str(item.ocr_unique_normalized_text_count),
            ),
            _profile_row(
                "OCR 合并证据字符",
                profiles,
                lambda item: str(item.ocr_merged_non_whitespace_character_count),
            ),
            _profile_row(
                "视觉事件 / 状态",
                profiles,
                lambda item: f"{item.visual_event_count} / {item.visual_state_count}",
            ),
            _profile_row("截图", profiles, lambda item: str(item.screenshot_count)),
            _profile_row("事实", profiles, lambda item: str(item.fact_count)),
            _profile_row("章节", profiles, lambda item: str(item.section_count)),
            "",
            "## 相邻档无金标准一致性",
            "",
            "| 方向 | ASR 全文相似度 | 视觉事件 ±0.5s | 视觉事件 ±1s | OCR 时间+文本一致性 |",
            "| --- | ---: | ---: | ---: | ---: |",
        )
    )
    for transition in result.transitions:
        visual_500ms = _count_ratio(
            transition.visual_event_retained_within_500ms_count,
            transition.visual_event_retained_within_500ms_ratio,
        )
        visual_1s = _count_ratio(
            transition.visual_event_retained_within_1s_count,
            transition.visual_event_retained_within_1s_ratio,
        )
        ocr_consistency = _count_ratio(
            transition.ocr_time_and_text_consistent_count,
            transition.ocr_time_and_text_consistency_ratio,
        )
        lines.append(
            f"| {transition.source_profile} → {transition.target_profile} | "
            f"{_ratio(transition.asr_full_text_similarity)} | "
            f"{visual_500ms} | {visual_1s} | {ocr_consistency} |"
        )
    lines.extend(
        (
            "",
            (
                f"OCR 一致性阈值：时间区间允许前后 "
                f"{_OCR_TIME_TOLERANCE_US / 1_000_000:.1f}s，规范文本相似度至少 "
                f"{_OCR_TEXT_SIMILARITY_THRESHOLD:.0%}。这是较低档结果在较高档中的"
                "方向性保留率，不是 OCR 准确率。"
            ),
            "",
            "## 各档实际增加了什么",
            "",
        )
    )
    for transition in result.transitions:
        label = f"{transition.source_profile.title()} → {transition.target_profile.title()}"
        lines.append(f"### {label}")
        lines.append("")
        lines.extend(f"- {item}" for item in transition.observed_changes)
        lines.append("")

    lines.extend(("## 分阶段耗时", ""))
    stage_names = tuple(
        dict.fromkeys(stage.stage_name for profile in profiles for stage in profile.stages)
    )
    lines.extend(
        (
            "| 阶段 | Fast | Balanced | Accurate |",
            "| --- | ---: | ---: | ---: |",
        )
    )
    by_profile_stage = {
        profile.profile: {stage.stage_name: stage.wall_time_seconds for stage in profile.stages}
        for profile in profiles
    }
    for stage_name in stage_names:
        values = [
            _seconds(by_profile_stage[profile.profile].get(stage_name)) for profile in profiles
        ]
        lines.append(f"| {_cell(stage_name)} | {' | '.join(values)} |")

    lines.extend(
        (
            "",
            "## 进程树 CPU / 内存遥测",
            "",
            "| 指标 | Fast | Balanced | Accurate |",
            "| --- | ---: | ---: | ---: |",
            _profile_row(
                "运行平均 / 峰值 CPU",
                profiles,
                lambda item: _average_peak_percent(
                    item.resource.run.average_process_tree_cpu_percent,
                    item.resource.run.peak_process_tree_cpu_percent,
                ),
            ),
            _profile_row(
                "运行平均 / 峰值 RSS",
                profiles,
                lambda item: _average_peak_bytes(
                    item.resource.run.average_process_tree_rss_bytes,
                    item.resource.run.peak_process_tree_rss_bytes,
                ),
            ),
            "",
            "## 整卡 GPU / 显存遥测",
            "",
            "> 下表是 NVIDIA 整卡读数，不是 Video2Notes 单进程占用；应结合基线与运行值阅读。",
            "",
            "| 指标 | Fast | Balanced | Accurate |",
            "| --- | ---: | ---: | ---: |",
            _profile_row(
                "基线平均 / 峰值 GPU",
                profiles,
                lambda item: _average_peak_percent(
                    item.resource.baseline.average_nvidia_gpu_percent,
                    item.resource.baseline.peak_nvidia_gpu_percent,
                ),
            ),
            _profile_row(
                "运行平均 / 峰值 GPU",
                profiles,
                lambda item: _average_peak_percent(
                    item.resource.run.average_nvidia_gpu_percent,
                    item.resource.run.peak_nvidia_gpu_percent,
                ),
            ),
            _profile_row(
                "基线平均 / 峰值显存",
                profiles,
                lambda item: _average_peak_bytes(
                    item.resource.baseline.average_nvidia_vram_used_bytes,
                    item.resource.baseline.peak_nvidia_vram_used_bytes,
                ),
            ),
            _profile_row(
                "运行平均 / 峰值显存",
                profiles,
                lambda item: _average_peak_bytes(
                    item.resource.run.average_nvidia_vram_used_bytes,
                    item.resource.run.peak_nvidia_vram_used_bytes,
                ),
            ),
            "",
            "## 准确率结论",
            "",
            (
                "当前数据只能回答每档增加了多少扫描、识别、证据、笔记结构及成本，"
                "以及这些结果跨档是否稳定。若要回答哪一档真正识别得更准，需要补充"
                "人工逐字稿、关键画面标注与 OCR 真值后，再计算 WER/CER、事件 "
                "precision/recall 和 OCR precision/recall。"
            ),
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def _analyze_tier(session: Path, root: Path) -> _TierArtifacts:
    if not root.is_dir():
        raise RunArtifactError(f"run directory does not exist: {root}")
    manifest = _read_object(root / "manifest.json")
    profile_value = manifest.get("profile")
    if profile_value not in _PROFILES:
        raise RunProfileSetError(f"unexpected detailed-analysis profile: {profile_value}")
    profile = cast(Literal["fast", "balanced", "accurate"], profile_value)
    if manifest.get("status") != "completed":
        raise RunArtifactError(f"run is not completed: {root}")

    plan_payload = _read_object(root / "system" / "execution-plan.json")
    effective_plan = _object(plan_payload.get("effective_plan"), "effective_plan")
    actual_backends = _object(plan_payload.get("actual_backends"), "actual_backends")
    asr_backend = _optional_object(actual_backends.get("asr_primary"))
    asr_config = _optional_object(asr_backend.get("config"))
    ocr_backend = _optional_object(actual_backends.get("ocr"))
    ocr_config = _optional_object(ocr_backend.get("config"))

    scan_payload = _read_object(root / "vision" / "scan-events.json")
    scanner = _object(scan_payload.get("scanner"), "scanner")
    events = _object_list(scan_payload.get("events"), "events")
    visual_states = _read_object_list(root / "vision" / "visual-states.json")
    asr_evidence = _read_object_list(root / "asr" / "asr-evidence.json")
    ocr_payload = _read_object(root / "ocr" / "ocr-evidence.json")
    ocr_bundle = _object(ocr_payload.get("bundle"), "ocr bundle")
    ocr_results = _object_list(ocr_bundle.get("results"), "ocr results")
    merged_ocr = _object_list(ocr_bundle.get("evidence"), "ocr evidence")
    note = _read_object(root / "notes" / "document.json")
    sections = _object_list(note.get("sections"), "note sections")
    facts = _object_list(note.get("facts"), "note facts")

    stages_object = _object(manifest.get("stages"), "manifest stages")
    stages: list[StageCost] = []
    for name, value in stages_object.items():
        record = _object(value, f"stage {name}")
        stages.append(
            StageCost(
                stage_name=name,
                wall_time_seconds=_optional_number(record.get("wall_time_seconds")),
            )
        )
    recorded_stage_seconds = sum(item.wall_time_seconds or 0.0 for item in stages)

    raw_accepted = [
        line
        for result in ocr_results
        for line in _object_list(result.get("lines", []), "ocr result lines")
        if line.get("decision") == "accepted"
    ]
    unique_ocr_text = {
        _canonical_text(_string(item.get("normalized_text")))
        for item in merged_ocr
        if _canonical_text(_string(item.get("normalized_text")))
    }
    asr_text = "".join(
        _string(item.get("normalized_text") or item.get("raw_text"))
        for item in sorted(asr_evidence, key=lambda item: _integer(item.get("start_us")))
    )
    # The first synthetic ``initial`` event establishes state zero; it is not a
    # detected visual change and would otherwise make every profile look at
    # least partly consistent before any content transition occurred.
    visual_times = tuple(
        _integer(item.get("transition_us", item.get("keyframe_us", 0)))
        for item in events
        if _string(item.get("reason")).casefold() != "initial"
    )
    timed_ocr = tuple(
        (
            _integer(item.get("start_us")),
            _integer(item.get("end_us")),
            _canonical_text(_string(item.get("normalized_text") or item.get("raw_text"))),
        )
        for item in merged_ocr
        if _canonical_text(_string(item.get("normalized_text") or item.get("raw_text")))
    )

    resource_payload = _read_object(session / "reports" / f"{profile}.resource.json")
    baseline = _resource_snapshot(_object(resource_payload.get("baseline"), "resource baseline"))
    run_resource = _resource_snapshot(_object(resource_payload.get("run"), "resource run"))
    guarded_seconds = _number_or_zero(resource_payload.get("duration_seconds"))
    screenshots = sum(
        len(_object_list(section.get("screenshots", []), "section screenshots"))
        for section in sections
    )

    engine = EngineConfiguration(
        asr_device=_string(asr_config.get("device") or effective_plan.get("asr_device")),
        asr_compute_type=_string(
            asr_config.get("compute_type") or effective_plan.get("asr_compute_type")
        ),
        asr_beam_size=max(
            1,
            _integer(asr_config.get("beam_size") or effective_plan.get("asr_beam_size")),
        ),
        asr_model_class=_string(effective_plan.get("asr_model_class"), "unknown"),
        ocr_device=_string(ocr_config.get("device") or effective_plan.get("ocr_device")),
        ocr_model_class=_string(effective_plan.get("ocr_model_class"), "unknown"),
    )
    scan = ScanConfiguration(
        analysis_width=max(
            1,
            _integer(scanner.get("analysis_width") or effective_plan.get("analysis_width")),
        ),
        analysis_height=_positive_optional_integer(scanner.get("analysis_height")),
        coarse_scan_fps=_number_or_zero(
            scanner.get("coarse_fps", effective_plan.get("cheap_scan_fps"))
        ),
        fine_scan_fps=_number_or_zero(
            scanner.get("fine_fps", effective_plan.get("expensive_scan_fps"))
        ),
        max_fixed_samples=max(0, _integer(effective_plan.get("max_fixed_samples"))),
        ocr_inference_max_width=max(
            1,
            _integer(effective_plan.get("ocr_inference_max_width")),
        ),
        verification_passes=max(0, _integer(effective_plan.get("verification_passes"))),
        screenshot_budget_per_section=max(
            0,
            _integer(effective_plan.get("screenshot_budget_per_section")),
        ),
    )
    measurement = TierMeasurements(
        profile=profile,
        run_directory=str(root),
        engine=engine,
        scan=scan,
        recorded_stage_wall_time_seconds=recorded_stage_seconds,
        stages=tuple(stages),
        asr_segment_count=len(asr_evidence),
        asr_non_whitespace_character_count=_non_whitespace_character_count(asr_text),
        ocr_processed_frame_count=sum(item.get("status") == "processed" for item in ocr_results),
        ocr_raw_accepted_line_count=len(raw_accepted),
        ocr_merged_evidence_count=len(merged_ocr),
        ocr_unique_normalized_text_count=len(unique_ocr_text),
        ocr_merged_non_whitespace_character_count=sum(
            _non_whitespace_character_count(
                _string(item.get("normalized_text") or item.get("raw_text"))
            )
            for item in merged_ocr
        ),
        visual_event_count=len(events),
        visual_state_count=len(visual_states),
        screenshot_count=screenshots,
        fact_count=len(facts),
        section_count=len(sections),
        resource=ResourceCost(
            guarded_wall_time_seconds=guarded_seconds,
            baseline=baseline,
            run=run_resource,
        ),
    )
    return _TierArtifacts(
        measurement=measurement,
        asr_text=_canonical_text(asr_text),
        visual_event_times_us=visual_times,
        ocr_evidence=timed_ocr,
    )


def _compare_tiers(source: _TierArtifacts, target: _TierArtifacts) -> DirectionalConsistency:
    source_profile = cast(Literal["fast", "balanced"], source.measurement.profile)
    target_profile = cast(Literal["balanced", "accurate"], target.measurement.profile)
    retained_500ms = _event_retention(
        source.visual_event_times_us,
        target.visual_event_times_us,
        tolerance_us=500_000,
    )
    retained_1s = _event_retention(
        source.visual_event_times_us,
        target.visual_event_times_us,
        tolerance_us=1_000_000,
    )
    ocr_consistent = _ocr_consistency(source.ocr_evidence, target.ocr_evidence)
    asr_similarity = _text_similarity(source.asr_text, target.asr_text)
    source_measurement = source.measurement
    target_measurement = target.measurement
    return DirectionalConsistency(
        source_profile=source_profile,
        target_profile=target_profile,
        asr_full_text_similarity=asr_similarity,
        visual_event_retained_within_500ms_count=retained_500ms,
        visual_event_retained_within_500ms_ratio=_safe_ratio(
            retained_500ms,
            len(source.visual_event_times_us),
        ),
        visual_event_retained_within_1s_count=retained_1s,
        visual_event_retained_within_1s_ratio=_safe_ratio(
            retained_1s,
            len(source.visual_event_times_us),
        ),
        ocr_time_tolerance_seconds=_OCR_TIME_TOLERANCE_US / 1_000_000,
        ocr_text_similarity_threshold=_OCR_TEXT_SIMILARITY_THRESHOLD,
        ocr_time_and_text_consistent_count=ocr_consistent,
        ocr_time_and_text_consistency_ratio=_safe_ratio(
            ocr_consistent,
            len(source.ocr_evidence),
        ),
        observed_changes=_observed_changes(source_measurement, target_measurement),
    )


def _observed_changes(source: TierMeasurements, target: TierMeasurements) -> tuple[str, ...]:
    elapsed_delta = (
        target.resource.guarded_wall_time_seconds - source.resource.guarded_wall_time_seconds
    )
    elapsed_multiple = (
        target.resource.guarded_wall_time_seconds / source.resource.guarded_wall_time_seconds
        if source.resource.guarded_wall_time_seconds > 0
        else None
    )
    return (
        (
            f"受保护进程耗时变化 {elapsed_delta:+.3f}s"
            + (f"（目标档为 {elapsed_multiple:.2f}×）" if elapsed_multiple is not None else "")
            + "；这是实际成本，不是准确率。"
        ),
        (
            f"ASR 从 {source.asr_segment_count} 段 / "
            f"{source.asr_non_whitespace_character_count} 字符变为 "
            f"{target.asr_segment_count} 段 / "
            f"{target.asr_non_whitespace_character_count} 字符。"
        ),
        (
            f"视觉状态从 {source.visual_state_count} 增至 {target.visual_state_count}，"
            f"OCR 原始接受行从 {source.ocr_raw_accepted_line_count} 增至 "
            f"{target.ocr_raw_accepted_line_count}，合并证据从 "
            f"{source.ocr_merged_evidence_count} 增至 {target.ocr_merged_evidence_count}；"
            "这表示扫描和文本覆盖更密。"
        ),
        (
            f"笔记截图从 {source.screenshot_count} 增至 {target.screenshot_count}，"
            f"事实从 {source.fact_count} 变为 {target.fact_count}，"
            f"章节从 {source.section_count} 变为 {target.section_count}。"
        ),
        "是否真正更准仍需人工金标准；新增证据也可能包含重复项或 OCR 噪声。",
    )


def _event_retention(
    source_times: Sequence[int],
    target_times: Sequence[int],
    *,
    tolerance_us: int,
) -> int:
    if not target_times:
        return 0
    ordered_target = sorted(target_times)
    used_target_indexes: set[int] = set()
    retained = 0
    for source_time in sorted(source_times):
        candidates = sorted(
            (
                (abs(target_time - source_time), index)
                for index, target_time in enumerate(ordered_target)
                if index not in used_target_indexes
                and abs(target_time - source_time) <= tolerance_us
            ),
        )
        if not candidates:
            continue
        used_target_indexes.add(candidates[0][1])
        retained += 1
    return retained


def _ocr_consistency(
    source: Sequence[tuple[int, int, str]],
    target: Sequence[tuple[int, int, str]],
) -> int:
    buckets: defaultdict[int, list[tuple[int, int, int, str]]] = defaultdict(list)
    for index, (start_us, end_us, text) in enumerate(target):
        first_bucket = max(0, start_us - _OCR_TIME_TOLERANCE_US) // _TIME_BUCKET_US
        last_bucket = max(0, end_us + _OCR_TIME_TOLERANCE_US) // _TIME_BUCKET_US
        for bucket in range(first_bucket, last_bucket + 1):
            buckets[bucket].append((index, start_us, end_us, text))

    consistent = 0
    for start_us, end_us, text in source:
        first_bucket = max(0, start_us) // _TIME_BUCKET_US
        last_bucket = max(0, end_us) // _TIME_BUCKET_US
        candidates: list[tuple[int, int, str]] = []
        seen: set[int] = set()
        for bucket in range(first_bucket, last_bucket + 1):
            for index, candidate_start, candidate_end, candidate_text in buckets.get(bucket, ()):
                if index not in seen:
                    seen.add(index)
                    candidates.append((candidate_start, candidate_end, candidate_text))
        if any(
            candidate_start <= end_us + _OCR_TIME_TOLERANCE_US
            and candidate_end >= start_us - _OCR_TIME_TOLERANCE_US
            and (_text_similarity(text, candidate_text) or 0.0) >= _OCR_TEXT_SIMILARITY_THRESHOLD
            for candidate_start, candidate_end, candidate_text in candidates
        ):
            consistent += 1
    return consistent


def _text_similarity(left: str, right: str) -> float | None:
    if not left and not right:
        return None
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _canonical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character for character in normalized if unicodedata.category(character)[0] in {"L", "N"}
    )


def _non_whitespace_character_count(value: str) -> int:
    return sum(not character.isspace() for character in value)


def _resource_snapshot(payload: Mapping[str, Any]) -> ResourceSnapshot:
    return ResourceSnapshot(
        sample_count=max(0, _integer(payload.get("sample_count"))),
        average_process_tree_cpu_percent=_optional_number(
            payload.get("average_process_tree_cpu_percent")
        ),
        peak_process_tree_cpu_percent=_optional_number(
            payload.get("peak_process_tree_cpu_percent")
        ),
        average_process_tree_rss_bytes=_optional_nonnegative_integer(
            payload.get("average_process_tree_rss_bytes")
        ),
        peak_process_tree_rss_bytes=_optional_nonnegative_integer(
            payload.get("peak_process_tree_rss_bytes")
        ),
        average_system_cpu_percent=_optional_number(payload.get("average_system_cpu_percent")),
        peak_system_cpu_percent=_optional_number(payload.get("peak_system_cpu_percent")),
        average_nvidia_gpu_percent=_optional_number(payload.get("average_nvidia_gpu_percent")),
        peak_nvidia_gpu_percent=_optional_number(payload.get("peak_nvidia_gpu_percent")),
        average_nvidia_vram_used_bytes=_optional_nonnegative_integer(
            payload.get("average_nvidia_vram_used_bytes")
        ),
        peak_nvidia_vram_used_bytes=_optional_nonnegative_integer(
            payload.get("peak_nvidia_vram_used_bytes")
        ),
    )


def _read_object(path: Path) -> JsonObject:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise RunArtifactError(f"required artifact must contain an object: {path}")
    return cast(JsonObject, payload)


def _read_object_list(path: Path) -> list[JsonObject]:
    return _object_list(_read_json(path), str(path))


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise RunArtifactError(f"required detailed-analysis artifact does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RunArtifactError(f"invalid detailed-analysis artifact: {path}") from error


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise RunArtifactError(f"{label} must be a JSON object")
    return cast(JsonObject, value)


def _optional_object(value: object) -> JsonObject:
    return cast(JsonObject, value) if isinstance(value, dict) else {}


def _object_list(value: object, label: str) -> list[JsonObject]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RunArtifactError(f"{label} must be a JSON array of objects")
    return cast(list[JsonObject], value)


def _string(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _integer(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _positive_optional_integer(value: object) -> int | None:
    parsed = _integer(value)
    return parsed if parsed > 0 else None


def _optional_nonnegative_integer(value: object) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def _optional_number(value: object) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, float(value))


def _number_or_zero(value: object) -> float:
    return _optional_number(value) or 0.0


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _profile_row(
    label: str,
    profiles: Sequence[TierMeasurements],
    formatter: Callable[[TierMeasurements], str],
) -> str:
    values = " | ".join(_cell(str(formatter(item))) for item in profiles)
    return f"| {label} | {values} |"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}s"


def _number(value: float) -> str:
    return f"{value:.3f}"


def _ratio(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def _count_ratio(count: int, ratio: float | None) -> str:
    return f"{count} / {_ratio(ratio)}"


def _average_peak_percent(average: float | None, peak: float | None) -> str:
    return f"{_percent(average)} / {_percent(peak)}"


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _average_peak_bytes(average: int | None, peak: int | None) -> str:
    return f"{_bytes(average)} / {_bytes(peak)}"


def _bytes(value: int | None) -> str:
    return "—" if value is None else f"{value / (1024**3):.2f} GiB"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
    os.replace(temporary, path)


__all__ = [
    "DirectionalConsistency",
    "EngineConfiguration",
    "ReferenceAnalysisError",
    "ReferenceDetailedAnalysis",
    "ResourceCost",
    "ResourceSnapshot",
    "ScanConfiguration",
    "StageCost",
    "TierMeasurements",
    "analyze_reference_runs",
    "render_reference_analysis_markdown",
    "write_reference_analysis",
]
