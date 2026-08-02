from __future__ import annotations

import unittest

from video2notes.domain import EvidenceModality, EvidenceSpan
from video2notes.notes import (
    FactCard,
    NoteDocument,
    NoteMetadata,
    NoteScreenshot,
    NoteSection,
)
from video2notes.notes.redaction import (
    contains_sensitive_note_text,
    redact_note_text,
    redacted_evidence_copy,
    redacted_evidence_payload,
    sanitize_note_document,
)


class NoteRedactionTests(unittest.TestCase):
    def test_redacts_contextual_private_values(self) -> None:
        source = (
            "推流地址 rtmp://192.168.2.39:1935/live 密钥 stream_key=ABCdef123456；"
            "交流群 1060030164，电话 13812345678，邮箱 demo@example.com，"
            "房间号：123456789，微信: Example_123；"
            "https://example.com/callback?token=url-secret&mode=fast"
        )

        redacted = redact_note_text(source)

        for secret in (
            "192.168.2.39",
            "ABCdef123456",
            "1060030164",
            "13812345678",
            "demo@example.com",
            "123456789",
            "Example_123",
            "url-secret",
        ):
            self.assertNotIn(secret, redacted)
        self.assertIn("[已隐藏推流地址]", redacted)
        self.assertIn("[已隐藏密钥]", redacted)
        self.assertIn("[已隐藏标识]", redacted)
        self.assertIn("[已隐藏手机号]", redacted)
        self.assertIn("[已隐藏邮箱]", redacted)
        self.assertIn("[已隐藏微信号]", redacted)
        self.assertIn("token=[已隐藏参数]", redacted)
        self.assertTrue(contains_sensitive_note_text(source))

    def test_preserves_technical_numbers_and_public_urls(self) -> None:
        source = (
            "版本 7.30.0.9821，分辨率 2560x1440，6000kbps，日期 2026-08-02；"
            "公开文档 https://example.com/docs?id=123456789；OBS 30 FPS"
        )

        self.assertEqual(redact_note_text(source), source)
        self.assertFalse(contains_sensitive_note_text(source))

    def test_evidence_copy_redacts_nested_word_provenance(self) -> None:
        evidence = EvidenceSpan(
            id="ocr-1",
            run_id="run-1",
            modality=EvidenceModality.OCR,
            start_us=0,
            end_us=1,
            raw_text="交流群1060030164",
            normalized_text="交流群1060030164",
            provenance={"words": [{"text": "手机号13812345678"}]},
        )

        redacted = redacted_evidence_copy(evidence)
        payload = redacted_evidence_payload(evidence)

        self.assertEqual(evidence.raw_text, "交流群1060030164")
        self.assertNotIn("1060030164", redacted.raw_text or "")
        self.assertNotIn("13812345678", str(redacted.provenance))
        self.assertNotIn("1060030164", str(payload))
        self.assertNotIn("13812345678", str(payload))
        self.assertEqual(redacted.id, evidence.id)
        self.assertEqual(redacted.start_us, evidence.start_us)

    def test_sanitizes_note_and_keeps_reference_graph_valid(self) -> None:
        evidence = EvidenceSpan(
            id="ocr-1",
            run_id="run-1",
            modality=EvidenceModality.OCR,
            start_us=0,
            end_us=1_000_000,
            raw_text="推流码: ABCdef123456",
            normalized_text="推流码: ABCdef123456",
        )
        note = NoteDocument(
            metadata=NoteMetadata(
                title="联系 13812345678",
                run_id="run-1",
                source_kind="local_file",
                source_locator="video.mp4?token=url-secret",
                duration_us=1_000_000,
                quality_mode="balanced",
            ),
            abstract="交流群1060030164",
            key_takeaways=["邮箱 demo@example.com"],
            sections=[
                NoteSection(
                    id="section-1",
                    title="房间号123456789",
                    start_us=0,
                    end_us=1_000_000,
                    summary="推流码: ABCdef123456",
                    body_markdown="微信 Example_123",
                    evidence_ids=["ocr-1"],
                    fact_ids=["fact-1"],
                    screenshots=[
                        NoteScreenshot(
                            relative_path="notes/assets/frame.jpg",
                            timestamp_us=500_000,
                            caption="手机号13812345678",
                            alt_text="手机号13812345678",
                            evidence_ids=["ocr-1"],
                        )
                    ],
                )
            ],
            facts=[
                FactCard(
                    id="fact-1",
                    claim="rtmp://127.0.0.1/live",
                    evidence_ids=["ocr-1"],
                )
            ],
            evidence=[evidence],
        )

        sanitized = sanitize_note_document(note)
        dumped = sanitized.model_dump_json()

        for secret in (
            "13812345678",
            "url-secret",
            "1060030164",
            "demo@example.com",
            "123456789",
            "ABCdef123456",
            "Example_123",
            "127.0.0.1",
        ):
            self.assertNotIn(secret, dumped)
        self.assertEqual(sanitized.sections[0].evidence_ids, ["ocr-1"])
        self.assertEqual(sanitized.facts[0].evidence_ids, ["ocr-1"])
        self.assertEqual(sanitized.evidence[0].id, "ocr-1")
        self.assertEqual(note.evidence[0].raw_text, "推流码: ABCdef123456")


if __name__ == "__main__":
    unittest.main()
