"""Range-local, append-only rework operations for completed runs.

Operations never mutate the evidence produced by the main pipeline.  Every
successful operation writes a complete evidence revision whose effective view
replaces only the selected modality inside the requested half-open time range.
The original and superseded evidence remain in the revision history for audit.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from video2notes.artifacts import RunWorkspace
from video2notes.audio import (
    ASRBackend,
    ASREvidenceResult,
    AudioExtractionResult,
    extract_audio_window,
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
    VisualState,
)
from video2notes.media import DecodedVideoFrame, iter_video_frames
from video2notes.ocr import (
    FilesystemArtifactImageLoader,
    OcrBackend,
    extract_ocr_evidence,
)
from video2notes.vision import (
    MAX_FIXED_SAMPLES,
    AdaptiveScanConfig,
    AdaptiveVideoScanner,
    ChangeEvent,
    SamplingMode,
    SamplingSpec,
    TimeRange,
)
from video2notes.vision.adaptive_sampler import ScanResult, VideoProbe

_SAFE_PERSISTED_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class OperationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class OperationKind(StrEnum):
    VISION_RESCAN = "vision_rescan"
    ASR_RETRANSCRIBE = "asr_retranscribe"
    EVIDENCE_CORRECT = "evidence_correct"


class OperationStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class OperationRequest(OperationModel):
    """One bounded rework request; fields are validated against ``kind``."""

    kind: OperationKind
    range: TimeRange
    sampling: SamplingSpec | None = None
    run_ocr: bool = True
    language_hints: list[str] = Field(default_factory=list, max_length=8)
    evidence_id: str | None = Field(default=None, max_length=300)
    new_text: str | None = Field(default=None, max_length=200_000)
    reason: str | None = Field(default=None, max_length=2_000)

    @field_validator("language_hints")
    @classmethod
    def normalize_language_hints(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_kind_fields(self) -> Self:
        if self.kind is OperationKind.VISION_RESCAN:
            sampling = self.sampling or SamplingSpec()
            if sampling.mode is SamplingMode.SKIP:
                raise ValueError("vision_rescan cannot use skip sampling")
            if self.evidence_id is not None or self.new_text is not None:
                raise ValueError("vision_rescan does not accept correction fields")
            object.__setattr__(self, "sampling", sampling)
        elif self.kind is OperationKind.ASR_RETRANSCRIBE:
            if self.sampling is not None:
                raise ValueError("asr_retranscribe does not accept visual sampling")
            if self.evidence_id is not None or self.new_text is not None:
                raise ValueError("asr_retranscribe does not accept correction fields")
            if not self.run_ocr:
                raise ValueError("run_ocr is only configurable for vision_rescan")
        else:
            if self.sampling is not None or self.language_hints:
                raise ValueError("evidence_correct does not accept model execution fields")
            if not self.run_ocr:
                raise ValueError("run_ocr is only configurable for vision_rescan")
            if self.evidence_id is None or not self.evidence_id.strip():
                raise ValueError("evidence_correct requires evidence_id")
            if self.new_text is None or not self.new_text.strip():
                raise ValueError("evidence_correct requires non-empty new_text")
            object.__setattr__(self, "evidence_id", self.evidence_id.strip())
            object.__setattr__(self, "new_text", self.new_text.strip())
            object.__setattr__(
                self,
                "reason",
                self.reason.strip() if self.reason and self.reason.strip() else None,
            )
        return self


class OperationRecord(OperationModel):
    schema_version: int = 1
    operation_id: str
    run_id: str
    request: OperationRequest
    status: OperationStatus
    created_at: datetime
    finished_at: datetime
    revision_id: str | None = None
    artifact_paths: list[str] = Field(default_factory=list)
    replaced_evidence_ids: list[str] = Field(default_factory=list)
    new_evidence_ids: list[str] = Field(default_factory=list)
    visual_state_count: int = Field(default=0, ge=0)
    error_type: str | None = None
    detail: str | None = None


class EvidenceRevision(OperationModel):
    schema_version: int = 1
    revision_id: str
    run_id: str
    operation_id: str
    parent_revision_id: str | None = None
    created_at: datetime
    all_evidence: list[EvidenceSpan]
    effective_evidence_ids: list[str]
    superseded_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_identity(self) -> Self:
        ids = [item.id for item in self.all_evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence revision contains duplicate evidence ids")
        known = set(ids)
        if not set(self.effective_evidence_ids).issubset(known):
            raise ValueError("effective evidence ids must exist in all_evidence")
        if not set(self.superseded_evidence_ids).issubset(known):
            raise ValueError("superseded evidence ids must exist in all_evidence")
        return self


class EvidenceView(OperationModel):
    schema_version: int = 1
    run_id: str
    revision_id: str | None = None
    operation_id: str | None = None
    evidence: list[EvidenceSpan]
    superseded_evidence_ids: list[str] = Field(default_factory=list)


class OperationConflictError(RuntimeError):
    """The run or configured runtime cannot execute the requested operation."""


class OperationInputError(ValueError):
    """The operation is well-formed JSON but invalid for this completed run."""


class OperationNotFoundError(KeyError):
    """A requested persisted revision does not exist."""


class RangeScanner(Protocol):
    def scan_range(
        self,
        source: str | Path,
        *,
        start_us: int,
        end_us: int,
        preview_dir: str | Path | None = None,
        probe: VideoProbe | None = None,
    ) -> ScanResult: ...


ScannerFactory = Callable[[AdaptiveScanConfig], RangeScanner]
FrameIterator = Callable[..., Iterator[DecodedVideoFrame]]
AudioWindowExtractor = Callable[..., AudioExtractionResult]
AsrTranscriber = Callable[..., ASREvidenceResult]


@dataclass(frozen=True, slots=True)
class _EvidenceSnapshot:
    revision_id: str | None
    all_evidence: list[EvidenceSpan]
    effective_evidence_ids: list[str]
    superseded_evidence_ids: list[str]


@dataclass(frozen=True, slots=True)
class _OperationOutput:
    new_evidence: list[EvidenceSpan]
    replacement_modalities: frozenset[EvidenceModality]
    artifact_paths: list[str]
    visual_state_count: int = 0


class OperationService:
    """Execute one operation synchronously and persist an auditable result."""

    def __init__(
        self,
        workspace: RunWorkspace,
        *,
        asr_backend: ASRBackend | None = None,
        ocr_backend: OcrBackend | None = None,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        scanner_factory: ScannerFactory | None = None,
        frame_iterator: FrameIterator = iter_video_frames,
        audio_window_extractor: AudioWindowExtractor = extract_audio_window,
        asr_transcriber: AsrTranscriber = transcribe_to_evidence,
        lock: threading.RLock | None = None,
    ) -> None:
        self.workspace = workspace
        self.asr_backend = asr_backend
        self.ocr_backend = ocr_backend
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self._scanner_factory = scanner_factory or self._default_scanner
        self._frame_iterator = frame_iterator
        self._audio_window_extractor = audio_window_extractor
        self._asr_transcriber = asr_transcriber
        self._lock = lock or threading.RLock()

    def execute(self, request: OperationRequest) -> OperationRecord:
        """Execute and index a request; runtime failures become failed records."""

        with self._lock:
            media = self._preflight(request)
            snapshot = self._load_current_snapshot()
            if request.kind is OperationKind.EVIDENCE_CORRECT:
                self._validate_correction_target(request, snapshot)

            operation_id = f"op-{uuid.uuid4().hex}"
            operation_dir = self._operation_dir(operation_id)
            operation_dir.mkdir(parents=True, exist_ok=False)
            created_at = datetime.now(UTC)
            _write_json_exclusive(
                operation_dir / "request.json",
                request.model_dump(mode="json"),
            )
            try:
                if request.kind is OperationKind.VISION_RESCAN:
                    if media is None:  # pragma: no cover - protected by preflight
                        raise RuntimeError("vision preflight did not load media")
                    output = self._execute_vision(
                        operation_id,
                        operation_dir,
                        request,
                        media,
                    )
                elif request.kind is OperationKind.ASR_RETRANSCRIBE:
                    output = self._execute_asr(
                        operation_id,
                        operation_dir,
                        request,
                    )
                else:
                    output = self._execute_correction(
                        operation_id,
                        request,
                        snapshot,
                    )

                revision, replaced_ids = self._build_revision(
                    operation_id,
                    request,
                    snapshot,
                    output,
                )
                self._persist_revision(revision)
                record = OperationRecord(
                    operation_id=operation_id,
                    run_id=self.workspace.manifest.run_id,
                    request=request,
                    status=OperationStatus.COMPLETED,
                    created_at=created_at,
                    finished_at=datetime.now(UTC),
                    revision_id=revision.revision_id,
                    artifact_paths=output.artifact_paths,
                    replaced_evidence_ids=replaced_ids,
                    new_evidence_ids=[item.id for item in output.new_evidence],
                    visual_state_count=output.visual_state_count,
                    detail="operation completed and evidence revision activated",
                )
                _write_json_exclusive(
                    operation_dir / "result.json",
                    record.model_dump(mode="json"),
                )
                self._activate_revision(revision)
            except Exception as error:
                record = OperationRecord(
                    operation_id=operation_id,
                    run_id=self.workspace.manifest.run_id,
                    request=request,
                    status=OperationStatus.FAILED,
                    created_at=created_at,
                    finished_at=datetime.now(UTC),
                    error_type=type(error).__name__,
                    detail="operation execution failed; no evidence revision was activated",
                )
                _write_json_exclusive(
                    operation_dir / "result.json",
                    record.model_dump(mode="json"),
                )
            self._append_operation(record)
            return record

    def list_operations(self) -> list[OperationRecord]:
        with self._lock:
            payload = _read_json_if_exists(
                self.workspace.root / "operations" / "index.json",
                default={"items": []},
            )
            raw_items = payload.get("items", [])
            if not isinstance(raw_items, list):
                raise OperationConflictError("operation index is invalid")
            return [OperationRecord.model_validate(item) for item in raw_items]

    def get_evidence(self, revision_id: str | None = None) -> EvidenceView:
        with self._lock:
            if revision_id is None:
                snapshot = self._load_current_snapshot()
                operation_id = None
                if snapshot.revision_id is not None:
                    revision = self._read_revision(snapshot.revision_id)
                    operation_id = revision.operation_id
            else:
                revision = self._read_revision(revision_id)
                snapshot = _EvidenceSnapshot(
                    revision_id=revision.revision_id,
                    all_evidence=revision.all_evidence,
                    effective_evidence_ids=revision.effective_evidence_ids,
                    superseded_evidence_ids=revision.superseded_evidence_ids,
                )
                operation_id = revision.operation_id

            by_id = {item.id: item for item in snapshot.all_evidence}
            effective = [
                by_id[item_id]
                for item_id in snapshot.effective_evidence_ids
                if item_id in by_id
            ]
            return EvidenceView(
                run_id=self.workspace.manifest.run_id,
                revision_id=snapshot.revision_id,
                operation_id=operation_id,
                evidence=sorted(
                    effective,
                    key=lambda item: (item.start_us, item.end_us, item.modality.value, item.id),
                ),
                superseded_evidence_ids=snapshot.superseded_evidence_ids,
            )

    def _preflight(self, request: OperationRequest) -> MediaManifest | None:
        if self.workspace.manifest.status is not RunStatus.COMPLETED:
            raise OperationConflictError("partial rework requires a completed run")

        media: MediaManifest | None = None
        if request.kind is OperationKind.VISION_RESCAN:
            if self.workspace.manifest.processing_scope is ProcessingScope.AUDIO_ONLY:
                raise OperationConflictError(
                    "audio-only runs do not permit visual rescan or OCR; "
                    "create a new audio-visual task to analyze the picture track"
                )
            media, _ = self._load_verified_media()
            if media.video_stream is None:
                raise OperationConflictError("completed run has no video stream")
            if request.run_ocr and self.ocr_backend is None:
                raise OperationConflictError(
                    "OCR backend is not configured; disable run_ocr or configure OCR"
                )
            sampling = request.sampling or SamplingSpec()
            if sampling.mode is SamplingMode.FIXED_INTERVAL:
                interval_us = sampling.interval_us
                if interval_us is None:  # pragma: no cover - model validator protects this
                    raise OperationInputError("fixed interval is missing interval_us")
                requested_count = (
                    (request.range.end_us - request.range.start_us - 1) // interval_us
                ) + 1
                if requested_count > MAX_FIXED_SAMPLES:
                    raise OperationInputError(
                        "fixed_interval rework would request "
                        f"{requested_count} frames; maximum is {MAX_FIXED_SAMPLES}"
                    )
        elif request.kind is OperationKind.ASR_RETRANSCRIBE:
            if self.asr_backend is None:
                raise OperationConflictError("ASR backend is not configured")
            extraction = self._load_verified_audio()
            available_start = extraction.output_time_zero_canonical_us
            available_end = available_start + extraction.duration_us
            if (
                request.range.start_us < available_start
                or request.range.end_us > available_end
            ):
                raise OperationInputError(
                    "ASR rework range is outside the extracted audio track"
                )
            media = self._load_verified_media()[0]
        else:
            self._load_base_evidence()

        if media is not None and request.range.end_us > media.duration_us:
            raise OperationInputError(
                "operation range ends after media duration "
                f"({request.range.end_us} > {media.duration_us})"
            )
        return media

    def _execute_vision(
        self,
        operation_id: str,
        operation_dir: Path,
        request: OperationRequest,
        media: MediaManifest,
    ) -> _OperationOutput:
        _, media_path = self._load_verified_media()
        keyframe_dir = operation_dir / "keyframes"
        sampling = request.sampling or SamplingSpec()
        if sampling.mode is SamplingMode.ADAPTIVE:
            scanner = self._scanner_factory(AdaptiveScanConfig())
            result = scanner.scan_range(
                media_path,
                start_us=request.range.start_us,
                end_us=request.range.end_us,
                preview_dir=keyframe_dir,
            )
            events = [
                replace(
                    item,
                    sampling_mode="adaptive",
                    segment_start_us=request.range.start_us,
                    segment_end_us=request.range.end_us,
                )
                for item in result.events
            ]
        elif sampling.mode is SamplingMode.FIXED_INTERVAL:
            events = self._fixed_events(
                media,
                media_path,
                request.range,
                sampling,
                keyframe_dir,
            )
        else:  # pragma: no cover - request model rejects skip
            raise OperationInputError("vision_rescan cannot use skip sampling")

        states = self._events_to_states(
            operation_id,
            events,
            selected_range=request.range,
            operation_dir=operation_dir,
        )
        scan_path = operation_dir / "vision.json"
        _write_json_exclusive(
            scan_path,
            {
                "schema_version": 1,
                "range": request.range.model_dump(mode="json"),
                "sampling": sampling.model_dump(mode="json"),
                "events": [
                    self._safe_event_payload(item, operation_dir=operation_dir)
                    for item in events
                ],
                "visual_states": [item.model_dump(mode="json") for item in states],
            },
        )
        artifacts = [
            self._relative(scan_path),
            *[
                state.keyframe_artifact.relative_path
                for state in states
                if state.keyframe_artifact is not None
            ],
        ]
        evidence: list[EvidenceSpan] = []
        replacement_modalities: frozenset[EvidenceModality] = frozenset()
        if request.run_ocr:
            if self.ocr_backend is None:  # pragma: no cover - protected by preflight
                raise OperationConflictError("OCR backend is not configured")
            bundle = extract_ocr_evidence(
                states,
                backend=self.ocr_backend,
                image_loader=FilesystemArtifactImageLoader(self.workspace.root),
                language_hints=request.language_hints,
            )
            evidence = [
                _scope_generated_evidence(item, operation_id=operation_id)
                for item in bundle.evidence
            ]
            ocr_path = operation_dir / "ocr.json"
            _write_json_exclusive(
                ocr_path,
                bundle.model_dump(mode="json"),
            )
            artifacts.append(self._relative(ocr_path))
            replacement_modalities = frozenset({EvidenceModality.OCR})
        return _OperationOutput(
            new_evidence=evidence,
            replacement_modalities=replacement_modalities,
            artifact_paths=artifacts,
            visual_state_count=len(states),
        )

    def _execute_asr(
        self,
        operation_id: str,
        operation_dir: Path,
        request: OperationRequest,
    ) -> _OperationOutput:
        extraction = self._load_verified_audio()
        if self.asr_backend is None:  # pragma: no cover - protected by preflight
            raise OperationConflictError("ASR backend is not configured")
        window_path = operation_dir / "audio-window.wav"
        window = self._audio_window_extractor(
            extraction,
            window_path,
            start_us=request.range.start_us,
            end_us=request.range.end_us,
            ffmpeg_path=self.ffmpeg_path,
        )
        if not window_path.is_file():
            raise FileNotFoundError("audio window extractor did not create its output")
        window_ref = self.workspace.ref_for(window_path, kind=ArtifactKind.AUDIO)
        language = request.language_hints[0] if len(request.language_hints) == 1 else None
        result = self._asr_transcriber(
            window,
            self.asr_backend,
            run_id=self.workspace.manifest.run_id,
            language=language,
            language_hints=request.language_hints,
        )
        if any(
            item.start_us < request.range.start_us
            or item.end_us > request.range.end_us
            for item in result.evidence
        ):
            raise ValueError("ASR backend returned evidence outside the selected range")
        evidence = [
            _scope_generated_evidence(
                item.model_copy(
                    update={"artifact_refs": [*item.artifact_refs, window_ref]}
                ),
                operation_id=operation_id,
            )
            for item in result.evidence
        ]
        result_path = operation_dir / "asr.json"
        _write_json_exclusive(
            result_path,
            {
                "schema_version": 1,
                "window": window.model_dump(mode="json"),
                "transcript": result.transcript.model_dump(mode="json"),
                "evidence": [item.model_dump(mode="json") for item in evidence],
            },
        )
        return _OperationOutput(
            new_evidence=evidence,
            replacement_modalities=frozenset({EvidenceModality.ASR}),
            artifact_paths=[self._relative(window_path), self._relative(result_path)],
        )

    def _execute_correction(
        self,
        operation_id: str,
        request: OperationRequest,
        snapshot: _EvidenceSnapshot,
    ) -> _OperationOutput:
        target_id = request.evidence_id
        new_text = request.new_text
        if target_id is None or new_text is None:  # pragma: no cover - model protects this
            raise OperationInputError("correction fields are missing")
        by_id = {item.id: item for item in snapshot.all_evidence}
        original = by_id[target_id]
        corrected = EvidenceSpan.model_validate(
            {
                **original.model_dump(mode="json"),
                "id": f"{operation_id}-correction",
                "raw_text": new_text,
                "normalized_text": " ".join(new_text.split()),
                "confidence": 1.0,
                "confidence_kind": "human_confirmed",
                "provider": "user",
                "model": "manual-correction",
                "version": "1",
                "correction_of": original.id,
                "provenance": {
                    **original.provenance,
                    "operation_id": operation_id,
                    "correction_reason": request.reason,
                },
            }
        )
        return _OperationOutput(
            new_evidence=[corrected],
            replacement_modalities=frozenset(),
            artifact_paths=[],
        )

    def _build_revision(
        self,
        operation_id: str,
        request: OperationRequest,
        snapshot: _EvidenceSnapshot,
        output: _OperationOutput,
    ) -> tuple[EvidenceRevision, list[str]]:
        revision_id = f"rev-{uuid.uuid4().hex}"
        by_id = {item.id: item for item in snapshot.all_evidence}
        effective = list(snapshot.effective_evidence_ids)
        appended: list[EvidenceSpan] = []
        replaced: list[str] = []

        if request.kind is OperationKind.EVIDENCE_CORRECT:
            target_id = request.evidence_id
            if target_id is None:  # pragma: no cover - model protects this
                raise OperationInputError("correction evidence id is missing")
            effective = [item_id for item_id in effective if item_id != target_id]
            replaced.append(target_id)
        else:
            retained_ids: list[str] = []
            for item_id in effective:
                item = by_id[item_id]
                if (
                    item.modality in output.replacement_modalities
                    and _overlaps(item, request.range)
                ):
                    replaced.append(item.id)
                    fragments = _outside_fragments(
                        item,
                        request.range,
                        revision_id=revision_id,
                    )
                    appended.extend(fragments)
                    retained_ids.extend(fragment.id for fragment in fragments)
                else:
                    retained_ids.append(item_id)
            effective = retained_ids

        for item in [*appended, *output.new_evidence]:
            if item.id in by_id:
                raise ValueError(f"generated evidence id already exists: {item.id}")
            by_id[item.id] = item
        effective.extend(item.id for item in output.new_evidence)
        all_evidence = [*snapshot.all_evidence, *appended, *output.new_evidence]
        superseded = list(
            dict.fromkeys([*snapshot.superseded_evidence_ids, *replaced])
        )
        return (
            EvidenceRevision(
                revision_id=revision_id,
                run_id=self.workspace.manifest.run_id,
                operation_id=operation_id,
                parent_revision_id=snapshot.revision_id,
                created_at=datetime.now(UTC),
                all_evidence=all_evidence,
                effective_evidence_ids=effective,
                superseded_evidence_ids=superseded,
            ),
            replaced,
        )

    def _fixed_events(
        self,
        media: MediaManifest,
        media_path: Path,
        selected_range: TimeRange,
        sampling: SamplingSpec,
        keyframe_dir: Path,
    ) -> list[ChangeEvent]:
        interval_us = sampling.interval_us
        stream = media.video_stream
        if interval_us is None or stream is None:
            raise OperationInputError("fixed sampling prerequisites are missing")
        keyframe_dir.mkdir(parents=True, exist_ok=True)
        events: list[ChangeEvent] = []
        previous = None
        frames = self._frame_iterator(
            media_path,
            timeline_origin_us=media.timeline_origin_us,
            stream_index=stream.index,
            sample_period_us=interval_us,
            start_us=selected_range.start_us,
            end_us=selected_range.end_us,
            target_size=None,
        )
        for index, decoded in enumerate(frames):
            if index >= MAX_FIXED_SAMPLES:
                raise OperationInputError(
                    f"fixed_interval exceeded runtime maximum of {MAX_FIXED_SAMPLES} frames"
                )
            requested = (
                decoded.requested_time_us
                if decoded.requested_time_us is not None
                else decoded.timestamp.time_us
            )
            preview = keyframe_dir / (
                f"{index:05d}_{requested:015d}req_"
                f"{decoded.timestamp.time_us:015d}us_fixed.jpg"
            )
            decoded.image.save(
                preview,
                format="JPEG",
                quality=95,
                subsampling=0,
            )
            events.append(
                ChangeEvent(
                    transition=decoded.timestamp,
                    keyframe=decoded.timestamp,
                    previous_keyframe=previous,
                    reason="fixed_interval",
                    state_score=0.0,
                    scene_score=0.0,
                    text_score=0.0,
                    step_score=0.0,
                    refined=False,
                    preview_path=str(preview),
                    sampling_mode="fixed_interval",
                    requested_time_us=requested,
                    requested_interval_us=interval_us,
                    segment_start_us=selected_range.start_us,
                    segment_end_us=selected_range.end_us,
                )
            )
            previous = decoded.timestamp
        return events

    def _events_to_states(
        self,
        operation_id: str,
        events: list[ChangeEvent],
        *,
        selected_range: TimeRange,
        operation_dir: Path,
    ) -> list[VisualState]:
        states: list[VisualState] = []
        for index, event in enumerate(events):
            next_transition = (
                min(events[index + 1].transition_us, selected_range.end_us)
                if index + 1 < len(events)
                else selected_range.end_us
            )
            start_us = max(selected_range.start_us, event.transition_us)
            end_us = max(event.keyframe_us, next_transition)
            end_us = min(selected_range.end_us, end_us)
            if end_us < start_us:
                raise ValueError("scanner returned an event outside the selected range")
            keyframe_ref: ArtifactRef | None = None
            if event.preview_path is not None:
                preview = Path(event.preview_path).expanduser().resolve()
                if (
                    not preview.is_relative_to(operation_dir)
                    or not preview.is_file()
                ):
                    raise ValueError("scanner preview escaped the immutable operation directory")
                keyframe_ref = self.workspace.ref_for(
                    preview,
                    kind=ArtifactKind.VISUAL,
                    media_type="image/jpeg",
                )
            states.append(
                VisualState(
                    id=f"{operation_id}-visual-state-{index:05d}",
                    run_id=self.workspace.manifest.run_id,
                    start_us=start_us,
                    end_us=end_us,
                    transition_us=start_us,
                    stable_keyframe_us=event.keyframe_us,
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
                        "operation_id": operation_id,
                    },
                )
            )
        return states

    def _validate_correction_target(
        self,
        request: OperationRequest,
        snapshot: _EvidenceSnapshot,
    ) -> None:
        target_id = request.evidence_id
        if target_id is None:  # pragma: no cover - model protects this
            raise OperationInputError("correction evidence id is missing")
        if target_id not in snapshot.effective_evidence_ids:
            raise OperationInputError(
                "evidence_correct can only target evidence in the effective revision"
            )
        by_id = {item.id: item for item in snapshot.all_evidence}
        if not _overlaps(by_id[target_id], request.range):
            raise OperationInputError(
                "correction target does not overlap the selected time range"
            )

    def _load_current_snapshot(self) -> _EvidenceSnapshot:
        index_path = self.workspace.root / "revisions" / "evidence" / "index.json"
        payload = _read_json_if_exists(
            index_path,
            default={"active_revision_id": None},
        )
        active = payload.get("active_revision_id")
        if active is None:
            evidence = self._load_base_evidence()
            ids = [item.id for item in evidence]
            if len(ids) != len(set(ids)):
                raise OperationConflictError("base evidence contains duplicate ids")
            return _EvidenceSnapshot(
                revision_id=None,
                all_evidence=evidence,
                effective_evidence_ids=ids,
                superseded_evidence_ids=[],
            )
        if not isinstance(active, str):
            raise OperationConflictError("evidence revision index is invalid")
        revision = self._read_revision(active)
        return _EvidenceSnapshot(
            revision_id=revision.revision_id,
            all_evidence=revision.all_evidence,
            effective_evidence_ids=revision.effective_evidence_ids,
            superseded_evidence_ids=revision.superseded_evidence_ids,
        )

    def _load_base_evidence(self) -> list[EvidenceSpan]:
        path = self._verified_stage_artifact(
            "evidence.fuse",
            "evidence/timeline.json",
        )
        payload = _read_json(path)
        raw = payload.get("evidence")
        if not isinstance(raw, list):
            raise OperationConflictError("base evidence timeline is invalid")
        evidence = [EvidenceSpan.model_validate(item) for item in raw]
        if any(item.run_id != self.workspace.manifest.run_id for item in evidence):
            raise OperationConflictError("base evidence belongs to another run")
        return evidence

    def _load_verified_media(self) -> tuple[MediaManifest, Path]:
        manifest_path = self._verified_stage_artifact(
            "media.probe",
            "media/media-manifest.json",
        )
        media = MediaManifest.model_validate(_read_json(manifest_path))
        media_path = Path(media.source_path).expanduser().resolve()
        media_root = (self.workspace.root / "media").resolve()
        if not media_path.is_relative_to(media_root) or not media_path.is_file():
            raise OperationConflictError("acquired media path is outside the run workspace")
        acquire_stage = self.workspace.manifest.stages.get("source.acquire")
        if acquire_stage is None:
            raise OperationConflictError("source acquisition record is missing")
        matching = [
            item
            for item in acquire_stage.outputs
            if item.kind is ArtifactKind.MEDIA
            and (self.workspace.root / item.relative_path).resolve() == media_path
        ]
        if not matching or not self.workspace.verify_ref(matching[0]):
            raise OperationConflictError("acquired media artifact failed verification")
        if media.source_sha256 != matching[0].sha256:
            raise OperationConflictError("media manifest digest does not match acquired media")
        return media, media_path

    def _load_verified_audio(self) -> AudioExtractionResult:
        manifest_path = self._verified_stage_artifact(
            "audio.extract",
            "audio/extraction.json",
        )
        payload = _read_json(manifest_path)
        raw = payload.get("extraction")
        if raw is None:
            raise OperationConflictError("completed run has no extracted audio track")
        extraction = AudioExtractionResult.model_validate(raw)
        audio_path = Path(extraction.output_path).expanduser().resolve()
        audio_root = (self.workspace.root / "audio").resolve()
        if not audio_path.is_relative_to(audio_root) or not audio_path.is_file():
            raise OperationConflictError("extracted audio path is outside the run workspace")
        stage = self.workspace.manifest.stages.get("audio.extract")
        if stage is None:
            raise OperationConflictError("audio extraction record is missing")
        matching = [
            item
            for item in stage.outputs
            if item.kind is ArtifactKind.AUDIO
            and (self.workspace.root / item.relative_path).resolve() == audio_path
        ]
        if not matching or not self.workspace.verify_ref(matching[0]):
            raise OperationConflictError("extracted audio artifact failed verification")
        return extraction

    def _verified_stage_artifact(self, stage_name: str, relative_path: str) -> Path:
        expected = (self.workspace.root / relative_path).resolve()
        if not expected.is_relative_to(self.workspace.root):
            raise OperationConflictError("artifact path escaped the run workspace")
        stage = self.workspace.manifest.stages.get(stage_name)
        if stage is None or stage.status.value != "completed":
            raise OperationConflictError(f"required completed stage is missing: {stage_name}")
        artifact = next(
            (
                item
                for item in stage.outputs
                if (self.workspace.root / item.relative_path).resolve() == expected
            ),
            None,
        )
        if artifact is None or not self.workspace.verify_ref(artifact):
            raise OperationConflictError(
                f"required artifact failed verification: {relative_path}"
            )
        return expected

    def _persist_revision(self, revision: EvidenceRevision) -> None:
        path = (
            self.workspace.root
            / "revisions"
            / "evidence"
            / f"{revision.revision_id}.json"
        )
        _write_json_exclusive(path, revision.model_dump(mode="json"))

    def _activate_revision(self, revision: EvidenceRevision) -> None:
        index_path = self.workspace.root / "revisions" / "evidence" / "index.json"
        payload = _read_json_if_exists(
            index_path,
            default={
                "schema_version": 1,
                "active_revision_id": None,
                "revisions": [],
            },
        )
        revisions = payload.get("revisions", [])
        if not isinstance(revisions, list):
            raise OperationConflictError("evidence revision index is invalid")
        revisions.append(
            {
                "revision_id": revision.revision_id,
                "operation_id": revision.operation_id,
                "parent_revision_id": revision.parent_revision_id,
                "created_at": revision.created_at.isoformat(),
            }
        )
        _atomic_write_json(
            index_path,
            {
                "schema_version": 1,
                "active_revision_id": revision.revision_id,
                "revisions": revisions,
            },
        )

    def _read_revision(self, revision_id: str) -> EvidenceRevision:
        if not _SAFE_PERSISTED_ID.fullmatch(revision_id):
            raise OperationNotFoundError(revision_id)
        path = (
            self.workspace.root / "revisions" / "evidence" / f"{revision_id}.json"
        ).resolve()
        root = (self.workspace.root / "revisions" / "evidence").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise OperationNotFoundError(revision_id)
        revision = EvidenceRevision.model_validate(_read_json(path))
        if revision.run_id != self.workspace.manifest.run_id:
            raise OperationConflictError("evidence revision belongs to another run")
        return revision

    def _append_operation(self, record: OperationRecord) -> None:
        index_path = self.workspace.root / "operations" / "index.json"
        payload = _read_json_if_exists(
            index_path,
            default={"schema_version": 1, "items": []},
        )
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise OperationConflictError("operation index is invalid")
        items.append(record.model_dump(mode="json"))
        _atomic_write_json(
            index_path,
            {"schema_version": 1, "items": items},
        )

    def _operation_dir(self, operation_id: str) -> Path:
        if not _SAFE_PERSISTED_ID.fullmatch(operation_id):
            raise ValueError("unsafe operation id")
        candidate = (self.workspace.root / "operations" / operation_id).resolve()
        root = (self.workspace.root / "operations").resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("operation path escaped the run workspace")
        return candidate

    def _safe_event_payload(
        self,
        event: ChangeEvent,
        *,
        operation_dir: Path,
    ) -> dict[str, object]:
        payload = event.to_dict()
        if event.preview_path is not None:
            preview = Path(event.preview_path).expanduser().resolve()
            if not preview.is_relative_to(operation_dir):
                raise ValueError("scanner preview escaped the operation directory")
            payload["preview_path"] = self._relative(preview)
        return payload

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.workspace.root):
            raise ValueError("operation artifact escaped the run workspace")
        return resolved.relative_to(self.workspace.root).as_posix()

    def _default_scanner(self, config: AdaptiveScanConfig) -> AdaptiveVideoScanner:
        return AdaptiveVideoScanner(
            config,
            ffmpeg_path=self.ffmpeg_path,
            ffprobe_path=self.ffprobe_path,
        )


def _scope_generated_evidence(
    evidence: EvidenceSpan,
    *,
    operation_id: str,
) -> EvidenceSpan:
    return EvidenceSpan.model_validate(
        {
            **evidence.model_dump(mode="json"),
            "id": f"{operation_id}-{evidence.id}",
            "provenance": {
                **evidence.provenance,
                "source_evidence_id": evidence.id,
                "operation_id": operation_id,
            },
        }
    )


def _overlaps(evidence: EvidenceSpan, selected_range: TimeRange) -> bool:
    if evidence.start_us == evidence.end_us:
        return selected_range.start_us <= evidence.start_us < selected_range.end_us
    return (
        evidence.start_us < selected_range.end_us
        and selected_range.start_us < evidence.end_us
    )


def _outside_fragments(
    evidence: EvidenceSpan,
    selected_range: TimeRange,
    *,
    revision_id: str,
) -> list[EvidenceSpan]:
    fragments: list[EvidenceSpan] = []
    if evidence.start_us < selected_range.start_us:
        fragments.append(
            _fragment(
                evidence,
                start_us=evidence.start_us,
                end_us=min(evidence.end_us, selected_range.start_us),
                suffix="left",
                revision_id=revision_id,
            )
        )
    if evidence.end_us > selected_range.end_us:
        fragments.append(
            _fragment(
                evidence,
                start_us=max(evidence.start_us, selected_range.end_us),
                end_us=evidence.end_us,
                suffix="right",
                revision_id=revision_id,
            )
        )
    return [item for item in fragments if item.end_us >= item.start_us]


def _fragment(
    evidence: EvidenceSpan,
    *,
    start_us: int,
    end_us: int,
    suffix: str,
    revision_id: str,
) -> EvidenceSpan:
    return EvidenceSpan.model_validate(
        {
            **evidence.model_dump(mode="json"),
            "id": f"{revision_id}-{suffix}-{evidence.id}",
            "start_us": start_us,
            "end_us": end_us,
            "provenance": {
                **evidence.provenance,
                "clipped_from": evidence.id,
                "clipped_for_revision": revision_id,
            },
        }
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise OperationConflictError(
            f"invalid persisted operation artifact: {path.name}"
        ) from error
    if not isinstance(payload, dict):
        raise OperationConflictError(f"persisted operation artifact is not an object: {path.name}")
    return payload


def _read_json_if_exists(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return default
    return _read_json(path)


def _write_json_exclusive(path: Path, payload: Any) -> None:
    if path.exists():
        raise FileExistsError(f"immutable operation artifact already exists: {path.name}")
    _atomic_write_json(path, payload, replace_existing=False)


def _atomic_write_json(
    path: Path,
    payload: Any,
    *,
    replace_existing: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        if not replace_existing and path.exists():
            raise FileExistsError(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
