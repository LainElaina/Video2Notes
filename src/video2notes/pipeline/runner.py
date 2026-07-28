"""Acquisition-to-note pipeline with a hash-verified artifact at every stage."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from video2notes.artifacts import RunWorkspace
from video2notes.audio import (
    ASRBackend,
    ASREvidenceResult,
    AudioExtractionError,
    AudioExtractionResult,
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
    RunStatus,
    SourceDescriptor,
    VisualState,
)
from video2notes.fusion import FusionResult, build_evidence_timeline
from video2notes.notes import (
    EvidenceNoteComposer,
    NoteCompositionResult,
    NoteDocument,
    NoteMetadata,
    NoteScreenshot,
    render_pdf_from_html,
    write_html,
    write_markdown,
)
from video2notes.ocr import (
    FilesystemArtifactImageLoader,
    OcrBackend,
    OcrEvidenceBundle,
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
    HardwareSnapshot,
    QualityMode,
    build_execution_plan,
    detect_hardware,
)
from video2notes.vision import (
    AdaptiveScanConfig,
    AdaptiveVideoScanner,
    ChangeEvent,
)


class PipelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PipelineRequest(PipelineModel):
    source: SourceInput
    auth: AuthSpec = Field(default_factory=AuthSpec)
    acquisition: AcquisitionPolicy = Field(default_factory=AcquisitionPolicy)
    quality_mode: QualityMode = QualityMode.BALANCED
    title_override: str | None = None
    language_hints: list[str] = Field(default_factory=list)
    include_screenshots: bool = True
    generate_pdf: bool = True


class PipelineOutcome(PipelineModel):
    run_id: str
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
    hardware: HardwareSnapshot | None = None
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
        "vision.scan": "3",
        "audio.extract": "2",
        "captions.parse": "2",
        "audio.asr": "4",
        "ocr.extract": "2",
        "evidence.fuse": "2",
        "notes.compose": "3",
        "render.outputs": "5",
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
        workspace.set_status(RunStatus.RUNNING)
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
            plan = build_execution_plan(
                self.runtime.hardware or detect_hardware(),
                request.quality_mode,
            )
            current_stage = "vision.scan"
            visual_states, visual_ref = self._scan_visual_states(
                workspace,
                media,
                media_ref,
                plan.model_dump(mode="json"),
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
                cancellation,
                progress,
            )
            current_stage = "ocr.extract"
            ocr_bundle, ocr_evidence, ocr_ref = self._extract_ocr(
                workspace,
                visual_states,
                visual_ref,
                request,
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
        plan: dict[str, Any],
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[list[VisualState], ArtifactRef]:
        states_path = workspace.artifact_path("vision", "visual-states.json")
        events_path = workspace.artifact_path("vision", "scan-events.json")
        analysis_width = int(plan["analysis_width"])
        analysis_height = max(64, round(analysis_width * 9 / 16))
        if analysis_height % 2:
            analysis_height += 1
        config = AdaptiveScanConfig(
            coarse_fps=float(plan["cheap_scan_fps"]),
            fine_fps=float(plan["expensive_scan_fps"]),
            analysis_width=analysis_width,
            analysis_height=analysis_height,
        )
        with workspace.stage(
            "vision.scan",
            stage_version=self.STAGE_VERSIONS["vision.scan"],
            config={
                "scanner": asdict(config),
                "ffprobe": self.runtime.ffprobe_path,
            },
            inputs=[media_ref],
        ) as stage:
            if stage.cached:
                states = _read_model_list(states_path, VisualState)
            else:
                cancel.raise_if_cancelled()
                emit("vision.scan", progress=0.0, message="顺序扫描持久画面变化")
                scanner = AdaptiveVideoScanner(
                    config,
                    ffmpeg_path=self.runtime.ffmpeg_path,
                    ffprobe_path=self.runtime.ffprobe_path,
                    cancel_check=cancel.raise_if_cancelled,
                )
                result = scanner.scan(
                    media.source_path,
                    preview_dir=workspace.artifact_path("vision", "keyframes"),
                )
                _write_json(events_path, result.to_dict())
                stage.add_output(events_path, kind=ArtifactKind.VISUAL)
                states = _events_to_visual_states(
                    workspace,
                    list(result.events),
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
                emit(
                    "vision.scan",
                    progress=1.0,
                    message=f"发现 {len(states)} 个持久画面状态",
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
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[list[EvidenceSpan], ArtifactRef]:
        output = workspace.artifact_path("asr", "asr-evidence.json")
        decisions_path = workspace.artifact_path("asr", "secondary-decisions.json")
        with workspace.stage(
            "audio.asr",
            stage_version=self.STAGE_VERSIONS["audio.asr"],
            config={
                "primary_backend": _backend_identity(self.runtime.asr_backend),
                "secondary_backend": _backend_identity(self.runtime.secondary_asr_backend),
                "secondary_policy": request.quality_mode.value,
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
                    if self.runtime.asr_backend is None:
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
                            self.runtime.asr_backend,
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
                        if _secondary_is_enabled(item.reasons, request.quality_mode)
                    ]
                    if eligible and self.runtime.secondary_asr_backend is None:
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
                        if self.runtime.secondary_asr_backend is None or clip_end <= clip_start:
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
                                self.runtime.secondary_asr_backend,
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
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[OcrEvidenceBundle | None, list[EvidenceSpan], ArtifactRef]:
        output = workspace.artifact_path("ocr", "ocr-evidence.json")
        with workspace.stage(
            "ocr.extract",
            stage_version=self.STAGE_VERSIONS["ocr.extract"],
            config={
                "backend": _backend_identity(self.runtime.ocr_backend),
                "language_hints": request.language_hints,
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
                if self.runtime.ocr_backend is None:
                    warning = "No OCR backend is configured; screen text was not recognized."
                    stage.add_warning(warning)
                    workspace.add_warning(warning)
                elif visual_states:
                    emit("ocr.extract", progress=0.0, message="识别持久画面中的可读文字")
                    bundle = extract_ocr_evidence(
                        visual_states,
                        backend=self.runtime.ocr_backend,
                        image_loader=FilesystemArtifactImageLoader(workspace.root),
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

    def _fuse(
        self,
        workspace: RunWorkspace,
        source: SourceManifest,
        media: MediaManifest,
        visual_states: list[VisualState],
        evidence: list[EvidenceSpan],
        input_refs: list[ArtifactRef],
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[FusionResult, ArtifactRef]:
        output = workspace.artifact_path("evidence", "timeline.json")
        with workspace.stage(
            "evidence.fuse",
            stage_version=self.STAGE_VERSIONS["evidence.fuse"],
            config={
                "association": "interval-overlap-v1",
                "speech_gap_us": 1_200_000,
                "maximum_window_us": 90_000_000,
            },
            inputs=input_refs,
        ) as stage:
            if stage.cached:
                fusion = _read_model(output, FusionResult)
            else:
                cancel.raise_if_cancelled()
                emit("evidence.fuse", progress=0.0, message="按物理时间对齐音画证据")
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
        cancel: CancellationToken,
        emit: PipelineEmitter,
    ) -> tuple[NoteCompositionResult, ArtifactRef]:
        document_path = workspace.artifact_path("notes", "document.json")
        composition_path = workspace.artifact_path("notes", "composition.json")
        with workspace.stage(
            "notes.compose",
            stage_version=self.STAGE_VERSIONS["notes.compose"],
            config={
                "composer": _composer_identity(self.runtime.note_composer),
                "screenshots": request.include_screenshots,
                "title_override": request.title_override,
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
                    _screenshots_for_windows(workspace, fusion, ocr_bundle)
                    if request.include_screenshots
                    else {}
                )
                composition = self.runtime.note_composer.compose(
                    metadata,
                    fusion,
                    screenshots_by_window=screenshots,
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
        with workspace.stage(
            "render.outputs",
            stage_version=self.STAGE_VERSIONS["render.outputs"],
            config={
                "markdown": True,
                "html": True,
                "pdf": request.generate_pdf,
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
                if request.generate_pdf:
                    render_pdf_from_html(
                        html_path,
                        pdf_path,
                        browser_executable=self.runtime.pdf_browser_executable,
                    )
                    stage.add_output(pdf_path, kind=ArtifactKind.RENDER)
                outcome = PipelineOutcome(
                    run_id=workspace.manifest.run_id,
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
                        if request.generate_pdf
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
        next_transition = (
            events[index + 1].transition_us if index + 1 < len(events) else duration_us
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
                start_us=max(0, event.transition_us),
                end_us=max(0, end_us),
                transition_us=max(0, event.transition_us),
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


def _secondary_is_enabled(
    reasons: list[SecondaryASRReason],
    quality_mode: QualityMode,
) -> bool:
    if quality_mode is QualityMode.FAST:
        return False
    if quality_mode is QualityMode.BALANCED:
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
) -> dict[str, list[NoteScreenshot]]:
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
            visible_text = " / ".join(text_by_state.get(selected.visual_state_id, [])[:3])
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
