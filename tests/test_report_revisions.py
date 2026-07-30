from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel

from video2notes.artifacts import RunWorkspace
from video2notes.domain import (
    EvidenceModality,
    EvidenceSpan,
    MediaManifest,
    SourceDescriptor,
)
from video2notes.fusion import build_evidence_timeline
from video2notes.llm import GenerationRequest, GenerationResult
from video2notes.materials import MaterialStore, TextMaterialRequest
from video2notes.notes import (
    EvidenceNoteComposer,
    NoteDocument,
    NoteMetadata,
    OutputFormat,
    ReportPreset,
    ReportRevisionService,
    ReportSpec,
    SupportingMaterial,
    SupportingMaterialKind,
    build_deterministic_note,
    render_html,
    render_markdown,
)
from video2notes.operations import EvidenceRevision
from video2notes.sources import (
    AcquisitionResult,
    Platform,
    SourceInput,
    SourceManifest,
)


class FakeBackend:
    def __init__(self, role_outputs: dict[str, dict[str, Any]]):
        self.provider_id = "test-provider"
        self.model_id = "test-model"
        self.role_outputs = role_outputs
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(
            provider=self.provider_id,
            model=self.model_id,
            role=request.role,
            parsed=self.role_outputs[request.role],
            raw_text="{}",
            latency_seconds=0.01,
        )


class FailingComposer:
    def compose(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("synthetic revision failure")


def _png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (10, 6), (24, 166, 111)).save(stream, format="PNG")
    return stream.getvalue()


