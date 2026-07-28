from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from pydantic import ValidationError

from video2notes.domain import EvidenceModality, EvidenceSpan
from video2notes.notes import (
    FactCard,
    NoteDocument,
    NoteMetadata,
    NoteScreenshot,
    NoteSection,
    format_timestamp,
    render_html,
    render_markdown,
    render_pdf_from_html,
)


def sample_note(*, screenshot: NoteScreenshot | None = None) -> NoteDocument:
    evidence = EvidenceSpan(
        id="ev-asr-1",
        run_id="run-1",
        modality=EvidenceModality.ASR,
        start_us=1_000_000,
        end_us=2_500_000,
        raw_text="真正的内容",
        normalized_text="真正的内容",
        confidence=0.95,
    )
    return NoteDocument(
        metadata=NoteMetadata(
            title="高精度测试笔记",
            run_id="run-1",
            source_kind="local",
            source_locator="source/video.mp4",
            duration_us=125_000_000,
            languages=["zh-CN"],
            quality_mode="accurate",
            quality_warnings=["源视频只有 480p，小字 OCR 可能不可读。"],
            created_at=datetime(2026, 7, 28, tzinfo=UTC),
        ),
        abstract="这是按证据组织的摘要。",
        key_takeaways=["保留时间关系", "不猜测不可读文字"],
        evidence=[evidence],
        facts=[
            FactCard(
                id="fact-1",
                claim="内容需要证据",
                evidence_ids=["ev-asr-1"],
                confidence=0.95,
            )
        ],
        sections=[
            NoteSection(
                id="section-1",
                title="核心方法",
                start_us=1_000_000,
                end_us=20_000_000,
                summary="先建立证据，再生成笔记。",
                body_markdown="### 步骤\n\n- 解码\n- 对齐\n- 核验",
                evidence_ids=["ev-asr-1"],
                fact_ids=["fact-1"],
                screenshots=[screenshot] if screenshot else [],
            )
        ],
        glossary={"PTS": "Presentation Timestamp"},
    )


class NoteModelTests(unittest.TestCase):
    def test_unknown_evidence_reference_is_rejected(self) -> None:
        payload = sample_note().model_dump()
        payload["sections"][0]["evidence_ids"] = ["missing"]
        with self.assertRaisesRegex(ValidationError, "unknown IDs"):
            NoteDocument.model_validate(payload)

    def test_screenshot_must_be_inside_section_time(self) -> None:
        with self.assertRaisesRegex(ValidationError, "outside section"):
            sample_note(
                screenshot=NoteScreenshot(
                    relative_path="vision/frame.png",
                    timestamp_us=30_000_000,
                    caption="太晚",
                    alt_text="测试",
                    evidence_ids=["ev-asr-1"],
                )
            )


class NoteRendererTests(unittest.TestCase):
    def test_markdown_is_primary_and_keeps_seek_and_evidence_links(self) -> None:
        result = render_markdown(sample_note())
        self.assertIn("# 高精度测试笔记", result)
        self.assertIn("video2notes://seek/run-1?time_us=1000000", result)
        self.assertIn("<!-- evidence: ev-asr-1 -->", result)
        self.assertIn("画质/识别提示", result)
        self.assertEqual(format_timestamp(3_661_000_000), "01:01:01")

    def test_html_embeds_local_screenshot_and_escapes_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path = root / "vision" / "frame.png"
            image_path.parent.mkdir()
            Image.new("RGB", (8, 8), (23, 108, 112)).save(image_path)
            note = sample_note(
                screenshot=NoteScreenshot(
                    relative_path="vision/frame.png",
                    timestamp_us=2_000_000,
                    caption="证据画面",
                    alt_text="青色测试图",
                    evidence_ids=["ev-asr-1"],
                )
            )
            result = render_html(note, artifact_root=root)
        self.assertIn("data:image/png;base64,", result)
        self.assertIn("evidence-rail", result)
        self.assertIn("证据画面", result)
        self.assertNotIn("<script>", result)

    def test_pdf_runner_must_create_a_nonempty_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "note.html"
            target = root / "note.pdf"
            browser = root / "msedge.exe"
            source.write_text("<html></html>", encoding="utf-8")
            browser.write_bytes(b"fake")

            def runner(
                command: list[str] | tuple[str, ...],
            ) -> subprocess.CompletedProcess[str]:
                output_arg = next(value for value in command if value.startswith("--print-to-pdf="))
                Path(output_arg.split("=", 1)[1]).write_bytes(b"%PDF-test")
                return subprocess.CompletedProcess(command, 0, "", "")

            result = render_pdf_from_html(
                source,
                target,
                browser_executable=browser,
                runner=runner,
            )
            self.assertEqual(result.read_bytes(), b"%PDF-test")
