from __future__ import annotations

import unittest

from video2notes.domain import EvidenceModality, EvidenceSpan
from video2notes.fusion import build_evidence_timeline
from video2notes.llm import GenerationRequest, GenerationResult
from video2notes.notes import (
    EvidenceNoteComposer,
    NoteMetadata,
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
        ).compose(metadata, fusion)
        self.assertFalse(result.used_deterministic_fallback)
        self.assertTrue(result.note.facts[0].needs_review)
        self.assertEqual(
            [(item.provider, item.model, item.role) for item in result.invocations],
            [
                ("cheap", "extractor", "notes.fact_extractor"),
                ("writer", "long-context", "notes.drafter"),
                ("judge", "precise", "notes.verifier"),
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
