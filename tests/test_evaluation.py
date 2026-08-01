from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from video2notes.domain import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactRef,
    EvidenceModality,
    EvidenceSpan,
    MediaManifest,
    RunStatus,
    SourceDescriptor,
    StageStatus,
    VisualState,
)
from video2notes.domain.models import StageRecord
from video2notes.evaluation import (
    RunArtifactError,
    RunNotCompleteError,
    RunProfileSetError,
    RunSourceMismatchError,
    compare_runs,
    diagnose_run,
    render_comparison_markdown,
    render_diagnostics_markdown,
    render_json,
)
from video2notes.fusion import EvidenceConflict, FusionResult
from video2notes.fusion.timeline import ConflictKind
from video2notes.notes import (
    FactCard,
    NoteDocument,
    NoteMetadata,
    NoteScreenshot,
    NoteSection,
)

_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _write_run(
    parent: Path,
    *,
    profile: str = "balanced",
    source_id: str = "BV1same",
    run_status: RunStatus = RunStatus.COMPLETED,
    stage_status: StageStatus = StageStatus.COMPLETED,
    duration_us: int = 10_000_000,
    stage_scale: float = 1.0,
    include_confidence: bool = True,
    ghost_reference: bool = False,
    missing_screenshot: bool = False,
    missing_output: bool = False,
    mismatched_outcome_counts: bool = False,
) -> Path:
    run_id = f"run-{profile}-{parent.name}"
    root = parent / run_id
    for directory in ("media", "evidence", "notes", "render", "vision"):
        (root / directory).mkdir(parents=True, exist_ok=True)

    markdown_path = root / "notes" / "note.md"
    html_path = root / "render" / "note.html"
    keyframe_path = root / "vision" / "keyframe.png"
    screenshot_path = root / "vision" / "screenshot.png"
    markdown_path.write_text("# note\n", encoding="utf-8")
    html_path.write_text("<h1>note</h1>\n", encoding="utf-8")
    keyframe_path.write_bytes(b"keyframe")
    if not missing_screenshot:
        screenshot_path.write_bytes(b"screenshot")

    evidence = [
        EvidenceSpan(
            id="ev-asr-1",
            run_id=run_id,
            modality=EvidenceModality.ASR,
            start_us=0,
            end_us=4_000_000,
            raw_text="第一段",
            normalized_text="第一段",
            confidence=0.8 if include_confidence else None,
        ),
        EvidenceSpan(
            id="ev-asr-2",
            run_id=run_id,
            modality=EvidenceModality.ASR,
            start_us=3_000_000,
            end_us=7_000_000,
            raw_text="第二段",
            normalized_text="第二段",
            confidence=0.6 if include_confidence else None,
        ),
        EvidenceSpan(
            id="ev-ocr-1",
            run_id=run_id,
            modality=EvidenceModality.OCR,
            start_us=8_000_000,
            end_us=12_000_000,
            raw_text="屏幕文字",
            normalized_text="屏幕文字",
            confidence=0.4 if include_confidence else None,
        ),
    ]
    keyframe_ref = _artifact_ref(root, keyframe_path, ArtifactKind.VISUAL)
    visual_states = [
        VisualState(
            id="visual-1",
            run_id=run_id,
            start_us=0,
            end_us=5_000_000,
            transition_us=0,
            stable_keyframe_us=1_000_000,
            keyframe_artifact=keyframe_ref,
            change_reason="scene_change",
        ),
        VisualState(
            id="visual-2",
            run_id=run_id,
            start_us=5_000_000,
            end_us=10_000_000,
            transition_us=5_000_000,
            stable_keyframe_us=6_000_000,
            change_reason="text_change",
        ),
    ]
    fusion = FusionResult(
        run_id=run_id,
        windows=[],
        links=[],
        conflicts=[
            EvidenceConflict(
                left_id="ev-asr-1",
                right_id="ev-asr-2",
                kind=ConflictKind.TRANSCRIPT_DISAGREEMENT,
                start_us=3_000_000,
                end_us=4_000_000,
                text_similarity=0.5,
                severity=0.7,
                requires_secondary=True,
            )
        ],
        evidence=evidence,
        visual_states=visual_states,
    )
    _write_model(root / "evidence" / "timeline.json", fusion)

    note_evidence = list(evidence)
    section_evidence_ids = ["ev-asr-1", "ev-ocr-1"]
    if ghost_reference:
        note_evidence.append(
            EvidenceSpan(
                id="ev-ghost",
                run_id=run_id,
                modality=EvidenceModality.VISUAL,
                start_us=1_000_000,
                end_us=2_000_000,
                raw_text="时间线中不存在",
            )
        )
        section_evidence_ids.append("ev-ghost")
    note = NoteDocument(
        metadata=NoteMetadata(
            title="诊断测试",
            run_id=run_id,
            source_kind="bilibili",
            source_locator=f"https://www.bilibili.com/video/{source_id}",
            source_url=f"https://www.bilibili.com/video/{source_id}",
            duration_us=duration_us,
            languages=["zh-CN"],
            quality_mode=profile,
            quality_warnings=["shared-warning"],
            created_at=_NOW,
        ),
        abstract="按证据生成摘要。",
        key_takeaways=["保留时间关系", "检查文字变化"],
        sections=[
            NoteSection(
                id="section-1",
                title="第一章",
                start_us=0,
                end_us=10_000_000,
                summary="章节摘要。",
                body_markdown="正文内容。",
                evidence_ids=section_evidence_ids,
                fact_ids=["fact-1"],
                screenshots=[
                    NoteScreenshot(
                        relative_path="vision/screenshot.png",
                        timestamp_us=9_000_000,
                        caption="关键画面",
                        alt_text="画面中的文字",
                        evidence_ids=["ev-ocr-1"],
                    )
                ],
            )
        ],
        facts=[
            FactCard(
                id="fact-1",
                claim="语音存在两个重叠片段。",
                evidence_ids=["ev-asr-2"],
                confidence=0.7 if include_confidence else None,
            )
        ],
        evidence=note_evidence,
        glossary={"RTF": "处理耗时与媒体时长之比"},
    )
    note_path = root / "notes" / "document.json"
    _write_model(note_path, note)

    media = MediaManifest(
        source_path="media/source.mp4",
        source_sha256="a" * 64,
        container_format="mp4",
        file_size=1234,
        duration_us=duration_us,
        timeline_origin_us=0,
        streams=[],
        probed_at=_NOW,
    )
    _write_model(root / "media" / "media-manifest.json", media)

    stage_times = {
        "acquire.source": 2.0,
        "audio.transcribe": 3.0,
        "notes.compose": 1.0,
        "render.outputs": 4.0,
    }
    stages = {
        name: StageRecord(
            stage_name=name,
            stage_version="1",
            fingerprint=f"fingerprint-{name}",
            status=stage_status,
            attempt=1,
            config_hash=f"config-{name}",
            started_at=_NOW,
            finished_at=_NOW + timedelta(seconds=seconds * stage_scale),
            wall_time_seconds=seconds * stage_scale,
            warnings=["stage-warning"] if name == "audio.transcribe" else [],
        )
        for name, seconds in stage_times.items()
    }
    source = SourceDescriptor(
        kind="url",
        locator=f"https://www.bilibili.com/video/{source_id}",
    )
    manifest = ArtifactManifest(
        run_id=run_id,
        source=source,
        profile=profile,
        status=run_status,
        created_at=_NOW,
        updated_at=_NOW + timedelta(seconds=20),
        stages=stages,
        warnings=["shared-warning"],
    )
    _write_model(root / "manifest.json", manifest)

    outcome = {
        "run_id": run_id,
        "markdown": _artifact_ref(root, markdown_path, ArtifactKind.NOTE).model_dump(mode="json"),
        "html": _artifact_ref(root, html_path, ArtifactKind.RENDER).model_dump(mode="json"),
        "pdf": None,
        "note_document": _artifact_ref(root, note_path, ArtifactKind.NOTE).model_dump(mode="json"),
        "evidence_count": 99 if mismatched_outcome_counts else len(evidence),
        "visual_state_count": 99 if mismatched_outcome_counts else len(visual_states),
        "used_deterministic_note_fallback": profile == "fast",
    }
    (root / "render" / "outcome.json").write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if missing_output:
        html_path.unlink()
    return root


