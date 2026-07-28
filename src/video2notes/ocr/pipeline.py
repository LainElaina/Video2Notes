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
    OcrEvidenceBundle,
    OcrFrameStatus,
    OcrLine,
    OcrLineDecision,
    OcrModel,
    OcrModelInvocation,
    OcrResult,
)
from .selection import ScrollSelectionConfig, select_scroll_keyframes
from .tracking import OcrTrackingConfig, track_ocr_lines


class OcrPipelineConfig(OcrModel):
    minimum_confidence: float = Field(default=0.70, ge=0, le=1)
    minimum_crop_readability: float = Field(default=0.035, ge=0, le=1)
    minimum_crop_width: int = Field(default=8, ge=1)
    minimum_crop_height: int = Field(default=6, ge=1)
    require_exact_pts: bool = True


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

    output = backend.recognize(image, language_hints=language_hints)
    frame_readability = image_readability(image)
    lines: list[OcrLine] = []
    abstentions: list[OcrAbstention] = []
    spans: list[EvidenceSpan] = []
    for index, candidate in enumerate(output.lines):
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


def _line_to_evidence(
    state: VisualState,
    *,
    line: OcrLine,
    invocation: OcrModelInvocation,
    result_id: str,
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
        },
    )


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
