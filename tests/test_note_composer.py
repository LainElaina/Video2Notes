from __future__ import annotations

import json
import threading
import time
import unittest

from video2notes.domain import BoundingBox, EvidenceModality, EvidenceSpan, VisualState
from video2notes.fusion import build_evidence_timeline
from video2notes.llm import GenerationRequest, GenerationResult
from video2notes.notes import (
    EvidenceNoteComposer,
    NoteMetadata,
    NoteScreenshot,
    ReportPreset,
    ReportSpec,
    SupportingMaterial,
    SupportingMaterialKind,
    build_deterministic_note,
)


class FakeBackend:
    def __init__(self, provider_id: str, model_id: str, outputs: dict[str, dict]):
        self.provider_id = provider_id
        self.model_id = model_id
        self.outputs = outputs
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        output = self.outputs[request.role]
        return GenerationResult(
            provider=self.provider_id,
            model=self.model_id,
            role=request.role,
            parsed=output,
            raw_text="{}",
            latency_seconds=0.01,
        )


def fixture():
    evidence = [
        EvidenceSpan(
            id="asr-1",
            run_id="run-1",
            modality=EvidenceModality.ASR,
            start_us=0,
            end_us=2_000_000,
            raw_text="先建立证据，再写笔记。",
            normalized_text="先建立证据，再写笔记。",
            confidence=0.95,
        ),
        EvidenceSpan(
            id="ocr-1",
            run_id="run-1",
            modality=EvidenceModality.OCR,
            start_us=0,
            end_us=2_000_000,
            raw_text="Evidence First",
            normalized_text="Evidence First",
            confidence=0.9,
        ),
    ]
    fusion = build_evidence_timeline(evidence)
    metadata = NoteMetadata(
        title="证据优先",
        run_id="run-1",
        source_kind="local",
        source_locator="media/video.mp4",
        duration_us=2_000_000,
        languages=["zh-CN", "en"],
        quality_mode="accurate",
    )
    return metadata, fusion