def _artifact_ref(root: Path, path: Path, kind: ArtifactKind) -> ArtifactRef:
    return ArtifactRef(
        kind=kind,
        relative_path=path.relative_to(root).as_posix(),
        sha256="b" * 64,
        size_bytes=path.stat().st_size,
    )


def _write_model(
    path: Path,
    model: ArtifactManifest | MediaManifest | FusionResult | NoteDocument,
) -> None:
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


class RunDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_calculates_timings_coverage_confidence_and_note_counts(self) -> None:
        result = diagnose_run(_write_run(self.root))

        self.assertEqual(result.recorded_stage_wall_time_seconds, 10.0)
        self.assertEqual(result.run_elapsed_wall_time_seconds, 20.0)
        self.assertEqual(result.realtime_factor, 1.0)
        self.assertEqual(result.stages_with_recorded_wall_time, 4)
        self.assertEqual(result.evidence_count, 3)
        self.assertEqual(result.evidence_temporal_covered_us, 9_000_000)
        self.assertEqual(result.evidence_temporal_coverage_ratio, 0.9)
        self.assertEqual(result.evidence_spans_with_confidence, 3)
        self.assertAlmostEqual(result.average_confidence or 0.0, 0.6)

        by_modality = {item.modality: item for item in result.evidence_by_modality}
        self.assertEqual(by_modality[EvidenceModality.ASR].span_count, 2)
        self.assertEqual(by_modality[EvidenceModality.ASR].temporal_covered_us, 7_000_000)
        self.assertEqual(by_modality[EvidenceModality.ASR].temporal_coverage_ratio, 0.7)
        self.assertAlmostEqual(by_modality[EvidenceModality.ASR].average_confidence or 0, 0.7)
        self.assertEqual(by_modality[EvidenceModality.OCR].temporal_covered_us, 2_000_000)
        self.assertEqual(by_modality[EvidenceModality.METADATA].span_count, 0)

        self.assertEqual(result.conflict_count, 1)
        self.assertEqual(result.unresolved_conflict_count, 1)
        self.assertEqual(result.secondary_review_conflict_count, 1)
        self.assertEqual(result.visual_state_count, 2)
        self.assertEqual(result.visual_state_with_keyframe_count, 1)
        self.assertEqual(result.note.section_count, 1)
        self.assertEqual(result.note.key_takeaway_count, 2)
        self.assertEqual(result.note.screenshot_count, 1)
        self.assertEqual(result.note.existing_screenshot_file_count, 1)
        self.assertGreater(result.note.non_whitespace_character_count, 20)
        self.assertEqual(result.warnings.total_count, 3)
        self.assertEqual(result.warnings.unique_count, 2)
        self.assertTrue(result.evidence_references.is_complete)
        self.assertEqual(result.evidence_references.citation_coverage_ratio, 1.0)
        self.assertTrue(result.outputs.evidence_count_matches_timeline)
        self.assertTrue(result.outputs.visual_state_count_matches_timeline)

    def test_renders_structured_json_and_markdown_without_misnaming_confidence(self) -> None:
        result = diagnose_run(_write_run(self.root))

        json_output = render_json(result)
        markdown = render_diagnostics_markdown(result)

        parsed = json.loads(json_output)
        self.assertEqual(parsed["profile"], "balanced")
        self.assertEqual(parsed["average_confidence"], 0.6)
        self.assertIn("平均置信度", markdown)
        self.assertIn("分阶段耗时", markdown)
        self.assertNotIn("accuracy", json_output.casefold())
        self.assertNotIn("准确率", markdown)

    def test_zero_duration_and_absent_confidence_are_explicitly_undefined(self) -> None:
        result = diagnose_run(_write_run(self.root, duration_us=0, include_confidence=False))

        self.assertIsNone(result.realtime_factor)
        self.assertEqual(result.evidence_temporal_covered_us, 0)
        self.assertIsNone(result.evidence_temporal_coverage_ratio)
        self.assertEqual(result.evidence_spans_with_confidence, 0)
        self.assertIsNone(result.average_confidence)
        self.assertTrue(
            all(item.temporal_coverage_ratio is None for item in result.evidence_by_modality)
        )

    def test_reports_cross_artifact_reference_gaps(self) -> None:
        result = diagnose_run(_write_run(self.root, ghost_reference=True))

        references = result.evidence_references
        self.assertFalse(references.all_references_valid)
        self.assertFalse(references.embedded_evidence_matches_timeline)
        self.assertFalse(references.is_complete)
        self.assertEqual(references.unknown_citation_ids, ("ev-ghost",))
        self.assertEqual(
            references.note_evidence_missing_from_timeline_ids,
            ("ev-ghost",),
        )

    def test_reports_missing_screenshot_output_and_outcome_count_mismatch(self) -> None:
        result = diagnose_run(
            _write_run(
                self.root,
                missing_screenshot=True,
                missing_output=True,
                mismatched_outcome_counts=True,
            )
        )

        self.assertEqual(result.note.existing_screenshot_file_count, 0)
        self.assertEqual(result.note.missing_screenshot_paths, ("vision/screenshot.png",))
        self.assertEqual(result.outputs.missing_artifact_paths, ("render/note.html",))
        self.assertFalse(result.outputs.evidence_count_matches_timeline)
        self.assertFalse(result.outputs.visual_state_count_matches_timeline)

    def test_rejects_non_completed_run_and_stage(self) -> None:
        with self.subTest("run"):
            path = _write_run(self.root / "pending-run", run_status=RunStatus.RUNNING)
            with self.assertRaisesRegex(RunNotCompleteError, "not completed"):
                diagnose_run(path)

        with self.subTest("stage"):
            path = _write_run(
                self.root / "pending-stage",
                stage_status=StageStatus.RUNNING,
            )
            with self.assertRaisesRegex(RunNotCompleteError, "incomplete stages"):
                diagnose_run(path)

    def test_rejects_missing_artifact_and_mismatched_run_id(self) -> None:
        with self.subTest("missing"):
            path = _write_run(self.root / "missing")
            (path / "evidence" / "timeline.json").unlink()
            with self.assertRaisesRegex(RunArtifactError, "does not exist"):
                diagnose_run(path)

        with self.subTest("run-id"):
            path = _write_run(self.root / "wrong-id")
            outcome_path = path / "render" / "outcome.json"
            payload = json.loads(outcome_path.read_text(encoding="utf-8"))
            payload["run_id"] = "another-run"
            outcome_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RunArtifactError, "run ID mismatch"):
                diagnose_run(path)


class RunComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_compares_three_profiles_in_fixed_order(self) -> None:
        paths = [
            _write_run(self.root / "accurate", profile="accurate", stage_scale=3.0),
            _write_run(self.root / "fast", profile="fast", stage_scale=0.5),
            _write_run(self.root / "balanced", profile="balanced", stage_scale=1.0),
        ]

        comparison = compare_runs(paths)
        self.assertEqual(
            tuple(item.profile for item in comparison.profiles),
            ("fast", "balanced", "accurate"),
        )
        self.assertEqual(
            tuple(item.realtime_factor for item in comparison.profiles),
            (0.5, 1.0, 3.0),
        )
        self.assertTrue(comparison.profiles[0].used_deterministic_note_fallback)

        markdown = render_comparison_markdown(comparison)
        structured = json.loads(render_json(comparison))
        self.assertIn("| 指标 | fast | balanced | accurate |", markdown)
        self.assertEqual(structured["profiles"][2]["profile"], "accurate")

    def test_rejects_runs_from_different_sources(self) -> None:
        paths = [
            _write_run(self.root / "fast", profile="fast"),
            _write_run(self.root / "balanced", profile="balanced"),
            _write_run(self.root / "accurate", profile="accurate", source_id="BV1other"),
        ]
        with self.assertRaisesRegex(RunSourceMismatchError, "different sources"):
            compare_runs(paths)

    def test_rejects_missing_duplicate_or_unexpected_profiles(self) -> None:
        with self.subTest("missing"):
            paths = [
                _write_run(self.root / "missing-fast", profile="fast"),
                _write_run(self.root / "missing-balanced", profile="balanced"),
            ]
            with self.assertRaisesRegex(RunProfileSetError, "requires exactly"):
                compare_runs(paths)

        with self.subTest("duplicate"):
            paths = [
                _write_run(self.root / "duplicate-fast-a", profile="fast"),
                _write_run(self.root / "duplicate-fast-b", profile="fast"),
                _write_run(self.root / "duplicate-accurate", profile="accurate"),
            ]
            with self.assertRaisesRegex(RunProfileSetError, "duplicate"):
                compare_runs(paths)

        with self.subTest("unexpected"):
            paths = [
                _write_run(self.root / "unexpected-fast", profile="fast"),
                _write_run(self.root / "unexpected-balanced", profile="balanced"),
                _write_run(self.root / "unexpected-custom", profile="custom"),
            ]
            with self.assertRaisesRegex(RunProfileSetError, "unexpected=custom"):
                compare_runs(paths)
