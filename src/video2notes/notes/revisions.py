"""Immutable report revisions composed from evidence and active external materials."""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from video2notes.artifacts import RunWorkspace
from video2notes.domain import ArtifactKind, ArtifactRef, MediaManifest
from video2notes.fusion import FusionResult, build_evidence_timeline
from video2notes.materials import MaterialStore, RunMaterial
from video2notes.operations import OperationService
from video2notes.sources import AcquisitionResult, SourceManifest

from .composer import EvidenceNoteComposer, InvocationSummary
from .models import NoteDocument, NoteMetadata, NoteScreenshot
from .render import render_pdf_from_html, write_html, write_markdown
from .reporting import OutputFormat, ReportSpec, ResolvedReportSpec

PdfRenderer = Callable[..., Path]

_LOCKS_GUARD = threading.Lock()
_WORKSPACE_LOCKS: dict[Path, threading.RLock] = {}


class RevisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ReportRevisionRecord(RevisionModel):
    id: str = Field(pattern=r"^revision-[A-Za-z0-9-]+$")
    run_id: str = Field(min_length=1)
    created_at: datetime
    report_spec: ResolvedReportSpec
    evidence_revision_id: str | None = None
    material_ids: list[str] = Field(default_factory=list)
    document: ArtifactRef
    markdown: ArtifactRef
    html: ArtifactRef
    pdf: ArtifactRef | None = None
    invocations: list[InvocationSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    used_deterministic_fallback: bool = False


class ReportRevisionIndex(RevisionModel):
    schema_version: int = 1
    run_id: str
    latest_revision_id: str | None = None
    revisions: list[ReportRevisionRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> ReportRevisionIndex:
        identifiers = [item.id for item in self.revisions]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("report revision IDs must be unique")
        if any(item.run_id != self.run_id for item in self.revisions):
            raise ValueError("all report revisions must belong to the index run")
        if (
            self.latest_revision_id is not None
            and self.latest_revision_id not in set(identifiers)
        ):
            raise ValueError("latest report revision must exist in the index")
        return self


class LatestReportRevision(RevisionModel):
    schema_version: int = 1
    run_id: str
    revision_id: str
    record_path: str
    activated_at: datetime


class ReportRevisionService:
    """Re-compose a completed run without mutating its original note artifacts."""

    def __init__(
        self,
        workspace: RunWorkspace,
        *,
        composer: EvidenceNoteComposer,
        pdf_browser_executable: str | Path | None = None,
        pdf_renderer: PdfRenderer = render_pdf_from_html,
    ):
        self.workspace = workspace
        self.composer = composer
        self.pdf_browser_executable = pdf_browser_executable
        self.pdf_renderer = pdf_renderer
        self.revisions_root = workspace.artifact_path("revisions", "notes")
        self.revisions_root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.revisions_root / "index.json"
        self.latest_path = self.revisions_root / "latest.json"
        self._lock = _workspace_lock(workspace.root)

    def create_revision(
        self,
        report_spec: ReportSpec | None = None,
    ) -> ReportRevisionRecord:
        """Create and activate one complete immutable report revision."""

        spec = report_spec or ReportSpec()
        resolved = spec.resolve()
        with self._lock:
            (
                fusion,
                evidence_revision_id,
                metadata,
                materials,
                screenshots,
            ) = self._load_composition_inputs(
                include_screenshots=resolved.include_screenshots
            )
            revision_id = _new_revision_id()
            final_directory = self.revisions_root / revision_id
            if final_directory.exists():
                raise FileExistsError(f"report revision already exists: {revision_id}")
            staging_directory = self.revisions_root / (
                f".{revision_id}.{uuid.uuid4().hex}.tmp"
            )
            staging_directory.mkdir(parents=False, exist_ok=False)
            try:
                composition = self.composer.compose(
                    metadata,
                    fusion,
                    screenshots_by_window=screenshots,
                    report_spec=spec,
                    supporting_materials=materials,
                    artifact_root=self.workspace.root,
                )
                document_path = staging_directory / "document.json"
                markdown_path = staging_directory / "note.md"
                html_path = staging_directory / "note.html"
                pdf_path = staging_directory / "note.pdf"
                _atomic_write_json(
                    document_path,
                    composition.note.model_dump(mode="json"),
                )
                write_markdown(
                    composition.note,
                    markdown_path,
                    artifact_root=self.workspace.root,
                )
                write_html(
                    composition.note,
                    html_path,
                    artifact_root=self.workspace.root,
                )
                if OutputFormat.PDF in resolved.output_formats:
                    self.pdf_renderer(
                        html_path,
                        pdf_path,
                        browser_executable=self.pdf_browser_executable,
                    )
                    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
                        raise RuntimeError("PDF renderer did not create a nonempty revision")

                os.replace(staging_directory, final_directory)
                record = self._record_for_published_revision(
                    revision_id=revision_id,
                    directory=final_directory,
                    report_spec=resolved,
                    evidence_revision_id=evidence_revision_id,
                    material_ids=[item.id for item in materials],
                    invocations=composition.invocations,
                    warnings=composition.warnings,
                    used_deterministic_fallback=composition.used_deterministic_fallback,
                    include_pdf=OutputFormat.PDF in resolved.output_formats,
                )
                self._activate(record)
                return record
            except Exception:
                _remove_staging_directory(
                    staging_directory,
                    revisions_root=self.revisions_root,
                )
                raise

    def list_revisions(self) -> ReportRevisionIndex:
        with self._lock:
            return self._load_index()

    def latest_revision(self) -> ReportRevisionRecord | None:
        index = self.list_revisions()
        if index.latest_revision_id is None:
            return None
        return next(
            item for item in index.revisions if item.id == index.latest_revision_id
        )

    def get_revision(self, revision_id: str) -> ReportRevisionRecord:
        index = self.list_revisions()
        for item in index.revisions:
            if item.id == revision_id:
                return item
        raise KeyError(revision_id)

    def _load_composition_inputs(
        self,
        *,
        include_screenshots: bool,
    ) -> tuple[
        FusionResult,
        str | None,
        NoteMetadata,
        list[RunMaterial],
        Mapping[str, list[NoteScreenshot]],
    ]:
        fusion = _read_model(
            self.workspace.root / "evidence" / "timeline.json",
            FusionResult,
        )
        source = _read_model(
            self.workspace.root / "source" / "source-manifest.json",
            SourceManifest,
        )
        acquisition = _read_model(
            self.workspace.root / "source" / "acquisition-result.json",
            AcquisitionResult,
        )
        media = _read_model(
            self.workspace.root / "media" / "media-manifest.json",
            MediaManifest,
        )
        if fusion.run_id != self.workspace.manifest.run_id:
            raise ValueError("evidence timeline belongs to a different run")
        fusion, evidence_revision_id = self._effective_fusion(fusion)

        materials = MaterialStore(self.workspace).list()
        for material in materials:
            if material.run_id != self.workspace.manifest.run_id:
                raise ValueError("supporting material belongs to a different run")
            if not self.workspace.verify_ref(material.artifact):
                raise ValueError(f"supporting material artifact is missing: {material.id}")

        original_note = _read_optional_model(
            self.workspace.root / "notes" / "document.json",
            NoteDocument,
        )
        if (
            original_note is not None
            and original_note.metadata.run_id != self.workspace.manifest.run_id
        ):
            raise ValueError("original note document belongs to a different run")

        languages = sorted(
            {
                item.language
                for item in fusion.evidence
                if item.language is not None and item.language.strip()
            }
        )
        warnings = [
            item
            for item in (
                source.quality_warning,
                *acquisition.warnings,
                *self.workspace.manifest.warnings,
            )
            if item
        ]
        resolution = (
            f"{acquisition.actual_width}x{acquisition.actual_height}"
            if acquisition.actual_width is not None
            and acquisition.actual_height is not None
            else None
        )
        title = (
            original_note.metadata.title
            if original_note is not None
            else source.title or Path(media.source_path).stem
        )
        metadata = NoteMetadata(
            title=title,
            run_id=self.workspace.manifest.run_id,
            source_kind=source.platform.value,
            source_locator=source.source.value,
            source_url=source.canonical_url,
            author=source.author,
            duration_us=media.duration_us,
            languages=languages,
            quality_mode=self.workspace.manifest.profile,
            source_resolution=(
                resolution
                or (
                    original_note.metadata.source_resolution
                    if original_note is not None
                    else None
                )
            ),
            quality_warnings=list(dict.fromkeys(warnings)),
        )
        screenshots = (
            _screenshots_by_window(original_note, fusion)
            if include_screenshots and original_note is not None
            else {}
        )
        return fusion, evidence_revision_id, metadata, materials, screenshots

    def _effective_fusion(
        self,
        base_fusion: FusionResult,
    ) -> tuple[FusionResult, str | None]:
        """Re-fuse the active partial-rework evidence when one is activated."""

        index_path = self.workspace.root / "revisions" / "evidence" / "index.json"
        if not index_path.is_file():
            return base_fusion, None
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("evidence revision index is invalid") from error
        if not isinstance(payload, dict):
            raise ValueError("evidence revision index is invalid")
        active_revision_id = payload.get("active_revision_id")
        if active_revision_id is None:
            return base_fusion, None
        if not isinstance(active_revision_id, str) or not active_revision_id:
            raise ValueError("evidence revision index is invalid")
        evidence_view = OperationService(self.workspace).get_evidence()
        if evidence_view.revision_id != active_revision_id:
            raise ValueError("active evidence revision does not match its index")
        return (
            build_evidence_timeline(
                evidence_view.evidence,
                base_fusion.visual_states,
            ),
            active_revision_id,
        )

    def _record_for_published_revision(
        self,
        *,
        revision_id: str,
        directory: Path,
        report_spec: ResolvedReportSpec,
        evidence_revision_id: str | None,
        material_ids: list[str],
        invocations: list[InvocationSummary],
        warnings: list[str],
        used_deterministic_fallback: bool,
        include_pdf: bool,
    ) -> ReportRevisionRecord:
        document_path = directory / "document.json"
        markdown_path = directory / "note.md"
        html_path = directory / "note.html"
        pdf_path = directory / "note.pdf"
        return ReportRevisionRecord(
            id=revision_id,
            run_id=self.workspace.manifest.run_id,
            created_at=datetime.now(UTC),
            report_spec=report_spec,
            evidence_revision_id=evidence_revision_id,
            material_ids=material_ids,
            document=self.workspace.ref_for(document_path, kind=ArtifactKind.NOTE),
            markdown=self.workspace.ref_for(markdown_path, kind=ArtifactKind.NOTE),
            html=self.workspace.ref_for(html_path, kind=ArtifactKind.RENDER),
            pdf=(
                self.workspace.ref_for(pdf_path, kind=ArtifactKind.RENDER)
                if include_pdf
                else None
            ),
            invocations=invocations,
            warnings=warnings,
            used_deterministic_fallback=used_deterministic_fallback,
        )

    def _load_index(self) -> ReportRevisionIndex:
        if not self.index_path.is_file():
            return ReportRevisionIndex(run_id=self.workspace.manifest.run_id)
        index = ReportRevisionIndex.model_validate_json(
            self.index_path.read_text(encoding="utf-8")
        )
        if index.run_id != self.workspace.manifest.run_id:
            raise ValueError("report revision index belongs to a different run")
        return index

    def _activate(self, record: ReportRevisionRecord) -> None:
        index = self._load_index()
        next_index = ReportRevisionIndex(
            run_id=index.run_id,
            latest_revision_id=record.id,
            revisions=[*index.revisions, record],
        )
        activated_at = datetime.now(UTC)
        latest = LatestReportRevision(
            run_id=self.workspace.manifest.run_id,
            revision_id=record.id,
            record_path="revisions/notes/index.json",
            activated_at=activated_at,
        )
        previous_latest = self.latest_path.read_bytes() if self.latest_path.is_file() else None
        _atomic_write_json(self.latest_path, latest.model_dump(mode="json"))
        try:
            # index.json is authoritative; latest.json is a small convenience pointer.
            _atomic_write_json(self.index_path, next_index.model_dump(mode="json"))
        except Exception:
            _restore_file(self.latest_path, previous_latest)
            raise


def _screenshots_by_window(
    note: NoteDocument,
    fusion: FusionResult,
) -> dict[str, list[NoteScreenshot]]:
    screenshots = [
        screenshot
        for section in note.sections
        for screenshot in section.screenshots
    ]
    result: dict[str, list[NoteScreenshot]] = {}
    for screenshot in screenshots:
        for index, window in enumerate(fusion.windows):
            is_last = index == len(fusion.windows) - 1
            if window.start_us <= screenshot.timestamp_us < window.end_us or (
                is_last and screenshot.timestamp_us == window.end_us
            ):
                result.setdefault(window.id, []).append(screenshot)
                break
    return result


def _workspace_lock(root: Path) -> threading.RLock:
    resolved = root.resolve()
    with _LOCKS_GUARD:
        return _WORKSPACE_LOCKS.setdefault(resolved, threading.RLock())


def _new_revision_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"revision-{timestamp}-{uuid.uuid4().hex[:10]}"


def _read_model(path: Path, model: type[BaseModel]) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"required report input does not exist: {path}")
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _read_optional_model(path: Path, model: type[BaseModel]) -> Any | None:
    if not path.is_file():
        return None
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _restore_file(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rollback")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _remove_staging_directory(path: Path, *, revisions_root: Path) -> None:
    resolved = path.resolve()
    root = revisions_root.resolve()
    if (
        resolved.parent == root
        and resolved.name.startswith(".revision-")
        and resolved.name.endswith(".tmp")
        and resolved.is_dir()
    ):
        shutil.rmtree(resolved)