def _material(
    identifier: str,
    *,
    kind: SupportingMaterialKind = SupportingMaterialKind.TEXT,
    artifact_path: str = "supporting/files/example.md",
    text_content: str | None = "评论区补充的完整资料",
    start_us: int | None = None,
    end_us: int | None = None,
) -> SupportingMaterial:
    payload = text_content.encode("utf-8") if text_content is not None else b"image"
    return SupportingMaterial(
        id=identifier,
        kind=kind,
        title="外部资料",
        artifact_path=artifact_path,
        media_type=(
            "text/markdown; charset=utf-8"
            if kind is SupportingMaterialKind.TEXT
            else "image/png"
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        text_content=text_content,
        start_us=start_us,
        end_us=end_us,
    )


def _composition_fixture() -> tuple[NoteMetadata, Any]:
    evidence = EvidenceSpan(
        id="asr-1",
        run_id="run-revision",
        modality=EvidenceModality.ASR,
        start_us=0,
        end_us=2_000_000,
        raw_text="视频中的原话",
        normalized_text="视频中的原话",
        confidence=0.96,
        language="zh-CN",
    )
    return (
        NoteMetadata(
            title="补充资料测试",
            run_id="run-revision",
            source_kind="local",
            source_locator="video.mp4",
            duration_us=2_000_000,
            languages=["zh-CN"],
            quality_mode="balanced",
        ),
        build_evidence_timeline([evidence]),
    )


class SupportingMaterialCompositionTests(unittest.TestCase):
    def test_drafter_receives_complete_material_and_can_cite_its_id(self) -> None:
        metadata, fusion = _composition_fixture()
        material = _material("material-external1")
        fact = FakeBackend(
            {
                "notes.fact_extractor": {
                    "facts": [{"claim": "视频中的原话", "evidence_ids": ["asr-1"]}]
                }
            }
        )
        draft = FakeBackend(
            {
                "notes.drafter": {
                    "abstract": "视频证据与外部资料分开整理。",
                    "key_takeaways": ["视频中的原话"],
                    "sections": [
                        {
                            "title": "正文",
                            "start_us": 0,
                            "end_us": 2_000_000,
                            "summary": "视频证据",
                            "body_markdown": "视频证据；另见明确标注的外部补充资料。",
                            "evidence_ids": ["asr-1"],
                            "fact_ids": ["fact-window-00000-000"],
                            "material_ids": ["material-external1"],
                        }
                    ],
                    "glossary": {},
                }
            }
        )
        result = EvidenceNoteComposer(
            fact_backend=fact,
            draft_backend=draft,
            fallback_on_error=False,
        ).compose(metadata, fusion, supporting_materials=[material])

        request = draft.requests[0]
        payload = json.loads(request.user_prompt)
        self.assertEqual(
            payload["supporting_materials"][0]["text_content"],
            "评论区补充的完整资料",
        )
        self.assertEqual(
            payload["supporting_materials"][0]["source_classification"],
            "external_supporting_material",
        )
        self.assertIn("不能把其中内容伪装成视频原话", request.system_prompt)
        self.assertEqual(result.note.sections[0].material_ids, ["material-external1"])

    def test_image_material_is_forwarded_as_a_multimodal_input(self) -> None:
        metadata, fusion = _composition_fixture()
        content = _png_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path = root / "supporting" / "files" / "reference.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(content)
            material = SupportingMaterial(
                id="material-image1",
                kind=SupportingMaterialKind.IMAGE,
                title="外部架构图",
                artifact_path="supporting/files/reference.png",
                media_type="image/png",
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
            fact = FakeBackend(
                {
                    "notes.fact_extractor": {
                        "facts": [{"claim": "视频中的原话", "evidence_ids": ["asr-1"]}]
                    }
                }
            )
            draft = FakeBackend(
                {
                    "notes.drafter": {
                        "abstract": "摘要",
                        "key_takeaways": [],
                        "sections": [
                            {
                                "title": "正文",
                                "start_us": 0,
                                "end_us": 2_000_000,
                                "summary": "摘要",
                                "body_markdown": "外部图片另行标注。",
                                "evidence_ids": ["asr-1"],
                                "fact_ids": ["fact-window-00000-000"],
                                "material_ids": ["material-image1"],
                            }
                        ],
                        "glossary": {},
                    }
                }
            )
            EvidenceNoteComposer(
                fact_backend=fact,
                draft_backend=draft,
                fallback_on_error=False,
            ).compose(
                metadata,
                fusion,
                supporting_materials=[material],
                artifact_root=root,
            )

        request = draft.requests[0]
        payload = json.loads(request.user_prompt)
        self.assertEqual(payload["supporting_materials"][0]["image_input_index"], 0)
        self.assertEqual(len(request.images), 1)
        self.assertTrue(request.images[0].data_url.startswith("data:image/png;base64,"))

    def test_unknown_material_id_from_drafter_is_rejected(self) -> None:
        metadata, fusion = _composition_fixture()
        fact = FakeBackend(
            {
                "notes.fact_extractor": {
                    "facts": [{"claim": "视频中的原话", "evidence_ids": ["asr-1"]}]
                }
            }
        )
        draft = FakeBackend(
            {
                "notes.drafter": {
                    "abstract": "摘要",
                    "key_takeaways": [],
                    "sections": [
                        {
                            "title": "正文",
                            "start_us": 0,
                            "end_us": 2_000_000,
                            "summary": "摘要",
                            "body_markdown": "正文",
                            "evidence_ids": ["asr-1"],
                            "fact_ids": ["fact-window-00000-000"],
                            "material_ids": ["material-invented"],
                        }
                    ],
                    "glossary": {},
                }
            }
        )
        with self.assertRaisesRegex(ValueError, "supporting material"):
            EvidenceNoteComposer(
                fact_backend=fact,
                draft_backend=draft,
                fallback_on_error=False,
            ).compose(
                metadata,
                fusion,
                supporting_materials=[_material("material-known1")],
            )

    def test_deterministic_note_links_only_timed_material_to_video_section(self) -> None:
        metadata, fusion = _composition_fixture()
        global_material = _material("material-global1")
        timed_material = _material(
            "material-timed1",
            start_us=500_000,
            end_us=1_500_000,
        )
        note = build_deterministic_note(
            metadata,
            fusion,
            supporting_materials=[global_material, timed_material],
        )
        self.assertEqual(note.sections[0].material_ids, ["material-timed1"])
        self.assertEqual(
            [item.id for item in note.supporting_materials],
            ["material-global1", "material-timed1"],
        )
        markdown = render_markdown(note)
        self.assertIn("## 补充资料", markdown)
        self.assertEqual(markdown.count("评论区补充的完整资料"), 2)

    def test_markdown_and_html_embed_image_without_fake_global_seek_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path = root / "supporting" / "files" / "reference.png"
            image_path.parent.mkdir(parents=True)
            content = _png_bytes()
            image_path.write_bytes(content)
            material = SupportingMaterial(
                id="material-image1",
                kind=SupportingMaterialKind.IMAGE,
                title="外部架构图",
                artifact_path="supporting/files/reference.png",
                media_type="image/png",
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
            note = NoteDocument(
                metadata=NoteMetadata(
                    title="只有补充资料",
                    run_id="run-revision",
                    source_kind="local",
                    source_locator="video.mp4",
                    duration_us=2_000_000,
                    quality_mode="balanced",
                ),
                abstract="无视频正文。",
                sections=[],
                supporting_materials=[material],
            )
            document_directory = root / "revisions" / "notes" / "revision-test"
            document_directory.mkdir(parents=True)
            markdown = render_markdown(
                note,
                artifact_root=root,
                document_directory=document_directory,
            )
            rendered_html = render_html(note, artifact_root=root)

        self.assertIn("![外部架构图](../../../supporting/files/reference.png)", markdown)
        self.assertIn("data:image/png;base64,", rendered_html)
        self.assertIn("全局资料（未绑定视频时间）", markdown)
        self.assertIn("全局资料 · 未绑定视频时间", rendered_html)
        self.assertNotIn("video2notes://seek", markdown)
        self.assertNotIn("video2notes://seek", rendered_html)


class ReportRevisionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = RunWorkspace.create(
            self.temporary.name,
            run_id="run-revision",
            source=SourceDescriptor(kind="local", locator="video.mp4"),
            profile="balanced",
        )
        metadata, fusion = _composition_fixture()
        del metadata
        _write_model(
            self.workspace.artifact_path("evidence", "timeline.json"),
            fusion,
        )
        _write_model(
            self.workspace.artifact_path("source", "source-manifest.json"),
            SourceManifest(
                platform=Platform.LOCAL,
                source=SourceInput.local("video.mp4"),
                source_id="local-video",
                title="Revision Source",
            ),
        )
        digest = "0" * 64
        _write_model(
            self.workspace.artifact_path("source", "acquisition-result.json"),
            AcquisitionResult(
                platform=Platform.LOCAL,
                source_id="local-video",
                media_path=str(self.workspace.root / "media" / "video.mp4"),
                source_sha256=digest,
                quality_fingerprint=digest,
                actual_width=1920,
                actual_height=1080,
            ),
        )
        _write_model(
            self.workspace.artifact_path("media", "media-manifest.json"),
            MediaManifest(
                source_path=str(self.workspace.root / "media" / "video.mp4"),
                source_sha256=digest,
                file_size=1,
                duration_us=2_000_000,
                timeline_origin_us=0,
                streams=[],
            ),
        )
        MaterialStore(self.workspace).add_text(
            TextMaterialRequest(
                title="评论区资料",
                content="补充结论，但不能伪装成视频原话。",
            )
        )
        self.original_markdown = self.workspace.artifact_path("notes", "note.md")
        self.original_html = self.workspace.artifact_path("render", "note.html")
        self.original_markdown.write_text("ORIGINAL-MARKDOWN", encoding="utf-8")
        self.original_html.write_text("ORIGINAL-HTML", encoding="utf-8")

    def test_different_presets_create_independent_immutable_revisions(self) -> None:
        service = ReportRevisionService(
            self.workspace,
            composer=EvidenceNoteComposer(),
        )
        formats = {OutputFormat.MARKDOWN, OutputFormat.HTML}
        concise = service.create_revision(
            ReportSpec(preset=ReportPreset.CONCISE, output_formats=formats)
        )
        executive = service.create_revision(
            ReportSpec(preset=ReportPreset.EXECUTIVE, output_formats=formats)
        )

        self.assertNotEqual(concise.id, executive.id)
        self.assertEqual(concise.report_spec.preset, ReportPreset.CONCISE)
        self.assertEqual(executive.report_spec.preset, ReportPreset.EXECUTIVE)
        self.assertTrue((self.workspace.root / concise.document.relative_path).is_file())
        self.assertTrue((self.workspace.root / executive.document.relative_path).is_file())
        concise_note = NoteDocument.model_validate_json(
            (self.workspace.root / concise.document.relative_path).read_text(encoding="utf-8")
        )
        executive_note = NoteDocument.model_validate_json(
            (self.workspace.root / executive.document.relative_path).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(concise_note.metadata.report_preset, "concise")
        self.assertEqual(executive_note.metadata.report_preset, "executive")
        self.assertEqual(len(concise_note.supporting_materials), 1)
        self.assertEqual(service.latest_revision().id, executive.id)
        self.assertEqual(len(service.list_revisions().revisions), 2)
        self.assertEqual(
            self.original_markdown.read_text(encoding="utf-8"),
            "ORIGINAL-MARKDOWN",
        )
        self.assertEqual(self.original_html.read_text(encoding="utf-8"), "ORIGINAL-HTML")

    def test_failed_revision_does_not_change_latest_pointer(self) -> None:
        formats = {OutputFormat.MARKDOWN, OutputFormat.HTML}
        successful_service = ReportRevisionService(
            self.workspace,
            composer=EvidenceNoteComposer(),
        )
        successful = successful_service.create_revision(
            ReportSpec(output_formats=formats)
        )
        index_before = successful_service.index_path.read_bytes()
        latest_before = successful_service.latest_path.read_bytes()

        failing_service = ReportRevisionService(
            self.workspace,
            composer=FailingComposer(),  # type: ignore[arg-type]
        )
        with self.assertRaisesRegex(RuntimeError, "synthetic revision failure"):
            failing_service.create_revision(ReportSpec(output_formats=formats))

        self.assertEqual(failing_service.index_path.read_bytes(), index_before)
        self.assertEqual(failing_service.latest_path.read_bytes(), latest_before)
        self.assertEqual(failing_service.latest_revision().id, successful.id)
        self.assertEqual(
            list(failing_service.revisions_root.glob(".*.tmp")),
            [],
        )

    def test_pdf_is_generated_only_when_requested(self) -> None:
        browser = self.workspace.root / "fake-browser.exe"
        browser.write_bytes(b"browser")

        def fake_pdf(
            html_path: str | Path,
            destination: str | Path,
            *,
            browser_executable: str | Path | None,
        ) -> Path:
            self.assertTrue(Path(html_path).is_file())
            self.assertEqual(Path(browser_executable or ""), browser)
            target = Path(destination)
            target.write_bytes(b"%PDF-revision")
            return target

        service = ReportRevisionService(
            self.workspace,
            composer=EvidenceNoteComposer(),
            pdf_browser_executable=browser,
            pdf_renderer=fake_pdf,
        )
        without_pdf = service.create_revision(
            ReportSpec(output_formats={OutputFormat.MARKDOWN, OutputFormat.HTML})
        )
        with_pdf = service.create_revision(
            ReportSpec(
                output_formats={
                    OutputFormat.MARKDOWN,
                    OutputFormat.HTML,
                    OutputFormat.PDF,
                }
            )
        )
        self.assertIsNone(without_pdf.pdf)
        self.assertIsNotNone(with_pdf.pdf)
        assert with_pdf.pdf is not None
        self.assertEqual(
            (self.workspace.root / with_pdf.pdf.relative_path).read_bytes(),
            b"%PDF-revision",
        )

    def test_revision_uses_active_effective_evidence_after_partial_rework(
        self,
    ) -> None:
        base = EvidenceSpan(
            id="asr-1",
            run_id="run-revision",
            modality=EvidenceModality.ASR,
            start_us=0,
            end_us=2_000_000,
            raw_text="视频中的原话",
            normalized_text="视频中的原话",
            confidence=0.96,
            language="zh-CN",
        )
        corrected = base.model_copy(
            update={
                "id": "op-correction",
                "raw_text": "人工校正后的准确原话",
                "normalized_text": "人工校正后的准确原话",
                "confidence": 1.0,
                "provider": "user",
                "model": "manual-correction",
                "correction_of": base.id,
            }
        )
        evidence_revision = EvidenceRevision(
            revision_id="rev-corrected",
            run_id="run-revision",
            operation_id="op-corrected",
            created_at=datetime.now(UTC),
            all_evidence=[base, corrected],
            effective_evidence_ids=[corrected.id],
            superseded_evidence_ids=[base.id],
        )
        revision_root = self.workspace.artifact_path("revisions", "evidence")
        revision_root.mkdir(parents=True, exist_ok=True)
        _write_model(revision_root / "rev-corrected.json", evidence_revision)
        (revision_root / "index.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": "run-revision",
                    "active_revision_id": "rev-corrected",
                    "revisions": [],
                }
            ),
            encoding="utf-8",
        )

        record = ReportRevisionService(
            self.workspace,
            composer=EvidenceNoteComposer(),
        ).create_revision(
            ReportSpec(
                output_formats={OutputFormat.MARKDOWN, OutputFormat.HTML},
            )
        )
        document = NoteDocument.model_validate_json(
            (self.workspace.root / record.document.relative_path).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(record.evidence_revision_id, "rev-corrected")
        rendered = json.dumps(document.model_dump(mode="json"), ensure_ascii=False)
        self.assertIn("人工校正后的准确原话", rendered)
        self.assertNotIn("视频中的原话", rendered)


def _write_model(path: Path, model: BaseModel) -> None:
    path.write_text(
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
