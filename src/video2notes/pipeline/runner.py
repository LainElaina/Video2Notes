"""Acquisition-to-note pipeline with a hash-verified artifact at every stage."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from video2notes.artifacts import RunWorkspace
from video2notes.audio import (
    ASRBackend,
    ASREvidenceResult,
    AudioExtractionError,
    AudioExtractionResult,
    FasterWhisperBackend,
    SecondaryASRDecision,
    SecondaryASRReason,
    SubtitleParseError,
    build_secondary_asr_decisions,
    extract_audio,
    extract_audio_window,
    parse_subtitle_file,
    transcribe_to_evidence,
)
from video2notes.domain import (
    ArtifactKind,
    ArtifactRef,
    EvidenceModality,
    EvidenceSpan,
    MediaManifest,
    ProcessingScope,
    RunStatus,
    SourceDescriptor,
    VisualState,
)
from video2notes.fusion import FusionResult, build_evidence_timeline
from video2notes.media import iter_video_frames
from video2notes.notes import (
    EvidenceNoteComposer,
    NoteCompositionResult,
    NoteDocument,
    NoteMetadata,
    NoteScreenshot,
    OutputFormat,
    ReportSpec,
    contains_sensitive_note_text,
    render_pdf_from_html,
    write_html,
    write_markdown,
)
from video2notes.ocr import (
    FilesystemArtifactImageLoader,
    OcrBackend,
    OcrEvidenceBundle,
    OcrPipelineConfig,
    PaddleOcrBackend,
    extract_ocr_evidence,
)
from video2notes.sources import (
    AcquisitionCancelled,
    AcquisitionPolicy,
    AcquisitionResult,
    AuthSpec,
    CancellationToken,
    ProgressEvent,
    SourceInput,
    SourceManifest,
    SourceRegistry,
)
from video2notes.system import (
    AccelerationCapabilities,
    ExecutionPlan,
    ExperienceMode,
    HardwareSnapshot,
    PerformanceOverrides,
    QualityMode,
    ResourcePreference,
    ResourceReserve,
    SecondaryAsrPolicy,
    align_execution_plan_with_acceleration,
    build_execution_plan,
    detect_acceleration_capabilities,
    detect_hardware,
)
from video2notes.vision import (
    AdaptiveScanConfig,
    AdaptiveVideoScanner,
    ChangeEvent,
    SamplingMode,
    SamplingPlan,
    SamplingSegment,
    merge_change_events,
)


class PipelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PipelineRequest(PipelineModel):
    source: SourceInput
    auth: AuthSpec = Field(default_factory=AuthSpec)
    acquisition: AcquisitionPolicy = Field(default_factory=AcquisitionPolicy)
    quality_mode: QualityMode = QualityMode.BALANCED
    processing_scope: ProcessingScope = ProcessingScope.AUDIO_VISUAL
    title_override: str | None = None
    language_hints: list[str] = Field(default_factory=list)
    sampling_plan: SamplingPlan = Field(default_factory=SamplingPlan)
    include_screenshots: bool = True
    generate_pdf: bool = True
    report_spec: ReportSpec | None = None

    def effective_report_spec(self) -> ReportSpec:
        """Resolve legacy booleans into the new report contract.

        Explicit report_spec values take precedence except that audio-only
        processing always disables screenshots because no visual analysis is
        permitted. This preserves existing CLI/API requests while allowing the
        desktop UI to select an audience, detail budget, screenshots, and
        output formats as one coherent policy.
        """

        if self.report_spec is not None:
            if self.processing_scope is ProcessingScope.AUDIO_ONLY:
                return self.report_spec.model_copy(update={"include_screenshots": False})
            return self.report_spec
        output_formats = {OutputFormat.MARKDOWN, OutputFormat.HTML}
        if self.generate_pdf:
            output_formats.add(OutputFormat.PDF)
        return ReportSpec(
            include_screenshots=(
                self.include_screenshots and self.processing_scope is ProcessingScope.AUDIO_VISUAL
            ),
            output_formats=output_formats,
        )


class PipelineOutcome(PipelineModel):
    run_id: str
    processing_scope: ProcessingScope = ProcessingScope.AUDIO_VISUAL
    markdown: ArtifactRef
    html: ArtifactRef
    pdf: ArtifactRef | None = None
    note_document: ArtifactRef
    evidence_count: int = Field(ge=0)
    visual_state_count: int = Field(ge=0)
    used_deterministic_note_fallback: bool


class PipelineEmitter(Protocol):
    def __call__(
        self,
        stage: str,
        *,
        progress: float | None = None,
        message: str | None = None,
        metrics: dict[str, float | int | str | bool | None] | None = None,
    ) -> None: ...


def _noop_emit(
    stage: str,
    *,
    progress: float | None = None,
    message: str | None = None,
    metrics: dict[str, float | int | str | bool | None] | None = None,
) -> None:
    del stage, progress, message, metrics


@dataclass
class PipelineRuntime:
    source_registry: SourceRegistry
    note_composer: EvidenceNoteComposer
    asr_backend: ASRBackend | None = None
    secondary_asr_backend: ASRBackend | None = None
    ocr_backend: OcrBackend | None = None
    asr_backends_by_quality: Mapping[QualityMode, ASRBackend] = field(default_factory=dict)
    secondary_asr_backends_by_quality: Mapping[QualityMode, ASRBackend] = field(
        default_factory=dict
    )
    ocr_backends_by_quality: Mapping[QualityMode, OcrBackend] = field(default_factory=dict)
    hardware: HardwareSnapshot | None = None
    hardware_disk_path: str | Path | None = None
    experience_mode: ExperienceMode = ExperienceMode.GUIDED
    resource_preference: ResourcePreference = ResourcePreference.BALANCED
    resource_reserve: ResourceReserve | None = None
    performance_overrides: PerformanceOverrides | None = None
    acceleration_capabilities: AccelerationCapabilities | None = None
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    pdf_browser_executable: str | Path | None = None

    @classmethod
    def local_defaults(cls) -> PipelineRuntime:
        return cls(
            source_registry=SourceRegistry.default(),
            note_composer=EvidenceNoteComposer(),
        )


class Video2NotesPipeline:
    STAGE_VERSIONS: Mapping[str, str] = {
        "source.acquire": "2",
        "media.probe": "2",
        "system.plan": "4",
        "vision.scan": "5",
        "audio.extract": "2",
        "captions.parse": "2",
        "audio.asr": "5",
        "ocr.extract": "4",
        "evidence.fuse": "3",
        "notes.compose": "11",
        "render.outputs": "8",
    }

    def __init__(
        self,
        runs_root: str | Path,
        *,
        runtime: PipelineRuntime | None = None,
    ):
        self.runs_root = Path(runs_root).expanduser().resolve()
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.runtime = runtime or PipelineRuntime.local_defaults()

    def create_run(
        self,
        request: PipelineRequest,
        *,
        run_id: str | None = None,
    ) -> RunWorkspace:
        return RunWorkspace.create(
            self.runs_root,
            run_id=run_id,
            source=SourceDescriptor(
                kind=request.source.kind.value,
                locator=request.source.value,
            ),
            profile=request.quality_mode.value,
            processing_scope=request.processing_scope,
        )

    def run(
        self,
        workspace: RunWorkspace,
        request: PipelineRequest,
        *,
        cancel: CancellationToken | None = None,
        emit: PipelineEmitter | None = None,
    ) -> PipelineOutcome:
        cancellation = cancel or CancellationToken()
        progress = emit or _noop_emit
        current_stage: str | None = None
        if workspace.manifest.processing_scope is not request.processing_scope:
            raise ValueError(
                "processing scope is immutable for an existing run; "
                "create a new run to change between audio-visual and audio-only"
            )
        workspace.set_status(RunStatus.RUNNING)
        if request.processing_scope is ProcessingScope.AUDIO_ONLY:
            workspace.add_warning(
                "Audio-only processing scope: visual scanning, OCR, and screenshots "
                "were intentionally skipped."
            )
        try:
            current_stage = "source.acquire"
            source_manifest, acquisition, media_ref, subtitle_refs = self._acquire(
                workspace,
                request,
                cancellation,
                progress,
            )
            current_stage = "media.probe"
            media, media_manifest_ref = self._probe_media(
                workspace,
                acquisition,
                media_ref,
                progress,
            )
            hardware_snapshot = self.runtime.hardware or detect_hardware(
                disk_path=self.runtime.hardware_disk_path
            )
            execution_plan = build_execution_plan(
                hardware_snapshot,
                request.quality_mode,
                experience_mode=self.runtime.experience_mode,
                preference=self.runtime.resource_preference,
                reserve=self.runtime.resource_reserve,
                overrides=self.runtime.performance_overrides,
            )
            acceleration = (
                self.runtime.acceleration_capabilities or detect_acceleration_capabilities()
            )
            execution_plan = align_execution_plan_with_acceleration(
                execution_plan,
                acceleration,
            )
            primary_asr_backend = _asr_backend_for_plan(
                self.runtime.asr_backends_by_quality.get(
                    request.quality_mode,
                    self.runtime.asr_backend,
                ),
                execution_plan,
            )
            secondary_asr_backend = _asr_backend_for_plan(
                self.runtime.secondary_asr_backends_by_quality.get(
                    request.quality_mode,
                    self.runtime.secondary_asr_backend,
                ),
                execution_plan,
            )
            ocr_backend = _ocr_backend_for_plan(
                self.runtime.ocr_backends_by_quality.get(
                    request.quality_mode,
                    self.runtime.ocr_backend,
                ),
                execution_plan,
            )
            execution_plan = _align_execution_plan_with_backends(
                execution_plan,
                primary_asr_backend=primary_asr_backend,
                ocr_backend=ocr_backend,
            )
            current_stage = "system.plan"
            self._record_execution_plan(
                workspace,
                request,
                media_manifest_ref,
                hardware_snapshot,
                acceleration,
                execution_plan,
                primary_asr_backend,
                secondary_asr_backend,
                ocr_backend,
                progress,
            )
            current_stage = "vision.scan"
            if request.processing_scope is ProcessingScope.AUDIO_ONLY:
                visual_states, visual_ref = self._skip_visual_states(
                    workspace,
                    media,
                    media_ref,
                    progress,
                )
            else:
                visual_states, visual_ref = self._scan_visual_states(
                    workspace,
                    media,
                    media_ref,
                    execution_plan.model_dump(mode="json"),
                    request.sampling_plan,
                    cancellation,
                    progress,
                )
            current_stage = "audio.extract"
            extraction, extraction_ref = self._extract_audio(
                workspace,
                media,
                media_ref,
                cancellation,
                progress,
            )
            current_stage = "captions.parse"
            captions, captions_ref = self._parse_captions(
                workspace,
                subtitle_refs,
                cancellation,
                progress,
            )
            current_stage = "audio.asr"
            asr_evidence, asr_ref = self._transcribe(
                workspace,
                extraction,
                extraction_ref,
                captions,
                captions_ref,
                request,
                primary_asr_backend,
                secondary_asr_backend,
                execution_plan.secondary_asr,
                cancellation,
                progress,
            )
            current_stage = "ocr.extract"
            if request.processing_scope is ProcessingScope.AUDIO_ONLY:
                ocr_bundle, ocr_evidence, ocr_ref = self._skip_ocr(
                    workspace,
                    visual_ref,
                    progress,
                )
            else:
                ocr_bundle, ocr_evidence, ocr_ref = self._extract_ocr(
                    workspace,
                    visual_states,
                    visual_ref,
                    request,
                    ocr_backend,
                    execution_plan,
                    cancellation,
                    progress,
                )
            current_stage = "evidence.fuse"
            fusion, fusion_ref = self._fuse(
                workspace,
                source_manifest,
                media,
                visual_states,
                [*captions, *asr_evidence, *ocr_evidence],
                [captions_ref, asr_ref, ocr_ref, visual_ref],
                request.processing_scope,
                cancellation,
                progress,
            )
            current_stage = "notes.compose"
            composition, note_ref = self._compose_note(
                workspace,
                request,
                source_manifest,
                acquisition,
                media,
                fusion,
                fusion_ref,
                ocr_bundle,
                execution_plan,
                cancellation,
                progress,
            )
            current_stage = "render.outputs"
            outcome = self._render(
                workspace,
                request,
                composition,
                note_ref,
                fusion,
                visual_states,
                cancellation,
                progress,
            )
        except AcquisitionCancelled:
            workspace.mark_cancelled(stage_name=current_stage)
            raise
        except Exception:
            if workspace.manifest.status is not RunStatus.FAILED:
                workspace.set_status(RunStatus.FAILED)
            raise
        workspace.set_status(RunStatus.COMPLETED)
        return outcome

    def _record_execution_plan(
        self,
        workspace: RunWorkspace,
        request: PipelineRequest,
        media_manifest_ref: ArtifactRef,
        hardware: HardwareSnapshot,
        acceleration: AccelerationCapabilities,
        execution_plan: ExecutionPlan,
        primary_asr_backend: ASRBackend | None,
        secondary_asr_backend: ASRBackend | None,
        ocr_backend: OcrBackend | None,
        emit: PipelineEmitter,
    ) -> ArtifactRef:
        output = workspace.artifact_path("system", "execution-plan.json")
        composer = _composer_identity(self.runtime.note_composer)
        audio_only = request.processing_scope is ProcessingScope.AUDIO_ONLY
        screenshot_export_enabled = request.effective_report_spec().resolve().include_screenshots
        degraded: list[str] = []
        if primary_asr_backend is None:
            degraded.append("primary_asr_unavailable")
        if not audio_only and ocr_backend is None:
            degraded.append("ocr_unavailable")
        if (
            execution_plan.secondary_asr is not SecondaryAsrPolicy.OFF
            and secondary_asr_backend is None
        ):
            degraded.append("secondary_asr_unavailable")
        if execution_plan.verification_passes > 0 and composer["verifier"] is None:
            degraded.append("note_verifier_unavailable")
        skipped_features = ["vision_scan", "ocr", "screenshots"] if audio_only else []
        effective_plan = execution_plan.model_dump(mode="json")
        effective_plan.update(
            {
                "processing_scope": request.processing_scope.value,
                "modality_controls": {
                    "source_acquisition_enabled": True,
                    "media_probe_enabled": True,
                    "audio_extraction_enabled": True,
                    "platform_captions_enabled": True,
                    "audio_analysis_enabled": True,
                    "visual_analysis_enabled": not audio_only,
                    "ocr_enabled": not audio_only,
                    "screenshot_export_enabled": screenshot_export_enabled,
                    "evidence_fusion_enabled": True,
                    "note_outputs_enabled": True,
                },
                "stage_execution": {
                    "source.acquire": {"status": "enabled", "reason": None},
                    "media.probe": {"status": "enabled", "reason": None},
                    "vision.scan": (
                        {"status": "skipped", "reason": "audio_only_scope"}
                        if audio_only
                        else {"status": "enabled", "reason": None}
                    ),
                    "audio.extract": {"status": "enabled", "reason": None},
                    "captions.parse": {"status": "enabled", "reason": None},
                    "audio.asr": {"status": "enabled", "reason": None},
                    "ocr.extract": (
                        {"status": "skipped", "reason": "audio_only_scope"}
                        if audio_only
                        else {"status": "enabled", "reason": None}
                    ),
                    "evidence.fuse": {"status": "enabled", "reason": None},
                    "notes.compose": {"status": "enabled", "reason": None},
                    "render.outputs": {"status": "enabled", "reason": None},
                },
            }
        )
        payload = {
            "schema_version": 2,
            "quality_mode": request.quality_mode.value,
            "processing_scope": request.processing_scope.value,
            "hardware": hardware.model_dump(mode="json"),
            "acceleration": acceleration.model_dump(mode="json"),
            "effective_plan": effective_plan,
            "actual_backends": {
                "asr_primary": _backend_identity(primary_asr_backend),
                "asr_secondary": _backend_identity(secondary_asr_backend),
                "ocr": None if audio_only else _backend_identity(ocr_backend),
                "ocr_configured_but_not_executed": (
                    _backend_identity(ocr_backend) if audio_only else None
                ),
                "notes": composer,
            },
            "degraded_features": degraded,
            "skipped_features": skipped_features,
        }
        with workspace.stage(
            "system.plan",
            stage_version=self.STAGE_VERSIONS["system.plan"],
            config=payload,
            inputs=[media_manifest_ref],
        ) as stage:
            if not stage.cached:
                emit("system.plan", progress=0.0, message="记录本次有效执行计划")
                _write_json(output, payload)
                stage.add_output(output, kind=ArtifactKind.SYSTEM)
                stage.add_metric("degraded_feature_count", len(degraded))
                stage.add_metric("skipped_feature_count", len(skipped_features))
                stage.add_metric("processing_scope", request.processing_scope.value)
                emit(
                    "system.plan",
                    progress=1.0,
                    message="有效执行计划已固定",
                    metrics={
                        "processing_scope": request.processing_scope.value,
                        "visual_analysis_enabled": not audio_only,
                        "ocr_enabled": not audio_only,
                        "screenshot_export_enabled": screenshot_export_enabled,
                    },
                )
        return workspace.ref_for(output, kind=ArtifactKind.SYSTEM)

    def _acquire(
        self,
        workspace: RunWorkspace,
        request: PipelineRequest,
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[SourceManifest, AcquisitionResult, ArtifactRef, list[ArtifactRef]]:
        manifest_path = workspace.artifact_path("source", "source-manifest.json")
        result_path = workspace.artifact_path("source", "acquisition-result.json")
        config = {
            "source": request.source.model_dump(mode="json"),
            "auth": request.auth.model_dump(mode="json"),
            "policy": request.acquisition.model_dump(mode="json"),
        }
        with workspace.stage(
            "source.acquire",
            stage_version=self.STAGE_VERSIONS["source.acquire"],
            config=config,
        ) as stage:
            if stage.cached:
                source_manifest = _read_model(manifest_path, SourceManifest)
                acquisition = _read_model(result_path, AcquisitionResult)
            else:
                cancel.raise_if_cancelled()
                emit("source.acquire", progress=0.0, message="正在探测来源与可用画质")
                adapter = self.runtime.source_registry.resolve(request.source)

                def on_source_progress(event: ProgressEvent) -> None:
                    cancel.raise_if_cancelled()
                    total = event.total_bytes or event.total_bytes_estimate
                    fraction = (
                        min(1.0, event.downloaded_bytes / total)
                        if total and event.downloaded_bytes is not None
                        else None
                    )
                    emit(
                        "source.acquire",
                        progress=fraction,
                        message=event.message or event.phase,
                        metrics={
                            "downloaded_bytes": event.downloaded_bytes,
                            "total_bytes": total,
                            "speed_bps": event.speed_bps,
                        },
                    )

                probed = asyncio.run(
                    adapter.probe(
                        request.source,
                        request.auth,
                        request.acquisition,
                        progress=on_source_progress,
                        cancel=cancel,
                    )
                )
                source_manifest = SourceManifest.model_validate(probed)
                acquired = asyncio.run(
                    adapter.acquire(
                        source_manifest,
                        request.acquisition,
                        workspace.artifact_path("media"),
                        request.auth,
                        progress=on_source_progress,
                        cancel=cancel,
                    )
                )
                acquisition = AcquisitionResult.model_validate(acquired)
                _write_model(manifest_path, source_manifest)
                _write_model(result_path, acquisition)
                stage.add_output(manifest_path, kind=ArtifactKind.SOURCE)
                stage.add_output(result_path, kind=ArtifactKind.SOURCE)
                stage.add_output(acquisition.media_path, kind=ArtifactKind.MEDIA)
                for subtitle_path in acquisition.subtitle_paths:
                    stage.add_output(subtitle_path, kind=ArtifactKind.SUBTITLE)
                for warning in [
                    source_manifest.quality_warning,
                    *acquisition.warnings,
                ]:
                    if warning:
                        stage.add_warning(warning)
                        workspace.add_warning(warning)
                emit("source.acquire", progress=1.0, message="来源已保存到任务目录")

        media_ref = workspace.ref_for(
            acquisition.media_path,
            kind=ArtifactKind.MEDIA,
        )
        subtitle_refs = [
            workspace.ref_for(item, kind=ArtifactKind.SUBTITLE)
            for item in acquisition.subtitle_paths
            if Path(item).is_file()
        ]
        return source_manifest, acquisition, media_ref, subtitle_refs

    def _probe_media(
        self,
        workspace: RunWorkspace,
        acquisition: AcquisitionResult,
        media_ref: ArtifactRef,
        emit: PipelineEmitter,
    ) -> tuple[MediaManifest, ArtifactRef]:
        from video2notes.media import probe_media

        output = workspace.artifact_path("media", "media-manifest.json")
        with workspace.stage(
            "media.probe",
            stage_version=self.STAGE_VERSIONS["media.probe"],
            config={"ffprobe": self.runtime.ffprobe_path},
            inputs=[media_ref],
        ) as stage:
            if stage.cached:
                manifest = _read_model(output, MediaManifest)
            else:
                emit("media.probe", progress=0.0, message="读取真实音视频时间基")
                manifest = probe_media(
                    acquisition.media_path,
                    ffprobe_path=self.runtime.ffprobe_path,
                )
                _write_model(output, manifest)
                stage.add_output(output, kind=ArtifactKind.MEDIA)
                stage.add_metric("duration_us", manifest.duration_us)
                stage.add_metric("timeline_origin_us", manifest.timeline_origin_us)
                emit("media.probe", progress=1.0, message="媒体时间轴已建立")
        return manifest, workspace.ref_for(output, kind=ArtifactKind.MEDIA)

    def _scan_visual_states(
        self,
        workspace: RunWorkspace,
        media: MediaManifest,
        media_ref: ArtifactRef,
        execution_plan: dict[str, Any],
        sampling_plan: SamplingPlan,
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[list[VisualState], ArtifactRef]:
        states_path = workspace.artifact_path("vision", "visual-states.json")
        events_path = workspace.artifact_path("vision", "scan-events.json")
        segments = sampling_plan.compile(
            media.duration_us,
            max_fixed_samples=int(execution_plan["max_fixed_samples"]),
        )
        analysis_width = int(execution_plan["analysis_width"])
        analysis_height = max(64, round(analysis_width * 9 / 16))
        if analysis_height % 2:
            analysis_height += 1
        config = AdaptiveScanConfig(
            coarse_fps=float(execution_plan["cheap_scan_fps"]),
            fine_fps=float(execution_plan["expensive_scan_fps"]),
            analysis_width=analysis_width,
            analysis_height=analysis_height,
            decode_threads=int(execution_plan["visual_decode_threads"]),
        )
        compiled_payload = [item.model_dump(mode="json") for item in segments]
        with workspace.stage(
            "vision.scan",
            stage_version=self.STAGE_VERSIONS["vision.scan"],
            config={
                "scanner": asdict(config),
                "sampling_plan": sampling_plan.model_dump(mode="json"),
                "sampling_segments": compiled_payload,
                "max_fixed_samples": int(execution_plan["max_fixed_samples"]),
                "ffprobe": self.runtime.ffprobe_path,
            },
            inputs=[media_ref],
        ) as stage:
            if stage.cached:
                states = _read_model_list(states_path, VisualState)
            else:
                cancel.raise_if_cancelled()
                emit("vision.scan", progress=0.0, message="按分段计划扫描视频画面")
                scanner = AdaptiveVideoScanner(
                    config,
                    ffmpeg_path=self.runtime.ffmpeg_path,
                    ffprobe_path=self.runtime.ffprobe_path,
                    cancel_check=cancel.raise_if_cancelled,
                )
                adaptive_probe = (
                    scanner.probe(media.source_path)
                    if any(item.sampling.mode is SamplingMode.ADAPTIVE for item in segments)
                    else None
                )
                discovered: list[ChangeEvent] = []
                raw_event_count = 0
                fixed_sample_count = 0
                for index, segment in enumerate(segments):
                    cancel.raise_if_cancelled()
                    segment_dir = workspace.artifact_path(
                        "vision",
                        "keyframes",
                        f"segment-{index:04d}",
                    )
                    if segment.sampling.mode is SamplingMode.ADAPTIVE:
                        result = scanner.scan_range(
                            media.source_path,
                            start_us=segment.start_us,
                            end_us=segment.end_us,
                            preview_dir=segment_dir,
                            probe=adaptive_probe,
                        )
                        segment_events = [
                            replace(
                                item,
                                sampling_mode="adaptive",
                                segment_start_us=segment.start_us,
                                segment_end_us=segment.end_us,
                            )
                            for item in result.events
                        ]
                    elif segment.sampling.mode is SamplingMode.FIXED_INTERVAL:
                        segment_events = _fixed_interval_events(
                            media,
                            segment,
                            preview_dir=segment_dir,
                            max_fixed_samples=int(execution_plan["max_fixed_samples"]),
                            decode_threads=int(execution_plan["visual_decode_threads"]),
                            cancel=cancel,
                        )
                        fixed_sample_count += len(segment_events)
                        max_fixed_samples = int(execution_plan["max_fixed_samples"])
                        if fixed_sample_count > max_fixed_samples:
                            raise ValueError(
                                "fixed_interval sampling exceeded runtime maximum "
                                f"of {max_fixed_samples} frames"
                            )
                    else:
                        segment_events = []
                    raw_event_count += len(segment_events)
                    discovered.extend(segment_events)
                    emit(
                        "vision.scan",
                        progress=(index + 1) / max(1, len(segments)),
                        message=(
                            f"完成采样分段 {index + 1}/{len(segments)}"
                            f"（{segment.sampling.mode.value}）"
                        ),
                    )

                events = merge_change_events(discovered)
                _write_json(
                    events_path,
                    {
                        "schema_version": 3,
                        "source": media.source_path,
                        "scanner": asdict(config),
                        "sampling_plan": sampling_plan.model_dump(mode="json"),
                        "segments": compiled_payload,
                        "events": [item.to_dict() for item in events],
                    },
                )
                stage.add_output(events_path, kind=ArtifactKind.VISUAL)
                states = _events_to_visual_states(
                    workspace,
                    events,
                    duration_us=media.duration_us,
                    run_id=workspace.manifest.run_id,
                    add_artifact=stage.add_output,
                )
                _write_json(
                    states_path,
                    [item.model_dump(mode="json") for item in states],
                )
                stage.add_output(states_path, kind=ArtifactKind.VISUAL)
                stage.add_metric("visual_state_count", len(states))
                stage.add_metric("sampling_segment_count", len(segments))
                stage.add_metric(
                    "adaptive_segment_count",
                    sum(item.sampling.mode is SamplingMode.ADAPTIVE for item in segments),
                )
                stage.add_metric(
                    "fixed_interval_segment_count",
                    sum(item.sampling.mode is SamplingMode.FIXED_INTERVAL for item in segments),
                )
                stage.add_metric(
                    "skip_segment_count",
                    sum(item.sampling.mode is SamplingMode.SKIP for item in segments),
                )
                stage.add_metric("fixed_interval_sample_count", fixed_sample_count)
                stage.add_metric(
                    "fixed_interval_requested_count",
                    sum(item.estimated_sample_count for item in segments),
                )
                stage.add_metric(
                    "deduplicated_visual_event_count",
                    raw_event_count - len(events),
                )
                emit(
                    "vision.scan",
                    progress=1.0,
                    message=f"发现 {len(states)} 个持久画面状态",
                )
        return states, workspace.ref_for(states_path, kind=ArtifactKind.VISUAL)

    def _skip_visual_states(
        self,
        workspace: RunWorkspace,
        media: MediaManifest,
        media_ref: ArtifactRef,
        emit: PipelineEmitter,
    ) -> tuple[list[VisualState], ArtifactRef]:
        """Persist an explicit, cacheable visual-stage abstention for audio-only runs."""

        states_path = workspace.artifact_path("vision", "visual-states.json")
        events_path = workspace.artifact_path("vision", "scan-events.json")
        skip_reason = "audio_only_scope"
        config = {
            "processing_scope": ProcessingScope.AUDIO_ONLY.value,
            "skipped": True,
            "skip_reason": skip_reason,
        }
        with workspace.stage(
            "vision.scan",
            stage_version=self.STAGE_VERSIONS["vision.scan"],
            config=config,
            inputs=[media_ref],
        ) as stage:
            if stage.cached:
                states = _read_model_list(states_path, VisualState)
            else:
                states = []
                _write_json(
                    events_path,
                    {
                        "schema_version": 3,
                        "source": media.source_path,
                        "duration_us": media.duration_us,
                        "processing_scope": ProcessingScope.AUDIO_ONLY.value,
                        "skipped": True,
                        "skip_reason": skip_reason,
                        "events": [],
                    },
                )
                _write_json(states_path, [])
                stage.add_output(events_path, kind=ArtifactKind.VISUAL)
                stage.add_output(states_path, kind=ArtifactKind.VISUAL)
                stage.add_warning(
                    "Visual scanning was intentionally skipped by the audio-only scope."
                )
                stage.add_metric("skipped", True)
                stage.add_metric("skip_reason", skip_reason)
                stage.add_metric("visual_state_count", 0)
                emit(
                    "vision.scan",
                    progress=1.0,
                    message="仅音频模式：已跳过画面扫描",
                    metrics={
                        "skipped": True,
                        "skip_reason": skip_reason,
                        "processing_scope": ProcessingScope.AUDIO_ONLY.value,
                    },
                )
        return states, workspace.ref_for(states_path, kind=ArtifactKind.VISUAL)

    def _extract_audio(
        self,
        workspace: RunWorkspace,
        media: MediaManifest,
        media_ref: ArtifactRef,
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[AudioExtractionResult | None, ArtifactRef]:
        manifest_path = workspace.artifact_path("audio", "extraction.json")
        wav_path = workspace.artifact_path("audio", "audio-16k-mono.wav")
        with workspace.stage(
            "audio.extract",
            stage_version=self.STAGE_VERSIONS["audio.extract"],
            config={"sample_rate": 16_000, "channels": 1},
            inputs=[media_ref],
        ) as stage:
            if stage.cached:
                payload = _read_json(manifest_path)
                extraction = (
                    AudioExtractionResult.model_validate(payload["extraction"])
                    if payload.get("extraction") is not None
                    else None
                )
            else:
                cancel.raise_if_cancelled()
                emit("audio.extract", progress=0.0, message="提取 16 kHz 单声道工作音轨")
                try:
                    extraction = extract_audio(
                        media,
                        wav_path,
                        ffmpeg_path=self.runtime.ffmpeg_path,
                    )
                except AudioExtractionError as error:
                    if media.audio_stream is not None:
                        raise
                    extraction = None
                    stage.add_warning(str(error))
                _write_json(
                    manifest_path,
                    {
                        "extraction": (
                            extraction.model_dump(mode="json") if extraction is not None else None
                        )
                    },
                )
                stage.add_output(manifest_path, kind=ArtifactKind.AUDIO)
                if extraction is not None:
                    stage.add_output(wav_path, kind=ArtifactKind.AUDIO)
                emit(
                    "audio.extract",
                    progress=1.0,
                    message="音轨已准备" if extraction else "视频没有可用音轨",
                )
        return extraction, workspace.ref_for(manifest_path, kind=ArtifactKind.AUDIO)

    def _parse_captions(
        self,
        workspace: RunWorkspace,
        subtitle_refs: list[ArtifactRef],
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[list[EvidenceSpan], ArtifactRef]:
        output = workspace.artifact_path("subtitles", "caption-evidence.json")
        with workspace.stage(
            "captions.parse",
            stage_version=self.STAGE_VERSIONS["captions.parse"],
            config={"parser": "srt-vtt-v1", "timeline_offset_us": 0},
            inputs=subtitle_refs,
        ) as stage:
            if stage.cached:
                evidence = _read_model_list(output, EvidenceSpan)
            else:
                emit("captions.parse", progress=0.0, message="解析平台字幕时间轴")
                evidence = []
                for index, subtitle_ref in enumerate(subtitle_refs):
                    cancel.raise_if_cancelled()
                    path = workspace.root / subtitle_ref.relative_path
                    try:
                        evidence.extend(
                            parse_subtitle_file(
                                path,
                                run_id=workspace.manifest.run_id,
                                language=_language_from_filename(path.name),
                            )
                        )
                    except (SubtitleParseError, UnicodeError) as error:
                        stage.add_warning(f"{path.name}: {error}")
                    emit(
                        "captions.parse",
                        progress=(index + 1) / max(1, len(subtitle_refs)),
                    )
                _write_json(
                    output,
                    [item.model_dump(mode="json") for item in evidence],
                )
                stage.add_output(output, kind=ArtifactKind.EVIDENCE)
                stage.add_metric("caption_count", len(evidence))
                emit("captions.parse", progress=1.0, message="平台字幕已规范化")
        return evidence, workspace.ref_for(output, kind=ArtifactKind.EVIDENCE)

    def _transcribe(
        self,
        workspace: RunWorkspace,
        extraction: AudioExtractionResult | None,
        extraction_ref: ArtifactRef,
        captions: list[EvidenceSpan],
        captions_ref: ArtifactRef,
        request: PipelineRequest,
        primary_asr_backend: ASRBackend | None,
        secondary_asr_backend: ASRBackend | None,
        secondary_policy: SecondaryAsrPolicy,
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[list[EvidenceSpan], ArtifactRef]:
        output = workspace.artifact_path("asr", "asr-evidence.json")
        decisions_path = workspace.artifact_path("asr", "secondary-decisions.json")
        with workspace.stage(
            "audio.asr",
            stage_version=self.STAGE_VERSIONS["audio.asr"],
            config={
                "primary_backend": _backend_identity(primary_asr_backend),
                "secondary_backend": _backend_identity(secondary_asr_backend),
                "secondary_policy": secondary_policy.value,
                "language_hints": request.language_hints,
            },
            inputs=[extraction_ref, captions_ref],
        ) as stage:
            if stage.cached:
                evidence = _read_model_list(output, EvidenceSpan)
            else:
                cancel.raise_if_cancelled()
                evidence = []
                decision_records: list[dict[str, Any]] = []
                if extraction is None:
                    stage.add_warning("No audio track was available for ASR.")
                else:
                    language = (
                        request.language_hints[0] if len(request.language_hints) == 1 else None
                    )
                    if primary_asr_backend is None:
                        warning = (
                            "No primary ASR backend is configured; transcript "
                            "contains captions and any configured selective secondary only."
                        )
                        stage.add_warning(warning)
                        workspace.add_warning(warning)
                    else:
                        emit(
                            "audio.asr",
                            progress=0.0,
                            message="运行带时间戳的主语音识别",
                        )
                        result: ASREvidenceResult = transcribe_to_evidence(
                            extraction,
                            primary_asr_backend,
                            run_id=workspace.manifest.run_id,
                            language=language,
                            language_hints=request.language_hints,
                        )
                        evidence = result.evidence

                    decisions = build_secondary_asr_decisions(
                        primary_asr=evidence,
                        platform_captions=captions,
                        language_hints=request.language_hints,
                    )
                    eligible = [
                        item
                        for item in decisions
                        if _secondary_is_enabled(item.reasons, secondary_policy)
                    ]
                    if eligible and secondary_asr_backend is None:
                        warning = (
                            f"{len(eligible)} ambiguous speech window(s) were found, "
                            "but no secondary ASR backend is configured."
                        )
                        stage.add_warning(warning)
                        workspace.add_warning(warning)
                    for index, decision in enumerate(eligible):
                        cancel.raise_if_cancelled()
                        clip_start = max(
                            extraction.output_time_zero_canonical_us,
                            decision.window_start_us - 250_000,
                        )
                        clip_end = min(
                            extraction.output_time_zero_canonical_us + extraction.duration_us,
                            decision.window_end_us + 250_000,
                        )
                        record: dict[str, Any] = {
                            "decision": decision.model_dump(mode="json"),
                            "clip_start_us": clip_start,
                            "clip_end_us": clip_end,
                            "status": "not_configured",
                            "secondary_evidence_ids": [],
                        }
                        if secondary_asr_backend is None or clip_end <= clip_start:
                            decision_records.append(record)
                            continue
                        clip_path = workspace.artifact_path(
                            "asr",
                            "secondary-clips",
                            (f"{index:04d}_{clip_start:015d}_{clip_end:015d}.wav"),
                        )
                        try:
                            clip = extract_audio_window(
                                extraction,
                                clip_path,
                                start_us=clip_start,
                                end_us=clip_end,
                                ffmpeg_path=self.runtime.ffmpeg_path,
                            )
                            stage.add_output(clip_path, kind=ArtifactKind.AUDIO)
                            secondary = transcribe_to_evidence(
                                clip,
                                secondary_asr_backend,
                                run_id=workspace.manifest.run_id,
                                language=language,
                                language_hints=request.language_hints,
                            )
                            enriched = [
                                _mark_secondary_evidence(item, decision)
                                for item in secondary.evidence
                            ]
                            evidence.extend(enriched)
                            record["status"] = "completed"
                            record["secondary_evidence_ids"] = [item.id for item in enriched]
                        except AcquisitionCancelled:
                            raise
                        except Exception as error:
                            record["status"] = "failed"
                            record["error_type"] = type(error).__name__
                            stage.add_warning(
                                "Selective secondary ASR failed for one window "
                                f"({type(error).__name__}); primary evidence was kept."
                            )
                        decision_records.append(record)
                        emit(
                            "audio.asr",
                            progress=(index + 1) / max(1, len(eligible)),
                            message=(f"选择性复核疑难语音片段 {index + 1}/{len(eligible)}"),
                        )
                _write_json(
                    output,
                    [item.model_dump(mode="json") for item in evidence],
                )
                _write_json(decisions_path, decision_records)
                stage.add_output(output, kind=ArtifactKind.EVIDENCE)
                stage.add_output(decisions_path, kind=ArtifactKind.ASR)
                stage.add_metric("asr_segment_count", len(evidence))
                stage.add_metric(
                    "secondary_window_count",
                    sum(item.get("status") == "completed" for item in decision_records),
                )
                emit("audio.asr", progress=1.0, message="语音证据已生成")
        return evidence, workspace.ref_for(output, kind=ArtifactKind.EVIDENCE)

    def _extract_ocr(
        self,
        workspace: RunWorkspace,
        visual_states: list[VisualState],
        visual_ref: ArtifactRef,
        request: PipelineRequest,
        ocr_backend: OcrBackend | None,
        execution_plan: ExecutionPlan,
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[OcrEvidenceBundle | None, list[EvidenceSpan], ArtifactRef]:
        output = workspace.artifact_path("ocr", "ocr-evidence.json")
        ocr_config = OcrPipelineConfig(
            inference_max_width=execution_plan.ocr_inference_max_width,
        )
        with workspace.stage(
            "ocr.extract",
            stage_version=self.STAGE_VERSIONS["ocr.extract"],
            config={
                "backend": _backend_identity(ocr_backend),
                "language_hints": request.language_hints,
                "pipeline": ocr_config.model_dump(mode="json"),
            },
            inputs=[visual_ref],
        ) as stage:
            if stage.cached:
                payload = _read_json(output)
                bundle = (
                    OcrEvidenceBundle.model_validate(payload["bundle"])
                    if payload.get("bundle") is not None
                    else None
                )
                evidence = bundle.evidence if bundle is not None else []
            else:
                cancel.raise_if_cancelled()
                bundle = None
                evidence = []
                if ocr_backend is None:
                    warning = "No OCR backend is configured; screen text was not recognized."
                    stage.add_warning(warning)
                    workspace.add_warning(warning)
                elif visual_states:
                    emit("ocr.extract", progress=0.0, message="识别持久画面中的可读文字")
                    bundle = extract_ocr_evidence(
                        visual_states,
                        backend=ocr_backend,
                        image_loader=FilesystemArtifactImageLoader(workspace.root),
                        config=ocr_config,
                        language_hints=request.language_hints,
                    )
                    evidence = bundle.evidence
                _write_json(
                    output,
                    {"bundle": (bundle.model_dump(mode="json") if bundle is not None else None)},
                )
                stage.add_output(output, kind=ArtifactKind.EVIDENCE)
                stage.add_metric("ocr_evidence_count", len(evidence))
                if bundle is not None:
                    stage.add_metric(
                        "screenshot_coverage_ratio",
                        bundle.scroll_selection.coverage_ratio,
                    )
                emit("ocr.extract", progress=1.0, message="画面文字证据已生成")
        return bundle, evidence, workspace.ref_for(output, kind=ArtifactKind.EVIDENCE)

    def _skip_ocr(
        self,
        workspace: RunWorkspace,
        visual_ref: ArtifactRef,
        emit: PipelineEmitter,
    ) -> tuple[None, list[EvidenceSpan], ArtifactRef]:
        """Persist an explicit OCR abstention while preserving downstream inputs."""

        output = workspace.artifact_path("ocr", "ocr-evidence.json")
        skip_reason = "audio_only_scope"
        config = {
            "processing_scope": ProcessingScope.AUDIO_ONLY.value,
            "skipped": True,
            "skip_reason": skip_reason,
        }
        with workspace.stage(
            "ocr.extract",
            stage_version=self.STAGE_VERSIONS["ocr.extract"],
            config=config,
            inputs=[visual_ref],
        ) as stage:
            if not stage.cached:
                _write_json(
                    output,
                    {
                        "bundle": None,
                        "processing_scope": ProcessingScope.AUDIO_ONLY.value,
                        "skipped": True,
                        "skip_reason": skip_reason,
                    },
                )
                stage.add_output(output, kind=ArtifactKind.EVIDENCE)
                stage.add_warning("OCR was intentionally skipped by the audio-only scope.")
                stage.add_metric("skipped", True)
                stage.add_metric("skip_reason", skip_reason)
                stage.add_metric("ocr_evidence_count", 0)
                emit(
                    "ocr.extract",
                    progress=1.0,
                    message="仅音频模式：已跳过画面文字识别",
                    metrics={
                        "skipped": True,
                        "skip_reason": skip_reason,
                        "processing_scope": ProcessingScope.AUDIO_ONLY.value,
                    },
                )
        return None, [], workspace.ref_for(output, kind=ArtifactKind.EVIDENCE)

    def _fuse(
        self,
        workspace: RunWorkspace,
        source: SourceManifest,
        media: MediaManifest,
        visual_states: list[VisualState],
        evidence: list[EvidenceSpan],
        input_refs: list[ArtifactRef],
        processing_scope: ProcessingScope,
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[FusionResult, ArtifactRef]:
        output = workspace.artifact_path("evidence", "timeline.json")
        config: dict[str, Any] = {
            "association": "interval-overlap-v1",
            "speech_gap_us": 1_200_000,
            "maximum_window_us": 90_000_000,
        }
        if processing_scope is ProcessingScope.AUDIO_ONLY:
            config["processing_scope"] = processing_scope.value
        with workspace.stage(
            "evidence.fuse",
            stage_version=self.STAGE_VERSIONS["evidence.fuse"],
            config=config,
            inputs=input_refs,
        ) as stage:
            if stage.cached:
                fusion = _read_model(output, FusionResult)
            else:
                cancel.raise_if_cancelled()
                emit(
                    "evidence.fuse",
                    progress=0.0,
                    message=(
                        "按物理时间对齐音频与字幕证据"
                        if processing_scope is ProcessingScope.AUDIO_ONLY
                        else "按物理时间对齐音画证据"
                    ),
                )
                metadata_text = " · ".join(
                    item for item in (source.title, source.author, source.description) if item
                )
                if metadata_text:
                    evidence = [
                        EvidenceSpan(
                            id="metadata-source",
                            run_id=workspace.manifest.run_id,
                            modality=EvidenceModality.METADATA,
                            start_us=0,
                            end_us=media.duration_us,
                            raw_text=metadata_text,
                            normalized_text=" ".join(metadata_text.split()),
                            provider=source.platform.value,
                            model="source-metadata",
                            version="1",
                        ),
                        *evidence,
                    ]
                fusion = build_evidence_timeline(evidence, visual_states)
                _write_model(output, fusion)
                stage.add_output(output, kind=ArtifactKind.EVIDENCE)
                stage.add_metric("evidence_count", len(fusion.evidence))
                stage.add_metric("conflict_count", len(fusion.conflicts))
                stage.add_metric("window_count", len(fusion.windows))
                emit(
                    "evidence.fuse",
                    progress=1.0,
                    message=f"建立 {len(fusion.windows)} 个证据窗口",
                )
        return fusion, workspace.ref_for(output, kind=ArtifactKind.EVIDENCE)

    def _compose_note(
        self,
        workspace: RunWorkspace,
        request: PipelineRequest,
        source: SourceManifest,
        acquisition: AcquisitionResult,
        media: MediaManifest,
        fusion: FusionResult,
        fusion_ref: ArtifactRef,
        ocr_bundle: OcrEvidenceBundle | None,
        execution_plan: ExecutionPlan,
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[NoteCompositionResult, ArtifactRef]:
        document_path = workspace.artifact_path("notes", "document.json")
        composition_path = workspace.artifact_path("notes", "composition.json")
        report_spec = request.effective_report_spec()
        resolved_report = report_spec.resolve()
        with workspace.stage(
            "notes.compose",
            stage_version=self.STAGE_VERSIONS["notes.compose"],
            config={
                "composer": _composer_identity(self.runtime.note_composer),
                "processing_scope": request.processing_scope.value,
                "report_spec": resolved_report.model_dump(mode="json"),
                "title_override": request.title_override,
                "quality_mode": request.quality_mode.value,
                "verification_passes": execution_plan.verification_passes,
                "screenshot_budget_per_section": (execution_plan.screenshot_budget_per_section),
                "remote_model_concurrency": execution_plan.remote_model_concurrency,
            },
            inputs=[fusion_ref],
        ) as stage:
            if stage.cached:
                document = _read_model(document_path, NoteDocument)
                composition_payload = _read_json(composition_path)
                composition = NoteCompositionResult(
                    note=document,
                    invocations=composition_payload.get("invocations", []),
                    warnings=composition_payload.get("warnings", []),
                    used_deterministic_fallback=bool(
                        composition_payload.get("used_deterministic_fallback")
                    ),
                )
            else:
                cancel.raise_if_cancelled()
                emit("notes.compose", progress=0.0, message="从证据卡片组织笔记")
                languages = sorted(
                    {item.language for item in fusion.evidence if item.language is not None}
                )
                warnings = [
                    item
                    for item in (
                        source.quality_warning,
                        *acquisition.warnings,
                        *workspace.manifest.warnings,
                    )
                    if item
                ]
                metadata = NoteMetadata(
                    title=(
                        request.title_override or source.title or Path(acquisition.media_path).stem
                    ),
                    run_id=workspace.manifest.run_id,
                    source_kind=source.platform.value,
                    source_locator=source.source.value,
                    source_url=source.canonical_url,
                    author=source.author,
                    duration_us=media.duration_us,
                    languages=languages,
                    quality_mode=request.quality_mode.value,
                    source_resolution=_source_resolution(source),
                    quality_warnings=list(dict.fromkeys(warnings)),
                )
                screenshots = (
                    _screenshots_for_windows(
                        workspace,
                        fusion,
                        ocr_bundle,
                        max_per_window=execution_plan.screenshot_budget_per_section,
                    )
                    if resolved_report.include_screenshots
                    and execution_plan.screenshot_budget_per_section > 0
                    else {}
                )
                composition = self.runtime.note_composer.compose(
                    metadata,
                    fusion,
                    screenshots_by_window=screenshots,
                    max_screenshots_per_section=(execution_plan.screenshot_budget_per_section),
                    report_spec=report_spec,
                    verification_passes=execution_plan.verification_passes,
                    max_model_concurrency=execution_plan.remote_model_concurrency,
                )
                _write_model(document_path, composition.note)
                _write_json(
                    composition_path,
                    {
                        "invocations": [
                            item.model_dump(mode="json") for item in composition.invocations
                        ],
                        "warnings": composition.warnings,
                        "used_deterministic_fallback": (composition.used_deterministic_fallback),
                    },
                )
                stage.add_output(document_path, kind=ArtifactKind.NOTE)
                stage.add_output(composition_path, kind=ArtifactKind.NOTE)
                for window_screenshots in screenshots.values():
                    for screenshot in window_screenshots:
                        stage.add_output(
                            workspace.root / screenshot.relative_path,
                            kind=ArtifactKind.VISUAL,
                        )
                for warning in composition.warnings:
                    stage.add_warning(warning)
                emit("notes.compose", progress=1.0, message="规范笔记文档已生成")
        return composition, workspace.ref_for(document_path, kind=ArtifactKind.NOTE)

    def _render(
        self,
        workspace: RunWorkspace,
        request: PipelineRequest,
        composition: NoteCompositionResult,
        note_ref: ArtifactRef,
        fusion: FusionResult,
        visual_states: list[VisualState],
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> PipelineOutcome:
        markdown_path = workspace.artifact_path("notes", "note.md")
        html_path = workspace.artifact_path("render", "note.html")
        pdf_path = workspace.artifact_path("render", "note.pdf")
        outcome_path = workspace.artifact_path("render", "outcome.json")
        resolved_report = request.effective_report_spec().resolve()
        generate_pdf = OutputFormat.PDF in resolved_report.output_formats
        with workspace.stage(
            "render.outputs",
            stage_version=self.STAGE_VERSIONS["render.outputs"],
            config={
                "markdown": True,
                "html": True,
                "pdf": generate_pdf,
                "processing_scope": request.processing_scope.value,
                "report_spec": resolved_report.model_dump(mode="json"),
                "theme": "evidence-light-table-v1",
            },
            inputs=[note_ref],
        ) as stage:
            if not stage.cached:
                cancel.raise_if_cancelled()
                emit("render.outputs", progress=0.0, message="渲染 Markdown 与阅读版")
                write_markdown(composition.note, markdown_path)
                write_html(
                    composition.note,
                    html_path,
                    artifact_root=workspace.root,
                )
                stage.add_output(markdown_path, kind=ArtifactKind.NOTE)
                stage.add_output(html_path, kind=ArtifactKind.RENDER)
                if generate_pdf:
                    render_pdf_from_html(
                        html_path,
                        pdf_path,
                        browser_executable=self.runtime.pdf_browser_executable,
                    )
                    stage.add_output(pdf_path, kind=ArtifactKind.RENDER)
                outcome = PipelineOutcome(
                    run_id=workspace.manifest.run_id,
                    processing_scope=request.processing_scope,
                    markdown=workspace.ref_for(
                        markdown_path,
                        kind=ArtifactKind.NOTE,
                    ),
                    html=workspace.ref_for(
                        html_path,
                        kind=ArtifactKind.RENDER,
                    ),
                    pdf=(
                        workspace.ref_for(pdf_path, kind=ArtifactKind.RENDER)
                        if generate_pdf
                        else None
                    ),
                    note_document=note_ref,
                    evidence_count=len(fusion.evidence),
                    visual_state_count=len(visual_states),
                    used_deterministic_note_fallback=(composition.used_deterministic_fallback),
                )
                _write_model(outcome_path, outcome)
                stage.add_output(outcome_path, kind=ArtifactKind.RENDER)
                emit("render.outputs", progress=1.0, message="笔记输出已完成")
        return _read_model(outcome_path, PipelineOutcome)


T = TypeVar("T", bound=BaseModel)


def _read_model(path: Path, model: type[T]) -> T:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _read_model_list(path: Path, model: type[T]) -> list[T]:
    payload = _read_json(path)
    if not isinstance(payload, list):
        raise ValueError(f"artifact must contain a JSON list: {path}")
    return [model.model_validate(item) for item in payload]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_model(path: Path, value: BaseModel) -> None:
    _write_json(path, value.model_dump(mode="json"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _fixed_interval_events(
    media: MediaManifest,
    segment: SamplingSegment,
    *,
    preview_dir: Path,
    max_fixed_samples: int,
    decode_threads: int,
    cancel: CancellationToken,
) -> list[ChangeEvent]:
    """Decode and persist real source frames for one fixed-interval segment."""

    interval_us = segment.sampling.interval_us
    if segment.sampling.mode is not SamplingMode.FIXED_INTERVAL or interval_us is None:
        raise ValueError("fixed interval events require a fixed_interval segment")
    video_stream = media.video_stream
    if video_stream is None:
        raise ValueError("input does not contain a video stream")

    preview_dir.mkdir(parents=True, exist_ok=True)
    events: list[ChangeEvent] = []
    previous_keyframe = None
    for index, decoded in enumerate(
        iter_video_frames(
            media.source_path,
            timeline_origin_us=media.timeline_origin_us,
            stream_index=video_stream.index,
            sample_period_us=interval_us,
            start_us=segment.start_us,
            end_us=segment.end_us,
            target_size=None,
            decode_threads=decode_threads,
        )
    ):
        cancel.raise_if_cancelled()
        if index >= max_fixed_samples:
            raise ValueError(
                f"fixed_interval sampling exceeded runtime maximum of {max_fixed_samples} frames"
            )
        requested_time_us = (
            decoded.requested_time_us
            if decoded.requested_time_us is not None
            else decoded.timestamp.time_us
        )
        preview_path = preview_dir / (
            f"{index:05d}_{requested_time_us:015d}req_{decoded.timestamp.time_us:015d}us_fixed.jpg"
        )
        decoded.image.save(
            preview_path,
            format="JPEG",
            quality=95,
            subsampling=0,
        )
        events.append(
            ChangeEvent(
                transition=decoded.timestamp,
                keyframe=decoded.timestamp,
                previous_keyframe=previous_keyframe,
                reason="fixed_interval",
                state_score=0.0,
                scene_score=0.0,
                text_score=0.0,
                step_score=0.0,
                refined=False,
                preview_path=str(preview_path),
                sampling_mode="fixed_interval",
                requested_time_us=requested_time_us,
                requested_interval_us=interval_us,
                segment_start_us=segment.start_us,
                segment_end_us=segment.end_us,
            )
        )
        previous_keyframe = decoded.timestamp
    return events


def _events_to_visual_states(
    workspace: RunWorkspace,
    events: list[ChangeEvent],
    *,
    duration_us: int,
    run_id: str,
    add_artifact: Callable[..., ArtifactRef],
) -> list[VisualState]:
    states: list[VisualState] = []
    for index, event in enumerate(events):
        segment_start_us = event.segment_start_us if event.segment_start_us is not None else 0
        segment_end_us = event.segment_end_us if event.segment_end_us is not None else duration_us
        next_transition = (
            min(events[index + 1].transition_us, segment_end_us)
            if index + 1 < len(events)
            else segment_end_us
        )
        end_us = max(event.keyframe_us, next_transition)
        keyframe_ref: ArtifactRef | None = None
        if event.preview_path is not None:
            keyframe_ref = add_artifact(
                event.preview_path,
                kind=ArtifactKind.VISUAL,
                media_type="image/jpeg",
            )
        states.append(
            VisualState(
                id=f"visual-state-{index:05d}",
                run_id=run_id,
                start_us=max(0, segment_start_us, event.transition_us),
                end_us=max(0, min(end_us, segment_end_us)),
                transition_us=max(0, segment_start_us, event.transition_us),
                stable_keyframe_us=max(0, event.keyframe_us),
                transition_pts=event.transition.pts,
                keyframe_pts=event.keyframe.pts,
                stream_time_base=event.keyframe.time_base,
                keyframe_artifact=keyframe_ref,
                change_reason=event.reason,
                quality={
                    "state_score": event.state_score,
                    "scene_score": event.scene_score,
                    "text_score": event.text_score,
                    "step_score": event.step_score,
                    "refined": event.refined,
                    "sampling_mode": event.sampling_mode,
                    "requested_time_us": event.requested_time_us,
                    "requested_interval_us": event.requested_interval_us,
                },
            )
        )
    return states


def _backend_identity(backend: object | None) -> dict[str, Any] | None:
    if backend is None:
        return None
    identity: dict[str, Any] = {"class": type(backend).__qualname__}
    for name in ("provider_id", "model_id"):
        value = getattr(backend, name, None)
        if isinstance(value, str):
            identity[name] = value
    config = getattr(backend, "config", None) or getattr(backend, "_config", None)
    if isinstance(config, BaseModel):
        identity["config"] = config.model_dump(mode="json")
    runtime_identity = getattr(backend, "runtime_identity", None)
    if isinstance(runtime_identity, Mapping):
        identity["runtime"] = {
            str(key): value
            for key, value in runtime_identity.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
    return identity


def _composer_identity(composer: EvidenceNoteComposer) -> dict[str, Any]:
    return {
        "fact": _backend_identity(composer.fact_backend),
        "draft": _backend_identity(composer.draft_backend),
        "verifier": _backend_identity(composer.verifier_backend),
        "fallback_on_error": composer.fallback_on_error,
    }


def _language_from_filename(name: str) -> str | None:
    parts = name.replace("_", ".").split(".")
    for candidate in reversed(parts[:-1]):
        normalized = candidate.replace("_", "-")
        if 2 <= len(normalized) <= 12 and normalized[0:2].isalpha():
            return normalized
    return None


def _source_resolution(source: SourceManifest) -> str | None:
    selected = [item for item in source.selected_formats if item.has_video]
    if not selected:
        return None
    best = max(selected, key=lambda item: (item.height or 0, item.width or 0))
    if best.width and best.height:
        return f"{best.width}×{best.height}"
    if best.height:
        return f"{best.height}p"
    return None


def _asr_backend_for_plan(
    backend: ASRBackend | None,
    plan: ExecutionPlan,
) -> ASRBackend | None:
    plan_adapter = getattr(backend, "for_execution_plan", None)
    if callable(plan_adapter):
        adapted = plan_adapter(plan)
        return adapted if hasattr(adapted, "transcribe") else backend
    if not isinstance(backend, FasterWhisperBackend):
        return backend
    return FasterWhisperBackend(
        backend.config.model_copy(
            update={
                "device": plan.asr_device,
                "compute_type": plan.asr_compute_type,
                "cpu_threads": plan.asr_cpu_threads,
                "beam_size": plan.asr_beam_size,
            }
        )
    )


def _ocr_backend_for_plan(
    backend: OcrBackend | None,
    plan: ExecutionPlan,
) -> OcrBackend | None:
    plan_adapter = getattr(backend, "for_execution_plan", None)
    if callable(plan_adapter):
        adapted = plan_adapter(plan)
        return adapted if hasattr(adapted, "recognize") else backend
    if not isinstance(backend, PaddleOcrBackend):
        return backend
    device = "gpu:0" if plan.ocr_device == "cuda" else plan.ocr_device
    return PaddleOcrBackend(
        backend.config.model_copy(
            update={
                "device": device,
                "cpu_threads": plan.ocr_cpu_threads,
            }
        )
    )


def _align_execution_plan_with_backends(
    plan: ExecutionPlan,
    *,
    primary_asr_backend: ASRBackend | None,
    ocr_backend: OcrBackend | None,
) -> ExecutionPlan:
    """Replace planned model classes with the models that will actually run.

    Hardware profiles describe the preferred model class. A configured local
    backend can legitimately point at a smaller (or simply different) model,
    so the persisted *effective* plan must not keep claiming the preference.
    """

    actual_asr = _actual_asr_model_class(primary_asr_backend)
    actual_ocr = _actual_ocr_model_class(ocr_backend)
    notes = list(plan.notes)
    asr_note = _model_class_alignment_note(
        component="ASR",
        planned=plan.asr_model_class,
        actual=actual_asr,
        ranks={
            "tiny": 0,
            "base": 1,
            "small": 2,
            "medium": 3,
            "large-v1": 4,
            "large-v2": 5,
            "large-v3": 6,
            "large-v3-turbo": 6,
        },
    )
    if asr_note is not None:
        notes.append(asr_note)
    ocr_note = _model_class_alignment_note(
        component="OCR",
        planned=plan.ocr_model_class,
        actual=actual_ocr,
        ranks={"mobile": 0, "medium": 1, "server": 2},
    )
    if ocr_note is not None:
        notes.append(ocr_note)
    return plan.model_copy(
        update={
            "asr_model_class": actual_asr,
            "ocr_model_class": actual_ocr,
            "notes": tuple(dict.fromkeys(notes)),
        }
    )


def _actual_asr_model_class(backend: ASRBackend | None) -> str:
    if backend is None:
        return "unavailable"
    if isinstance(backend, FasterWhisperBackend):
        basename = _portable_basename(backend.config.model_path)
        lowered = basename.casefold()
        for prefix in ("faster-whisper-", "whisper-"):
            if lowered.startswith(prefix):
                return basename[len(prefix) :]
        return basename
    model_id = getattr(backend, "model_id", None)
    if isinstance(model_id, str) and model_id.strip():
        return model_id.strip()
    return type(backend).__qualname__


def _actual_ocr_model_class(backend: OcrBackend | None) -> str:
    if backend is None:
        return "unavailable"
    if isinstance(backend, PaddleOcrBackend):
        model_names = (
            _portable_basename(backend.config.detection_model_dir),
            _portable_basename(backend.config.recognition_model_dir),
        )
        joined = " ".join(model_names).casefold()
        has_mobile = "mobile" in joined
        has_server = "server" in joined
        if has_mobile and has_server:
            return "mixed"
        if has_server:
            return "server"
        if has_mobile:
            return "mobile"
        return "+".join(dict.fromkeys(model_names))
    model_id = getattr(backend, "model_id", None)
    if isinstance(model_id, str) and model_id.strip():
        return model_id.strip()
    return type(backend).__qualname__


def _portable_basename(value: str) -> str:
    normalized = value.strip().rstrip("/\\").replace("\\", "/")
    return normalized.rsplit("/", maxsplit=1)[-1] or value


def _model_class_alignment_note(
    *,
    component: str,
    planned: str,
    actual: str,
    ranks: Mapping[str, int],
) -> str | None:
    if actual.casefold() == planned.casefold():
        return None
    normalized_actual = actual.casefold().removesuffix(".en")
    normalized_planned = planned.casefold().removesuffix(".en")
    actual_rank = ranks.get(normalized_actual)
    planned_rank = ranks.get(normalized_planned)
    is_downgrade = actual == "unavailable" or (
        actual_rank is not None and planned_rank is not None and actual_rank < planned_rank
    )
    verb = "downgraded" if is_downgrade else "adjusted"
    return (
        f"{component} model class was {verb} from {planned!r} to {actual!r} "
        "to reflect the selected backend."
    )


def _secondary_is_enabled(
    reasons: list[SecondaryASRReason],
    policy: SecondaryAsrPolicy,
) -> bool:
    if policy is SecondaryAsrPolicy.OFF:
        return False
    if policy is SecondaryAsrPolicy.CONFLICTS_ONLY:
        return any(
            item
            in {
                SecondaryASRReason.CAPTION_CONFLICT,
                SecondaryASRReason.LANGUAGE_CONFLICT,
            }
            for item in reasons
        )
    return bool(reasons)


def _mark_secondary_evidence(
    evidence: EvidenceSpan,
    decision: SecondaryASRDecision,
) -> EvidenceSpan:
    provenance = dict(evidence.provenance)
    provenance.update(
        {
            "asr_pass": "selective_secondary",
            "decision_reasons": [item.value for item in decision.reasons],
            "decision_window_start_us": decision.window_start_us,
            "decision_window_end_us": decision.window_end_us,
            "primary_evidence_ids": decision.primary_evidence_ids,
            "caption_evidence_ids": decision.caption_evidence_ids,
        }
    )
    return EvidenceSpan.model_validate(
        {
            **evidence.model_dump(exclude={"provenance", "parent_hypothesis_id"}),
            "parent_hypothesis_id": (
                decision.primary_evidence_ids[0]
                if len(decision.primary_evidence_ids) == 1
                else None
            ),
            "provenance": provenance,
        }
    )


def _screenshots_for_windows(
    workspace: RunWorkspace,
    fusion: FusionResult,
    ocr: OcrEvidenceBundle | None,
    *,
    max_per_window: int,
) -> dict[str, list[NoteScreenshot]]:
    if max_per_window < 1:
        return {}
    state_time = {state.id: state.stable_keyframe_us for state in fusion.visual_states}
    evidence_by_state: dict[str, list[str]] = {}
    text_by_state: dict[str, list[str]] = {}
    if ocr is not None:
        for evidence in ocr.evidence:
            visual_state_id = evidence.provenance.get("visual_state_id")
            if not isinstance(visual_state_id, str):
                continue
            evidence_by_state.setdefault(visual_state_id, []).append(evidence.id)
            text = evidence.normalized_text or evidence.raw_text
            if text:
                text_by_state.setdefault(visual_state_id, []).append(text)

    result: dict[str, list[NoteScreenshot]] = {}
    selected_states: set[str] = set()

    def add_screenshot(
        *,
        visual_state_id: str,
        artifact: ArtifactRef | None,
        caption: str,
        evidence_ids: list[str],
    ) -> None:
        timestamp_us = state_time.get(visual_state_id)
        if artifact is None or timestamp_us is None:
            return
        window = next(
            (
                item
                for item in fusion.windows
                if item.start_us <= timestamp_us < item.end_us
                or (item is fusion.windows[-1] and timestamp_us == item.end_us)
            ),
            None,
        )
        if window is None:
            return
        if len(result.get(window.id, [])) >= max_per_window:
            return
        source = workspace.root / artifact.relative_path
        if not source.is_file():
            return
        suffix = source.suffix.lower() or ".jpg"
        asset_relative = Path("notes", "assets", f"{visual_state_id}{suffix}").as_posix()
        destination = workspace.root / asset_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        result.setdefault(window.id, []).append(
            NoteScreenshot(
                relative_path=asset_relative,
                timestamp_us=timestamp_us,
                caption=caption,
                alt_text=caption,
                evidence_ids=evidence_ids,
            )
        )
        selected_states.add(visual_state_id)

    if ocr is not None:
        for selected in ocr.scroll_selection.selected_frames:
            state_text = text_by_state.get(selected.visual_state_id, [])
            if any(contains_sensitive_note_text(item) for item in state_text):
                continue
            visible_text = " / ".join(state_text[:3])
            caption = (
                f"关键画面文字：{visible_text}" if visible_text else "覆盖独特屏幕文字的关键画面"
            )
            add_screenshot(
                visual_state_id=selected.visual_state_id,
                artifact=selected.keyframe_artifact,
                caption=caption,
                evidence_ids=evidence_by_state.get(selected.visual_state_id, []),
            )

    # OCR may correctly abstain on a photograph or low-resolution frame. Keep one
    # content-adaptive representative per evidence window so the note still carries
    # visual context; this never falls back to fixed-N-second sampling.
    for window in fusion.windows:
        if result.get(window.id):
            continue
        candidates = [
            state
            for state in fusion.visual_states
            if state.id not in selected_states
            and not any(
                contains_sensitive_note_text(item) for item in text_by_state.get(state.id, [])
            )
            and state.keyframe_artifact is not None
            and window.start_us <= state.stable_keyframe_us <= window.end_us
        ]
        if not candidates:
            continue

        def state_score(state: VisualState) -> float:
            raw = state.quality.get("state_score", 0.0)
            return float(raw) if isinstance(raw, (int, float)) else 0.0

        representative = max(
            candidates,
            key=lambda state: (state_score(state), -state.stable_keyframe_us),
        )
        contextual_evidence = [
            identifier
            for identifier in window.evidence_ids
            if identifier in {item.id for item in fusion.evidence}
        ][:3]
        add_screenshot(
            visual_state_id=representative.id,
            artifact=representative.keyframe_artifact,
            caption="内容自适应扫描选出的稳定代表画面",
            evidence_ids=contextual_evidence,
        )
    return result
