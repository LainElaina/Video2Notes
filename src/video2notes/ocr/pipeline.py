"""Convert visual-state keyframes into conservative, PTS-bound OCR evidence."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from PIL import Image
from pydantic import Field

from video2notes.domain import (
    ArtifactRef,
    BoundingBox,
    EvidenceModality,
    EvidenceSpan,
    VisualState,
)

from .backend import OcrBackend, detect_script, image_readability, normalize_ocr_text
from .models import (
    BackendOcrLine,
    OcrAbstention,
    OcrAbstentionScope,
    OcrBox,
    OcrEvidenceBundle,
    OcrFrameStatus,
    OcrLine,
    OcrLineDecision,
    OcrModel,
    OcrModelInvocation,
    OcrResult,
    OcrTrackingResult,
)
from .selection import ScrollSelectionConfig, select_scroll_keyframes
from .tracking import (
    OcrTrackingConfig,
    boxes_share_semantic_region,
    normalized_edit_similarity,
    track_ocr_lines,
)


class OcrPipelineConfig(OcrModel):
    minimum_confidence: float = Field(default=0.70, ge=0, le=1)
    minimum_crop_readability: float = Field(default=0.035, ge=0, le=1)
    minimum_crop_width: int = Field(default=8, ge=1)
    minimum_crop_height: int = Field(default=6, ge=1)
    require_exact_pts: bool = True
    inference_max_width: int | None = Field(default=None, ge=64)
    dedup_text_similarity: float = Field(default=0.88, ge=0, le=1)


class OcrArtifactError(RuntimeError):
    """Raised when an artifact path or digest violates the evidence contract."""


class FilesystemArtifactImageLoader:
    """Load and verify a keyframe inside one run's artifact directory."""

    def __init__(self, artifact_root: str | Path) -> None:
        self._root = Path(artifact_root).expanduser().resolve()

    def __call__(self, artifact: ArtifactRef) -> Image.Image:
        candidate = (self._root / artifact.relative_path).resolve()
        if not candidate.is_relative_to(self._root):
            raise OcrArtifactError("keyframe path escapes the run artifact directory")
        if not candidate.is_file():
            raise OcrArtifactError(f"keyframe artifact does not exist: {artifact.relative_path}")
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest != artifact.sha256:
            raise OcrArtifactError(f"keyframe digest mismatch for {artifact.relative_path}")
        try:
            with Image.open(candidate) as source:
                return source.convert("RGB").copy()
        except OSError as error:
            raise OcrArtifactError(
                f"keyframe is not a readable image: {artifact.relative_path}"
            ) from error


def extract_ocr_evidence(
    visual_states: Iterable[VisualState],
    *,
    backend: OcrBackend,
    image_loader: Callable[[ArtifactRef], Image.Image],
    config: OcrPipelineConfig | None = None,
    language_hints: Sequence[str] = (),
    tracking_config: OcrTrackingConfig | None = None,
    selection_config: ScrollSelectionConfig | None = None,
) -> OcrEvidenceBundle:
    """Run OCR only on adaptive visual-state keyframes, never on a fixed cadence."""

    settings = config or OcrPipelineConfig()
    states = sorted(
        visual_states,
        key=lambda item: (item.stable_keyframe_us, item.id),
    )
    results: list[OcrResult] = []
    evidence: list[EvidenceSpan] = []
    for state in states:
        result, spans = _process_state(
            state,
            backend=backend,
            image_loader=image_loader,
            settings=settings,
            language_hints=language_hints,
        )
        results.append(result)
        evidence.extend(spans)

    tracking = track_ocr_lines(results, config=tracking_config)
    evidence = _merge_tracked_evidence(
        evidence,
        results=results,
        tracking=tracking,
        minimum_text_similarity=settings.dedup_text_similarity,
    )
    selection = select_scroll_keyframes(results, config=selection_config)
    return OcrEvidenceBundle(
        results=results,
        evidence=evidence,
        tracking=tracking,
        scroll_selection=selection,
    )


