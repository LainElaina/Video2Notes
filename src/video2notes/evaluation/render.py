"""Deterministic JSON and Markdown renderers for evaluation results."""

from __future__ import annotations

from collections.abc import Callable

from .models import ProfileComparison, RunComparison, RunDiagnostics


def render_json(result: RunDiagnostics | RunComparison) -> str:
    """Serialize diagnostics with stable field order and readable indentation."""

    return result.model_dump_json(indent=2)


def render_diagnostics_markdown(result: RunDiagnostics) -> str:
    """Render a completed run's intrinsic diagnostics as a readable report."""

    references = result.evidence_references
    note = result.note
    outputs = result.outputs
    lines = [
        f"# 运行诊断：{_cell(result.run_id)}",
        "",
        "## 概览",
        "",
        "| 指标 | 值 |",
        "| --- | ---: |",
        f"| 档位 | {_cell(result.profile)} |",
        f"| 来源 | {_cell(result.source.comparison_key)} |",
        f"| 媒体时长 | {_seconds(result.media_duration_seconds)} |",
        f"| 记录的阶段总耗时 | {_seconds(result.recorded_stage_wall_time_seconds)} |",
        f"| 运行墙钟跨度 | {_seconds(result.run_elapsed_wall_time_seconds)} |",
        f"| RTF | {_number(result.realtime_factor)} |",
        f"| 证据数 | {result.evidence_count} |",
        f"| 证据时间覆盖 | {_ratio(result.evidence_temporal_coverage_ratio)} |",
        f"| 有置信度的证据数 | {result.evidence_spans_with_confidence} |",
        f"| 平均置信度 | {_number(result.average_confidence)} |",
        f"| 冲突 / 未解决 | {result.conflict_count} / {result.unresolved_conflict_count} |",
        f"| 视觉状态 / 截图 | {result.visual_state_count} / {note.screenshot_count} |",
        f"| 警告（总计 / 去重） | {result.warnings.total_count} / {result.warnings.unique_count} |",
        "",
        "## 分阶段耗时",
        "",
        "| 阶段 | 状态 | 尝试 | 耗时 | 占已记录阶段耗时 | 警告 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for stage in result.stages:
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(stage.stage_name),
                    _cell(stage.status),
                    str(stage.attempt),
                    _seconds(stage.wall_time_seconds),
                    _ratio(stage.share_of_recorded_stage_time),
                    str(stage.warning_count),
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 证据分类与覆盖",
            "",
            "| 分类 | 数量 | 有置信度 | 平均置信度 | 覆盖时长 | 时间覆盖 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in result.evidence_by_modality:
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(item.modality.value),
                    str(item.span_count),
                    str(item.spans_with_confidence),
                    _number(item.average_confidence),
                    _seconds(item.temporal_covered_us / 1_000_000),
                    _ratio(item.temporal_coverage_ratio),
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 笔记与证据引用",
            "",
            "| 指标 | 值 |",
            "| --- | ---: |",
            (
                f"| 章节 / 要点 / 事实 | {note.section_count} / "
                f"{note.key_takeaway_count} / {note.fact_count} |"
            ),
            f"| 非空白字符数 | {note.non_whitespace_character_count} |",
            (
                f"| 截图文件存在 / 声明 | {note.existing_screenshot_file_count} / "
                f"{note.screenshot_count} |"
            ),
            (
                "| 唯一引用证据 / 时间线证据 | "
                f"{references.cited_timeline_evidence_count} / "
                f"{references.timeline_evidence_count} |"
            ),
            f"| 引用覆盖 | {_ratio(references.citation_coverage_ratio)} |",
            f"| 所有引用有效 | {_boolean(references.all_references_valid)} |",
            f"| 内嵌证据与时间线一致 | {_boolean(references.embedded_evidence_matches_timeline)} |",
            f"| 引用完整 | {_boolean(references.is_complete)} |",
            "",
            "## 输出一致性",
            "",
            "| 指标 | 值 |",
            "| --- | ---: |",
            (
                f"| 输出文件存在 / 声明 | {outputs.existing_artifact_count} / "
                f"{outputs.declared_artifact_count} |"
            ),
            f"| 证据计数一致 | {_boolean(outputs.evidence_count_matches_timeline)} |",
            f"| 视觉状态计数一致 | {_boolean(outputs.visual_state_count_matches_timeline)} |",
            f"| 使用确定性笔记回退 | {_boolean(outputs.used_deterministic_note_fallback)} |",
        ]
    )
    _append_items(lines, "缺失截图", note.missing_screenshot_paths)
    _append_items(lines, "缺失输出", outputs.missing_artifact_paths)
    _append_items(lines, "未知证据引用", references.unknown_citation_ids)
    _append_items(
        lines,
        "笔记内嵌但时间线不存在的证据",
        references.note_evidence_missing_from_timeline_ids,
    )
    _append_items(
        lines,
        "时间线存在但笔记未内嵌的证据",
        references.timeline_evidence_missing_from_note_ids,
    )
    _append_items(lines, "警告", result.warnings.unique_messages)
    return "\n".join(lines).rstrip() + "\n"


def render_comparison_markdown(result: RunComparison) -> str:
    """Render the three quality profiles side-by-side."""

    profiles = result.profiles
    headers = " | ".join(_cell(item.profile) for item in profiles)
    lines = [
        "# Fast / Balanced / Accurate 三档比较",
        "",
        f"来源：`{_cell(result.source.comparison_key)}`",
        "",
        f"| 指标 | {headers} |",
        "| --- | ---: | ---: | ---: |",
    ]
    metrics: tuple[tuple[str, Callable[[ProfileComparison], str]], ...] = (
        ("运行 ID", lambda item: _cell(item.run_id)),
        ("媒体时长", lambda item: _seconds(item.media_duration_seconds)),
        ("记录的阶段总耗时", lambda item: _seconds(item.recorded_stage_wall_time_seconds)),
        ("RTF", lambda item: _number(item.realtime_factor)),
        ("证据数", lambda item: str(item.evidence_count)),
        ("证据时间覆盖", lambda item: _ratio(item.evidence_temporal_coverage_ratio)),
        ("有置信度的证据数", lambda item: str(item.evidence_spans_with_confidence)),
        ("平均置信度", lambda item: _number(item.average_confidence)),
        ("冲突数", lambda item: str(item.conflict_count)),
        ("未解决冲突数", lambda item: str(item.unresolved_conflict_count)),
        ("视觉状态数", lambda item: str(item.visual_state_count)),
        ("截图数", lambda item: str(item.screenshot_count)),
        ("章节数", lambda item: str(item.section_count)),
        ("要点数", lambda item: str(item.key_takeaway_count)),
        ("非空白字符数", lambda item: str(item.non_whitespace_character_count)),
        ("警告数", lambda item: str(item.warning_count)),
        ("证据引用完整", lambda item: _boolean(item.evidence_references_complete)),
        ("确定性回退", lambda item: _boolean(item.used_deterministic_note_fallback)),
    )
    for label, formatter in metrics:
        values = " | ".join(formatter(item) for item in profiles)
        lines.append(f"| {label} | {values} |")
    return "\n".join(lines) + "\n"


def _append_items(lines: list[str], heading: str, items: tuple[str, ...]) -> None:
    if not items:
        return
    lines.extend(("", f"### {heading}", ""))
    lines.extend(f"- {_cell(item)}" for item in items)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f} s"


def _ratio(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def _boolean(value: bool) -> str:
    return "是" if value else "否"