class NoteComposerTests(unittest.TestCase):
    def test_deterministic_note_keeps_modalities_and_evidence(self) -> None:
        metadata, fusion = fixture()
        note = build_deterministic_note(metadata, fusion)
        self.assertEqual(len(note.sections), 1)
        self.assertIn("**语音**", note.sections[0].body_markdown)
        self.assertIn("**画面文字**", note.sections[0].body_markdown)
        self.assertEqual(
            set(note.sections[0].evidence_ids),
            {"asr-1", "ocr-1"},
        )

    def test_deterministic_note_keeps_metadata_as_provenance_not_a_fact(self) -> None:
        evidence = [
            EvidenceSpan(
                id="metadata-source",
                run_id="run-metadata",
                modality=EvidenceModality.METADATA,
                start_us=0,
                end_us=2_000_000,
                raw_text="clip-210-300 · local-file",
                normalized_text="clip-210-300 local-file",
            ),
            EvidenceSpan(
                id="asr-instruction",
                run_id="run-metadata",
                modality=EvidenceModality.ASR,
                start_us=0,
                end_us=2_000_000,
                raw_text="打开弹幕机并安装 TTS 插件。",
                normalized_text="打开弹幕机并安装 TTS 插件。",
                confidence=0.94,
            ),
        ]
        fusion = build_evidence_timeline(evidence)
        metadata = NoteMetadata(
            title="多平台直播和发布视频",
            run_id="run-metadata",
            source_kind="local",
            source_locator="clip-210-300.mp4",
            duration_us=2_000_000,
            quality_mode="balanced",
        )

        note = build_deterministic_note(metadata, fusion)

        self.assertEqual(note.metadata.title, "多平台直播和发布视频")
        self.assertEqual(note.key_takeaways, ["打开弹幕机并安装 TTS 插件。"])
        self.assertEqual([item.claim for item in note.facts], ["打开弹幕机并安装 TTS 插件。"])
        self.assertEqual(
            {item.id for item in note.evidence},
            {"metadata-source", "asr-instruction"},
        )
        self.assertEqual(
            set(note.sections[0].evidence_ids),
            {"metadata-source", "asr-instruction"},
        )

    def test_roles_can_use_different_models_and_verifier_marks_review(self) -> None:
        metadata, fusion = fixture()
        fact = FakeBackend(
            "cheap",
            "extractor",
            {
                "notes.fact_extractor": {
                    "facts": [
                        {
                            "claim": "先建立证据",
                            "evidence_ids": ["asr-1"],
                            "confidence": 0.95,
                            "kind": "principle",
                            "needs_review": False,
                        }
                    ]
                }
            },
        )
        draft = FakeBackend(
            "writer",
            "long-context",
            {
                "notes.drafter": {
                    "abstract": "证据优先流程",
                    "key_takeaways": ["先建立证据"],
                    "sections": [
                        {
                            "title": "原则",
                            "start_us": 0,
                            "end_us": 2_000_000,
                            "summary": "先证据后笔记",
                            "body_markdown": "- 建立证据\n- 生成笔记",
                            "evidence_ids": ["asr-1"],
                            "fact_ids": ["fact-window-00000-000"],
                        }
                    ],
                    "glossary": {},
                }
            },
        )
        verifier = FakeBackend(
            "judge",
            "precise",
            {
                "notes.verifier": {
                    "items": [
                        {
                            "fact_id": "fact-window-00000-000",
                            "supported": False,
                            "reason": "需要人工复查",
                        }
                    ]
                }
            },
        )
        result = EvidenceNoteComposer(
            fact_backend=fact,
            draft_backend=draft,
            verifier_backend=verifier,
        ).compose(metadata, fusion, verification_passes=2)
        self.assertFalse(result.used_deterministic_fallback)
        self.assertTrue(result.note.facts[0].needs_review)
        self.assertEqual(
            [(item.provider, item.model, item.role) for item in result.invocations],
            [
                ("cheap", "extractor", "notes.fact_extractor"),
                ("writer", "long-context", "notes.drafter"),
                ("judge", "precise", "notes.verifier"),
                ("judge", "precise", "notes.verifier"),
            ],
        )

    def test_remote_model_payloads_and_final_note_are_redacted(self) -> None:
        run_id = "run-private-note"
        evidence = [
            EvidenceSpan(
                id="asr-secret",
                run_id=run_id,
                modality=EvidenceModality.ASR,
                start_us=0,
                end_us=2_000_000,
                raw_text="交流群 1060030164",
                normalized_text="交流群 1060030164",
                confidence=0.95,
            )
        ]
        fusion = build_evidence_timeline(evidence)
        metadata = NoteMetadata(
            title="本地直播教程",
            run_id=run_id,
            source_kind="local",
            source_locator="video.mp4?token=url-secret",
            duration_us=2_000_000,
            quality_mode="accurate",
        )
        fact = FakeBackend(
            "facts",
            "extractor",
            {
                "notes.fact_extractor": {
                    "facts": [
                        {
                            "claim": "推流码: TESTsecret1234",
                            "evidence_ids": ["asr-secret"],
                        }
                    ]
                }
            },
        )
        draft = FakeBackend(
            "writer",
            "draft",
            {
                "notes.drafter": {
                    "abstract": "电话 13812345678",
                    "key_takeaways": ["邮箱 demo@example.com"],
                    "sections": [
                        {
                            "title": "房间号 123456789",
                            "start_us": 0,
                            "end_us": 2_000_000,
                            "summary": "推流码: TESTsecret1234",
                            "body_markdown": "微信 Example_123",
                            "evidence_ids": ["asr-secret"],
                            "fact_ids": ["fact-window-00000-000"],
                            "material_ids": ["material-Privacy"],
                        }
                    ],
                    "glossary": {},
                }
            },
        )
        verifier = FakeBackend(
            "judge",
            "verifier",
            {
                "notes.verifier": {
                    "items": [
                        {
                            "fact_id": "fact-window-00000-000",
                            "supported": True,
                            "reason": "直接支持",
                        }
                    ]
                }
            },
        )
        material = SupportingMaterial(
            id="material-Privacy",
            kind=SupportingMaterialKind.TEXT,
            title="补充邮箱 demo@example.com",
            artifact_path="supporting/material-Privacy/text.txt",
            media_type="text/plain",
            sha256="0" * 64,
            size_bytes=32,
            text_content="手机号 13812345678",
        )

        result = EvidenceNoteComposer(
            fact_backend=fact,
            draft_backend=draft,
            verifier_backend=verifier,
        ).compose(metadata, fusion, supporting_materials=[material])

        remote_payloads = [
            request.user_prompt
            for backend in (fact, draft, verifier)
            for request in backend.requests
        ]
        final_payload = result.note.model_dump_json()
        for secret in (
            "1060030164",
            "TESTsecret1234",
            "13812345678",
            "demo@example.com",
            "123456789",
            "Example_123",
            "url-secret",
        ):
            self.assertNotIn(secret, final_payload)
            self.assertTrue(all(secret not in payload for payload in remote_payloads))

    def test_llm_sections_apply_temporally_spread_screenshot_budget(self) -> None:
        metadata, fusion = fixture()
        fact = FakeBackend(
            "facts",
            "extractor",
            {
                "notes.fact_extractor": {
                    "facts": [
                        {
                            "claim": "先建立证据",
                            "evidence_ids": ["asr-1"],
                        }
                    ]
                }
            },
        )
        draft = FakeBackend(
            "writer",
            "draft",
            {
                "notes.drafter": {
                    "abstract": "证据优先流程",
                    "key_takeaways": ["先建立证据"],
                    "sections": [
                        {
                            "title": "原则",
                            "start_us": 0,
                            "end_us": 2_000_000,
                            "summary": "先证据后笔记",
                            "body_markdown": "建立证据后生成笔记。",
                            "evidence_ids": ["asr-1"],
                            "fact_ids": ["fact-window-00000-000"],
                        }
                    ],
                    "glossary": {},
                }
            },
        )
        timestamps = [2_000_000, 500_000, 1_500_000, 0, 1_000_000]
        screenshots = {
            fusion.windows[0].id: [
                NoteScreenshot(
                    relative_path=f"screenshots/{timestamp}.jpg",
                    timestamp_us=timestamp,
                    caption=f"截图 {timestamp}",
                    alt_text=f"截图 {timestamp}",
                    evidence_ids=["asr-1"],
                )
                for timestamp in timestamps
            ]
        }

        result = EvidenceNoteComposer(
            fact_backend=fact,
            draft_backend=draft,
        ).compose(
            metadata,
            fusion,
            screenshots_by_window=screenshots,
            max_screenshots_per_section=2,
        )

        self.assertFalse(result.used_deterministic_fallback)
        self.assertEqual(
            [item.timestamp_us for item in result.note.sections[0].screenshots],
            [0, 2_000_000],
        )

    def test_concise_report_caps_runtime_screenshot_budget_to_one_per_section(self) -> None:
        metadata, fusion = fixture()
        screenshots = {
            fusion.windows[0].id: [
                NoteScreenshot(
                    relative_path=f"screenshots/{timestamp}.jpg",
                    timestamp_us=timestamp,
                    caption=f"截图 {timestamp}",
                    alt_text=f"截图 {timestamp}",
                    evidence_ids=["asr-1"],
                )
                for timestamp in (0, 1_000_000, 2_000_000)
            ]
        }

        note = build_deterministic_note(
            metadata,
            fusion,
            screenshots_by_window=screenshots,
            max_screenshots_per_section=4,
            report_spec=ReportSpec(preset=ReportPreset.CONCISE),
        )

        self.assertEqual(
            [item.timestamp_us for item in note.sections[0].screenshots],
            [1_000_000],
        )

    def test_fact_windows_use_the_configured_remote_concurrency(self) -> None:
        evidence = [
            EvidenceSpan(
                id="speech-a",
                run_id="parallel-run",
                modality=EvidenceModality.ASR,
                start_us=0,
                end_us=1_000_000,
                raw_text="第一段",
                normalized_text="第一段",
            ),
            EvidenceSpan(
                id="speech-b",
                run_id="parallel-run",
                modality=EvidenceModality.ASR,
                start_us=5_000_000,
                end_us=6_000_000,
                raw_text="第二段",
                normalized_text="第二段",
            ),
        ]
        fusion = build_evidence_timeline(evidence)
        self.assertEqual(len(fusion.windows), 2)
        metadata = NoteMetadata(
            title="并发事实提取",
            run_id="parallel-run",
            source_kind="local",
            source_locator="parallel.mp4",
            duration_us=6_000_000,
            quality_mode="balanced",
        )

        class ConcurrentFactBackend:
            provider_id = "parallel"
            model_id = "facts"

            def __init__(self) -> None:
                self.active = 0
                self.maximum_active = 0
                self.lock = threading.Lock()

            def generate(self, request: GenerationRequest) -> GenerationResult:
                payload = json.loads(request.user_prompt)
                evidence_id = payload["evidence"][0]["id"]
                with self.lock:
                    self.active += 1
                    self.maximum_active = max(self.maximum_active, self.active)
                time.sleep(0.03)
                with self.lock:
                    self.active -= 1
                return GenerationResult(
                    provider=self.provider_id,
                    model=self.model_id,
                    role=request.role,
                    parsed={
                        "facts": [
                            {
                                "claim": f"事实 {evidence_id}",
                                "evidence_ids": [evidence_id],
                            }
                        ]
                    },
                    raw_text="{}",
                    latency_seconds=0.03,
                )

        facts = ConcurrentFactBackend()
        draft = FakeBackend(
            "writer",
            "draft",
            {
                "notes.drafter": {
                    "abstract": "两段事实",
                    "key_takeaways": ["第一段", "第二段"],
                    "sections": [
                        {
                            "title": "内容",
                            "start_us": 0,
                            "end_us": 6_000_000,
                            "summary": "两段内容",
                            "body_markdown": "第一段；第二段。",
                            "evidence_ids": ["speech-a", "speech-b"],
                            "fact_ids": [
                                "fact-window-00000-000",
                                "fact-window-00001-000",
                            ],
                        }
                    ],
                    "glossary": {},
                }
            },
        )

        result = EvidenceNoteComposer(
            fact_backend=facts,
            draft_backend=draft,
        ).compose(metadata, fusion, max_model_concurrency=2)

        self.assertFalse(result.used_deterministic_fallback)
        self.assertEqual(facts.maximum_active, 2)
        self.assertEqual(
            [item.id for item in result.note.facts],
            [
                "fact-window-00000-000",
                "fact-window-00001-000",
            ],
        )

    def test_unknown_evidence_from_model_triggers_safe_fallback(self) -> None:
        metadata, fusion = fixture()
        bad_fact = FakeBackend(
            "bad",
            "bad",
            {
                "notes.fact_extractor": {
                    "facts": [
                        {
                            "claim": "unsupported",
                            "evidence_ids": ["invented"],
                        }
                    ]
                }
            },
        )
        unused_draft = FakeBackend("draft", "draft", {})
        result = EvidenceNoteComposer(
            fact_backend=bad_fact,
            draft_backend=unused_draft,
        ).compose(metadata, fusion)
        self.assertTrue(result.used_deterministic_fallback)
        self.assertGreater(len(result.note.facts), 0)
        self.assertTrue(any("fallback" in item for item in result.warnings))

    def test_report_profile_is_resolved_for_drafter_and_persisted(self) -> None:
        metadata, fusion = fixture()
        fact = FakeBackend(
            "facts",
            "extractor",
            {
                "notes.fact_extractor": {
                    "facts": [
                        {
                            "claim": "先建立证据",
                            "evidence_ids": ["asr-1"],
                        }
                    ]
                }
            },
        )
        draft = FakeBackend(
            "writer",
            "executive",
            {
                "notes.drafter": {
                    "abstract": "管理摘要",
                    "key_takeaways": ["结论"],
                    "sections": [
                        {
                            "title": "结论",
                            "start_us": 0,
                            "end_us": 2_000_000,
                            "summary": "先证据后笔记",
                            "body_markdown": "证据充分。",
                            "evidence_ids": ["asr-1"],
                            "fact_ids": ["fact-window-00000-000"],
                        }
                    ],
                    "glossary": {"PTS": "不应出现在领导版"},
                }
            },
        )
        result = EvidenceNoteComposer(
            fact_backend=fact,
            draft_backend=draft,
        ).compose(
            metadata,
            fusion,
            report_spec=ReportSpec(preset=ReportPreset.EXECUTIVE),
        )
        request_payload = draft.requests[0].user_prompt
        self.assertIn('"preset": "executive"', request_payload)
        self.assertIn("管理者", request_payload)
        self.assertEqual(result.note.metadata.report_preset, "executive")
        self.assertEqual(result.note.glossary, {})

    def test_concise_deterministic_profile_limits_sections(self) -> None:
        metadata, fusion = fixture()
        note = build_deterministic_note(
            metadata,
            fusion,
            report_spec=ReportSpec(
                preset=ReportPreset.CONCISE,
                max_sections=1,
                max_takeaways=1,
            ),
        )
        self.assertLessEqual(len(note.sections), 1)
        self.assertLessEqual(len(note.key_takeaways), 1)
        self.assertEqual(note.metadata.report_preset, "concise")

    def test_deterministic_note_does_not_repeat_long_ocr_span_across_windows(self) -> None:
        evidence = [
            EvidenceSpan(
                id="ocr-persistent",
                run_id="run-repeat",
                modality=EvidenceModality.OCR,
                start_us=0,
                end_us=3_000_000,
                raw_text="平台直播设置",
                normalized_text="平台直播设置",
                confidence=0.95,
            ),
            EvidenceSpan(
                id="speech-first",
                run_id="run-repeat",
                modality=EvidenceModality.ASR,
                start_us=0,
                end_us=500_000,
                raw_text="第一段",
                normalized_text="第一段",
            ),
            EvidenceSpan(
                id="speech-second",
                run_id="run-repeat",
                modality=EvidenceModality.ASR,
                start_us=2_000_000,
                end_us=2_500_000,
                raw_text="第二段",
                normalized_text="第二段",
            ),
        ]
        fusion = build_evidence_timeline(evidence)
        metadata = NoteMetadata(
            title="跨窗口",
            run_id="run-repeat",
            source_kind="local",
            source_locator="repeat.mp4",
            duration_us=3_000_000,
            quality_mode="balanced",
        )

        note = build_deterministic_note(metadata, fusion)

        screen_facts = [item for item in note.facts if item.kind == "screen_text"]
        self.assertEqual([item.claim for item in screen_facts], ["平台直播设置"])

    def test_deterministic_note_caps_ocr_facts_without_dropping_evidence(self) -> None:
        evidence = [
            EvidenceSpan(
                id="speech",
                run_id="run-budget",
                modality=EvidenceModality.ASR,
                start_us=0,
                end_us=1_000_000,
                raw_text="讲解多平台发布流程",
                normalized_text="讲解多平台发布流程",
            ),
            *[
                EvidenceSpan(
                    id=f"ocr-{index}",
                    run_id="run-budget",
                    modality=EvidenceModality.OCR,
                    start_us=0,
                    end_us=1_000_000,
                    raw_text=f"界面字段 {index}",
                    normalized_text=f"界面字段 {index}",
                    confidence=0.90 + index / 1000,
                )
                for index in range(12)
            ],
        ]
        fusion = build_evidence_timeline(evidence)
        metadata = NoteMetadata(
            title="事实预算",
            run_id="run-budget",
            source_kind="local",
            source_locator="budget.mp4",
            duration_us=1_000_000,
            quality_mode="balanced",
        )

        note = build_deterministic_note(metadata, fusion)

        self.assertEqual(len(note.evidence), len(evidence))
        self.assertLessEqual(
            sum(item.kind == "screen_text" for item in note.facts),
            8,
        )
        self.assertTrue(any(item.kind == "speech" for item in note.facts))

    def test_deterministic_note_ignores_repeated_edge_navigation_chrome(self) -> None:
        chrome = EvidenceSpan(
            id="ocr-chrome",
            run_id="run-chrome",
            modality=EvidenceModality.OCR,
            start_us=0,
            end_us=3_000_000,
            raw_text="首页",
            normalized_text="首页",
            confidence=0.99,
            bounding_boxes=[
                BoundingBox(x=1000, y=200, width=60, height=30),
            ],
            provenance={
                "observation_count": 5,
                "frame_width": 1080,
                "frame_height": 1920,
            },
        )
        content = EvidenceSpan(
            id="ocr-content",
            run_id="run-chrome",
            modality=EvidenceModality.OCR,
            start_us=0,
            end_us=3_000_000,
            raw_text="多平台直播教程",
            normalized_text="多平台直播教程",
            confidence=0.95,
            bounding_boxes=[
                BoundingBox(x=200, y=500, width=500, height=60),
            ],
            provenance={
                "observation_count": 5,
                "frame_width": 1080,
                "frame_height": 1920,
            },
        )
        fusion = build_evidence_timeline([chrome, content])
        metadata = NoteMetadata(
            title="过滤界面导航",
            run_id="run-chrome",
            source_kind="local",
            source_locator="chrome.mp4",
            duration_us=3_000_000,
            quality_mode="balanced",
        )

        note = build_deterministic_note(
            metadata,
            fusion,
            report_spec=ReportSpec(preset=ReportPreset.CONCISE),
        )

        self.assertNotIn("首页", [item.claim for item in note.facts])
        self.assertIn("多平台直播教程", [item.claim for item in note.facts])

    def test_deterministic_note_preserves_numeric_ocr_changes_across_windows(self) -> None:
        run_id = "run-numeric-change"
        evidence = [
            EvidenceSpan(
                id="temperature-before",
                run_id=run_id,
                modality=EvidenceModality.OCR,
                start_us=0,
                end_us=1_000_000,
                raw_text="temperature 20.0",
                normalized_text="temperature 20.0",
                confidence=0.98,
            ),
            EvidenceSpan(
                id="temperature-after",
                run_id=run_id,
                modality=EvidenceModality.OCR,
                start_us=1_000_000,
                end_us=2_000_000,
                raw_text="temperature 20.1",
                normalized_text="temperature 20.1",
                confidence=0.98,
            ),
        ]
        states = [
            VisualState(
                id="state-before",
                run_id=run_id,
                start_us=0,
                end_us=1_000_000,
                transition_us=0,
                stable_keyframe_us=500_000,
                change_reason="initial_state",
            ),
            VisualState(
                id="state-after",
                run_id=run_id,
                start_us=1_000_000,
                end_us=2_000_000,
                transition_us=1_000_000,
                stable_keyframe_us=1_500_000,
                change_reason="screen_text_change",
            ),
        ]
        fusion = build_evidence_timeline(evidence, states)
        metadata = NoteMetadata(
            title="温度变化",
            run_id=run_id,
            source_kind="local",
            source_locator="temperature.mp4",
            duration_us=2_000_000,
            quality_mode="accurate",
        )

        note = build_deterministic_note(metadata, fusion)

        self.assertEqual(
            [item.claim for item in note.facts],
            ["temperature 20.0", "temperature 20.1"],
        )
        self.assertEqual(len(note.sections), 1)
        self.assertEqual(set(note.sections[0].fact_ids), {item.id for item in note.facts})

    def test_deterministic_overview_prefers_chronological_speech_over_early_ocr(self) -> None:
        run_id = "run-speech-overview"
        evidence = [
            *[
                EvidenceSpan(
                    id=f"ocr-noise-{index}",
                    run_id=run_id,
                    modality=EvidenceModality.OCR,
                    start_us=0,
                    end_us=500_000,
                    raw_text=text,
                    normalized_text=text,
                    confidence=0.99,
                )
                for index, text in enumerate(("STORMCREW+", "APK", "ZIP"))
            ],
            *[
                EvidenceSpan(
                    id=f"speech-{index}",
                    run_id=run_id,
                    modality=EvidenceModality.ASR,
                    start_us=start,
                    end_us=start + 500_000,
                    raw_text=text,
                    normalized_text=text,
                    confidence=0.90,
                )
                for index, (start, text) in enumerate(
                    (
                        (1_000_000, "先下载弹幕机"),
                        (2_000_000, "好"),
                        (3_000_000, "然后安装语音插件"),
                        (5_000_000, "最后复制到插件目录"),
                    )
                )
            ],
        ]
        fusion = build_evidence_timeline(evidence)
        metadata = NoteMetadata(
            title="语音优先摘要",
            run_id=run_id,
            source_kind="local",
            source_locator="overview.mp4",
            duration_us=6_000_000,
            quality_mode="balanced",
        )

        note = build_deterministic_note(
            metadata,
            fusion,
            report_spec=ReportSpec(max_takeaways=3),
        )

        self.assertEqual(
            note.key_takeaways,
            ["先下载弹幕机", "然后安装语音插件", "最后复制到插件目录"],
        )
        self.assertEqual(
            note.abstract,
            "先下载弹幕机；然后安装语音插件；最后复制到插件目录",
        )

    def test_deterministic_overview_spans_the_full_timeline(self) -> None:
        run_id = "run-full-timeline-overview"
        evidence = [
            EvidenceSpan(
                id=f"speech-{index}",
                run_id=run_id,
                modality=EvidenceModality.ASR,
                start_us=index * 10_000_000,
                end_us=index * 10_000_000 + 1_000_000,
                raw_text=f"第{index + 1}阶段操作说明",
                normalized_text=f"第{index + 1}阶段操作说明",
                confidence=0.9,
            )
            for index in range(9)
        ]
        fusion = build_evidence_timeline(evidence)
        metadata = NoteMetadata(
            title="完整时间轴摘要",
            run_id=run_id,
            source_kind="local",
            source_locator="timeline.mp4",
            duration_us=90_000_000,
            quality_mode="balanced",
        )

        note = build_deterministic_note(
            metadata,
            fusion,
            report_spec=ReportSpec(max_takeaways=5),
        )

        self.assertEqual(
            note.key_takeaways,
            [
                "第1阶段操作说明",
                "第3阶段操作说明",
                "第5阶段操作说明",
                "第7阶段操作说明",
                "第9阶段操作说明",
            ],
        )
        self.assertEqual(
            note.abstract,
            "第1阶段操作说明；第5阶段操作说明；第9阶段操作说明",
        )

    def test_ocr_fact_ranking_keeps_descriptions_and_numeric_changes_ahead_of_ui(self) -> None:
        run_id = "run-ocr-ranking"
        texts = [
            "A",
            "APK",
            "ZIP",
            "STORMCREW+",
            "点击下载安装包并完成插件配置",
            "选择弹幕机的 plugins 目录",
            "复制文件后重新启动应用",
            "当前连接状态显示为成功",
            "temperature 20.0",
            "temperature 20.1",
            "40",
        ]
        evidence = [
            EvidenceSpan(
                id=f"ocr-rank-{index}",
                run_id=run_id,
                modality=EvidenceModality.OCR,
                start_us=0,
                end_us=1_000_000,
                raw_text=text,
                normalized_text=text,
                confidence=0.99 if index < 4 else 0.90,
            )
            for index, text in enumerate(texts)
        ]
        fusion = build_evidence_timeline(evidence)
        metadata = NoteMetadata(
            title="OCR 排序",
            run_id=run_id,
            source_kind="local",
            source_locator="ranking.mp4",
            duration_us=1_000_000,
            quality_mode="accurate",
        )

        note = build_deterministic_note(metadata, fusion)
        claims = [item.claim for item in note.facts]

        self.assertEqual(len(note.evidence), len(texts))
        self.assertNotIn("A", claims)
        self.assertIn("temperature 20.0", claims)
        self.assertIn("temperature 20.1", claims)
        self.assertIn("40", claims)
        self.assertLess(
            claims.index("点击下载安装包并完成插件配置"),
            claims.index("40"),
        )

    def test_balanced_section_coalescing_preserves_references_and_caps_screenshots(self) -> None:
        run_id = "run-section-coalesce"
        evidence = [
            EvidenceSpan(
                id=f"speech-part-{index}",
                run_id=run_id,
                modality=EvidenceModality.ASR,
                start_us=index * 1_000_000,
                end_us=(index + 1) * 1_000_000,
                raw_text=f"步骤 {index + 1}",
                normalized_text=f"步骤 {index + 1}",
                confidence=0.95,
            )
            for index in range(8)
        ]
        states = [
            VisualState(
                id=f"state-{index}",
                run_id=run_id,
                start_us=index * 1_000_000,
                end_us=(index + 1) * 1_000_000,
                transition_us=index * 1_000_000,
                stable_keyframe_us=index * 1_000_000 + 500_000,
                change_reason="screen_text_change",
            )
            for index in range(8)
        ]
        fusion = build_evidence_timeline(evidence, states)
        screenshots = {
            window.id: [
                NoteScreenshot(
                    relative_path=f"screenshots/{index}.jpg",
                    timestamp_us=window.start_us,
                    caption=f"截图 {index}",
                    alt_text=f"截图 {index}",
                    evidence_ids=list(window.evidence_ids),
                )
            ]
            for index, window in enumerate(fusion.windows)
        }
        materials = [
            SupportingMaterial(
                id="material-A",
                kind=SupportingMaterialKind.TEXT,
                title="开头资料",
                artifact_path="supporting/a.txt",
                media_type="text/plain",
                sha256="a" * 64,
                size_bytes=1,
                text_content="a",
                start_us=0,
                end_us=2_000_000,
            ),
            SupportingMaterial(
                id="material-B",
                kind=SupportingMaterialKind.TEXT,
                title="结尾资料",
                artifact_path="supporting/b.txt",
                media_type="text/plain",
                sha256="b" * 64,
                size_bytes=1,
                text_content="b",
                start_us=6_000_000,
                end_us=8_000_000,
            ),
        ]
        metadata = NoteMetadata(
            title="章节合并",
            run_id=run_id,
            source_kind="local",
            source_locator="sections.mp4",
            duration_us=8_000_000,
            quality_mode="balanced",
        )

        note = build_deterministic_note(
            metadata,
            fusion,
            screenshots_by_window=screenshots,
            max_screenshots_per_section=2,
            supporting_materials=materials,
        )

        self.assertEqual([item.id for item in note.sections], ["section-001", "section-002"])
        self.assertEqual(note.sections[0].end_us, note.sections[1].start_us)
        self.assertEqual(
            {identifier for section in note.sections for identifier in section.fact_ids},
            {item.id for item in note.facts},
        )
        self.assertEqual(
            {identifier for section in note.sections for identifier in section.evidence_ids},
            {item.id for item in note.evidence},
        )
        self.assertEqual(
            {identifier for section in note.sections for identifier in section.material_ids},
            {"material-A", "material-B"},
        )
        self.assertEqual(sum(len(section.screenshots) for section in note.sections), 4)
        for section in note.sections:
            timestamps = [item.timestamp_us for item in section.screenshots]
            midpoint = section.start_us + (section.end_us - section.start_us) // 2
            self.assertEqual(len(timestamps), 2)
            self.assertLess(timestamps[0], midpoint)
            self.assertGreater(timestamps[1], midpoint)

    def test_section_limit_coalesces_all_windows_without_truncating_the_tail(self) -> None:
        run_id = "run-long-coalesce"
        evidence = [
            EvidenceSpan(
                id=f"long-speech-{index}",
                run_id=run_id,
                modality=EvidenceModality.ASR,
                start_us=index * 1_000_000,
                end_us=(index + 1) * 1_000_000,
                raw_text=f"长视频步骤 {index + 1}",
                normalized_text=f"长视频步骤 {index + 1}",
                confidence=0.95,
            )
            for index in range(30)
        ]
        states = [
            VisualState(
                id=f"long-state-{index}",
                run_id=run_id,
                start_us=index * 1_000_000,
                end_us=(index + 1) * 1_000_000,
                transition_us=index * 1_000_000,
                stable_keyframe_us=index * 1_000_000 + 500_000,
                change_reason="screen_text_change",
            )
            for index in range(30)
        ]
        fusion = build_evidence_timeline(evidence, states)
        metadata = NoteMetadata(
            title="长视频章节覆盖",
            run_id=run_id,
            source_kind="local",
            source_locator="long.mp4",
            duration_us=30_000_000,
            quality_mode="accurate",
        )

        note = build_deterministic_note(
            metadata,
            fusion,
            report_spec=ReportSpec(
                preset=ReportPreset.CONCISE,
                max_sections=2,
            ),
        )

        self.assertLessEqual(len(note.sections), 2)
        self.assertEqual(note.sections[-1].end_us, 30_000_000)
        self.assertLessEqual(len(note.facts), 12)
        self.assertTrue(all(len(section.fact_ids) <= 6 for section in note.sections))
        self.assertEqual(
            {identifier for section in note.sections for identifier in section.evidence_ids},
            {item.id for item in note.evidence},
        )
        self.assertEqual(
            {identifier for section in note.sections for identifier in section.fact_ids},
            {item.id for item in note.facts},
        )
        claims = [item.claim for item in note.facts]
        self.assertIn("长视频步骤 1", claims)
        self.assertIn("长视频步骤 30", claims)

    def test_detailed_and_professional_notes_keep_persistent_edge_warning(self) -> None:
        warning = EvidenceSpan(
            id="ocr-edge-warning",
            run_id="run-edge-warning",
            modality=EvidenceModality.OCR,
            start_us=0,
            end_us=3_000_000,
            raw_text="系统过热告警",
            normalized_text="系统过热告警",
            confidence=0.99,
            bounding_boxes=[
                BoundingBox(x=1000, y=200, width=60, height=30),
            ],
            provenance={
                "observation_count": 5,
                "frame_width": 1080,
                "frame_height": 1920,
            },
        )
        fusion = build_evidence_timeline([warning])
        metadata = NoteMetadata(
            title="边缘告警",
            run_id="run-edge-warning",
            source_kind="local",
            source_locator="warning.mp4",
            duration_us=3_000_000,
            quality_mode="accurate",
        )

        for preset in (ReportPreset.DETAILED, ReportPreset.PROFESSIONAL):
            with self.subTest(preset=preset):
                note = build_deterministic_note(
                    metadata,
                    fusion,
                    report_spec=ReportSpec(preset=preset),
                )
                self.assertIn("系统过热告警", [item.claim for item in note.facts])