def _process_state(
    state: VisualState,
    *,
    backend: OcrBackend,
    image_loader: Callable[[ArtifactRef], Image.Image],
    settings: OcrPipelineConfig,
    language_hints: Sequence[str],
) -> tuple[OcrResult, list[EvidenceSpan]]:
    result_id = f"ocr-{state.id}"
    if state.keyframe_artifact is None:
        return (
            _abstained_result(
                state,
                result_id=result_id,
                reason="missing_keyframe_artifact",
            ),
            [],
        )
    if settings.require_exact_pts and (
        state.keyframe_pts is None or state.stream_time_base is None
    ):
        return (
            _abstained_result(
                state,
                result_id=result_id,
                reason="missing_exact_keyframe_timestamp",
                detail="raw PTS and stream time base are required for OCR evidence",
            ),
            [],
        )
    try:
        image = image_loader(state.keyframe_artifact)
    except (OSError, OcrArtifactError, ValueError) as error:
        return (
            _abstained_result(
                state,
                result_id=result_id,
                reason="unreadable_keyframe_artifact",
                detail=f"{type(error).__name__}: {error}",
            ),
            [],
        )

    inference_image = _inference_image(image, maximum_width=settings.inference_max_width)
    output = backend.recognize(inference_image, language_hints=language_hints)
    frame_readability = image_readability(image)
    lines: list[OcrLine] = []
    abstentions: list[OcrAbstention] = []
    spans: list[EvidenceSpan] = []
    for index, backend_candidate in enumerate(output.lines):
        candidate = _map_candidate_to_original(
            backend_candidate,
            inference_size=inference_image.size,
            original_size=image.size,
        )
        line_id = f"{result_id}-line-{index:04d}"
        line = _evaluate_line(
            candidate,
            line_id=line_id,
            image=image,
            settings=settings,
            language_hints=language_hints,
        )
        lines.append(line)
        if line.decision is OcrLineDecision.ABSTAINED:
            abstentions.append(
                OcrAbstention(
                    scope=OcrAbstentionScope.LINE,
                    reason=line.abstain_reason or "unspecified_rejection",
                    line_id=line.id,
                )
            )
            continue
        spans.append(
            _line_to_evidence(
                state,
                line=line,
                invocation=output.invocation,
                result_id=result_id,
                frame_size=image.size,
                inference_size=inference_image.size,
            )
        )

    return (
        OcrResult(
            id=result_id,
            run_id=state.run_id,
            visual_state_id=state.id,
            keyframe_artifact=state.keyframe_artifact,
            keyframe_pts=state.keyframe_pts,
            keyframe_time_base=state.stream_time_base,
            keyframe_us=state.stable_keyframe_us,
            state_start_us=state.start_us,
            state_end_us=state.end_us,
            image_width=image.width,
            image_height=image.height,
            frame_readability=frame_readability,
            status=OcrFrameStatus.PROCESSED,
            lines=lines,
            abstentions=abstentions,
            invocation=output.invocation,
        ),
        spans,
    )


def _evaluate_line(
    candidate: BackendOcrLine,
    *,
    line_id: str,
    image: Image.Image,
    settings: OcrPipelineConfig,
    language_hints: Sequence[str],
) -> OcrLine:
    normalized = normalize_ocr_text(candidate.raw_text)
    crop, valid_box = _crop_for_box(image, candidate)
    readability = image_readability(crop) if crop is not None else 0.0
    reason: str | None = None
    if not normalized:
        reason = "empty_text"
    elif candidate.confidence < settings.minimum_confidence:
        reason = "low_confidence"
    elif not valid_box:
        reason = "invalid_or_outside_box"
    elif crop is None or (
        crop.width < settings.minimum_crop_width or crop.height < settings.minimum_crop_height
    ):
        reason = "crop_too_small"
    elif readability < settings.minimum_crop_readability:
        reason = "unreadable_crop"

    language = candidate.language
    if language is None and len(language_hints) == 1:
        language = language_hints[0]
    return OcrLine(
        id=line_id,
        raw_text=candidate.raw_text,
        normalized_text=normalized,
        box=candidate.box,
        script=candidate.script or detect_script(normalized),
        language=language,
        confidence=candidate.confidence,
        crop_readability=readability,
        decision=(OcrLineDecision.ABSTAINED if reason is not None else OcrLineDecision.ACCEPTED),
        abstain_reason=reason,
    )


def _crop_for_box(
    image: Image.Image,
    candidate: BackendOcrLine,
) -> tuple[Image.Image | None, bool]:
    box = candidate.box
    if box.coordinate_space == "normalized":
        left = box.x * image.width
        top = box.y * image.height
        right = box.right * image.width
        bottom = box.bottom * image.height
    elif box.coordinate_space == "pixels":
        left, top, right, bottom = box.x, box.y, box.right, box.bottom
    else:
        return None, False
    clamped_left = max(0, min(image.width, math.floor(left)))
    clamped_top = max(0, min(image.height, math.floor(top)))
    clamped_right = max(0, min(image.width, math.ceil(right)))
    clamped_bottom = max(0, min(image.height, math.ceil(bottom)))
    if clamped_right <= clamped_left or clamped_bottom <= clamped_top:
        return None, False
    return (
        image.crop((clamped_left, clamped_top, clamped_right, clamped_bottom)),
        True,
    )


def _inference_image(image: Image.Image, *, maximum_width: int | None) -> Image.Image:
    if maximum_width is None or image.width <= maximum_width:
        return image
    target_height = max(1, round(image.height * maximum_width / image.width))
    return image.resize((maximum_width, target_height), Image.Resampling.LANCZOS)


def _map_candidate_to_original(
    candidate: BackendOcrLine,
    *,
    inference_size: tuple[int, int],
    original_size: tuple[int, int],
) -> BackendOcrLine:
    if inference_size == original_size or candidate.box.coordinate_space != "pixels":
        return candidate
    scale_x = original_size[0] / inference_size[0]
    scale_y = original_size[1] / inference_size[1]
    box = candidate.box
    return candidate.model_copy(
        update={
            "box": box.model_copy(
                update={
                    "x": box.x * scale_x,
                    "y": box.y * scale_y,
                    "width": box.width * scale_x,
                    "height": box.height * scale_y,
                }
            )
        }
    )


def _line_to_evidence(
    state: VisualState,
    *,
    line: OcrLine,
    invocation: OcrModelInvocation,
    result_id: str,
    frame_size: tuple[int, int],
    inference_size: tuple[int, int],
) -> EvidenceSpan:
    time_base = (
        state.stream_time_base.model_dump(mode="json")
        if state.stream_time_base is not None
        else None
    )
    return EvidenceSpan(
        id=f"{line.id}-evidence",
        run_id=state.run_id,
        modality=EvidenceModality.OCR,
        start_us=state.start_us,
        end_us=state.end_us,
        language=line.language,
        raw_text=line.raw_text,
        normalized_text=line.normalized_text,
        confidence=line.confidence,
        confidence_kind="ocr_engine_line",
        provider=invocation.backend,
        model=invocation.engine,
        version=invocation.version,
        artifact_refs=([state.keyframe_artifact] if state.keyframe_artifact is not None else []),
        bounding_boxes=[
            BoundingBox(
                x=line.box.x,
                y=line.box.y,
                width=line.box.width,
                height=line.box.height,
                coordinate_space=line.box.coordinate_space,
            )
        ],
        provenance={
            "ocr_result_id": result_id,
            "ocr_line_id": line.id,
            "visual_state_id": state.id,
            "keyframe_pts": state.keyframe_pts,
            "keyframe_time_base": time_base,
            "keyframe_us": state.stable_keyframe_us,
            "script": line.script,
            "crop_readability": line.crop_readability,
            "local_models_only": invocation.local_models_only,
            "frame_width": frame_size[0],
            "frame_height": frame_size[1],
            "inference_width": inference_size[0],
            "inference_height": inference_size[1],
        },
    )


def _merge_tracked_evidence(
    evidence: list[EvidenceSpan],
    *,
    results: list[OcrResult],
    tracking: OcrTrackingResult,
    minimum_text_similarity: float,
) -> list[EvidenceSpan]:
    """Collapse stable adjacent observations while retaining every source trace."""

    evidence_by_line = {
        str(item.provenance.get("ocr_line_id")): item
        for item in evidence
        if item.provenance.get("ocr_line_id") is not None
    }
    state_order = {item.visual_state_id: index for index, item in enumerate(results)}
    line_state = {
        line.id: result.visual_state_id
        for result in results
        for line in result.accepted_lines
    }
    by_track: dict[str, list[EvidenceSpan]] = {}
    unassigned: list[EvidenceSpan] = []
    for assignment in tracking.assignments:
        span = evidence_by_line.get(assignment.line_id)
        if span is None:
            continue
        by_track.setdefault(assignment.track_id, []).append(span)
    assigned_ids = {item.id for spans in by_track.values() for item in spans}
    unassigned.extend(item for item in evidence if item.id not in assigned_ids)

    merged: list[EvidenceSpan] = []
    for track_id, spans in sorted(by_track.items()):
        ordered = sorted(
            spans,
            key=lambda item: (
                state_order.get(line_state.get(str(item.provenance.get("ocr_line_id")), ""), -1),
                item.start_us,
                item.id,
            ),
        )
        group: list[EvidenceSpan] = []
        previous_state_index: int | None = None
        for span in ordered:
            line_id = str(span.provenance.get("ocr_line_id", ""))
            current_state_index = state_order.get(line_state.get(line_id, ""), -1)
            if group and (
                previous_state_index is None
                or current_state_index != previous_state_index + 1
                or not _mergeable_ocr_observations(
                    group[-1],
                    span,
                    minimum_text_similarity=minimum_text_similarity,
                )
            ):
                merged.append(_merge_evidence_group(group, track_id=track_id))
                group = []
            group.append(span)
            previous_state_index = current_state_index
        if group:
            merged.append(_merge_evidence_group(group, track_id=track_id))

    merged.extend(unassigned)
    return sorted(merged, key=lambda item: (item.start_us, item.end_us, item.id))


def _mergeable_ocr_observations(
    left: EvidenceSpan,
    right: EvidenceSpan,
    *,
    minimum_text_similarity: float,
) -> bool:
    if right.start_us > left.end_us:
        return False
    if not left.bounding_boxes or not right.bounding_boxes:
        return False
    if not boxes_share_semantic_region(
        OcrBox.model_validate(left.bounding_boxes[0].model_dump()),
        OcrBox.model_validate(right.bounding_boxes[0].model_dump()),
    ):
        return False
    left_text = left.normalized_text or left.raw_text or ""
    right_text = right.normalized_text or right.raw_text or ""
    return normalized_edit_similarity(left_text, right_text) >= minimum_text_similarity


def _merge_evidence_group(
    group: list[EvidenceSpan],
    *,
    track_id: str,
) -> EvidenceSpan:
    best = max(
        group,
        key=lambda item: (
            item.confidence if item.confidence is not None else -1.0,
            len(item.normalized_text or item.raw_text or ""),
            -item.start_us,
        ),
    )
    artifact_refs: list[ArtifactRef] = []
    seen_artifacts: set[tuple[str, str]] = set()
    for item in group:
        for artifact in item.artifact_refs:
            key = (artifact.relative_path, artifact.sha256)
            if key not in seen_artifacts:
                seen_artifacts.add(key)
                artifact_refs.append(artifact)
    observations = [
        {
            "evidence_id": item.id,
            "start_us": item.start_us,
            "end_us": item.end_us,
            "raw_text": item.raw_text,
            "normalized_text": item.normalized_text,
            "confidence": item.confidence,
            "artifact_refs": [ref.model_dump(mode="json") for ref in item.artifact_refs],
            "bounding_boxes": [box.model_dump(mode="json") for box in item.bounding_boxes],
            "provenance": item.provenance,
        }
        for item in group
    ]
    source_evidence_ids = [item.id for item in group]
    provenance = dict(best.provenance)
    provenance.update(
        {
            "ocr_track_id": track_id,
            "observation_count": len(group),
            "representative_evidence_id": best.id,
            "source_evidence_ids": source_evidence_ids,
            "observations": observations,
        }
    )
    confidences = [item.confidence for item in group if item.confidence is not None]
    evidence_id = (
        _aggregate_evidence_id(track_id, source_evidence_ids)
        if len(group) > 1
        else group[0].id
    )
    return best.model_copy(
        update={
            "id": evidence_id,
            "start_us": min(item.start_us for item in group),
            "end_us": max(item.end_us for item in group),
            "confidence": max(confidences) if confidences else None,
            "confidence_kind": "ocr_engine_line_max_over_track",
            "artifact_refs": artifact_refs,
            "bounding_boxes": [
                box
                for item in group
                for box in item.bounding_boxes
            ],
            "provenance": provenance,
        }
    )


def _aggregate_evidence_id(track_id: str, source_evidence_ids: Sequence[str]) -> str:
    """Build a stable identity without impersonating one source observation."""

    digest = hashlib.sha256("\n".join(source_evidence_ids).encode("utf-8")).hexdigest()[:16]
    return f"{track_id}-aggregate-{digest}-evidence"


def _abstained_result(
    state: VisualState,
    *,
    result_id: str,
    reason: str,
    detail: str | None = None,
) -> OcrResult:
    return OcrResult(
        id=result_id,
        run_id=state.run_id,
        visual_state_id=state.id,
        keyframe_artifact=state.keyframe_artifact,
        keyframe_pts=state.keyframe_pts,
        keyframe_time_base=state.stream_time_base,
        keyframe_us=state.stable_keyframe_us,
        state_start_us=state.start_us,
        state_end_us=state.end_us,
        status=OcrFrameStatus.ABSTAINED,
        abstentions=[
            OcrAbstention(
                scope=OcrAbstentionScope.FRAME,
                reason=reason,
                detail=detail,
            )
        ],
    )
