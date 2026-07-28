"""Track screen text across adjacent visual states."""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from typing import Self

from pydantic import Field, model_validator

from .backend import normalize_ocr_text, tokenize_ocr_text
from .models import (
    OcrBox,
    OcrLine,
    OcrLineAssignment,
    OcrLineMatch,
    OcrModel,
    OcrResult,
    OcrStateDelta,
    OcrTrackingResult,
)


class OcrTrackingConfig(OcrModel):
    iou_weight: float = Field(default=0.55, ge=0, le=1)
    text_weight: float = Field(default=0.45, ge=0, le=1)
    minimum_iou: float = Field(default=0.05, ge=0, le=1)
    minimum_text_similarity: float = Field(default=0.20, ge=0, le=1)
    minimum_combined_score: float = Field(default=0.30, ge=0, le=1)

    @model_validator(mode="after")
    def validate_weights(self) -> Self:
        if abs(self.iou_weight + self.text_weight - 1.0) > 1e-9:
            raise ValueError("OCR tracking weights must sum to 1")
        return self


def box_iou(left: OcrBox, right: OcrBox) -> float:
    """Calculate intersection-over-union for boxes in the same coordinate space."""

    if left.coordinate_space != right.coordinate_space:
        return 0.0
    intersection_width = max(0.0, min(left.right, right.right) - max(left.x, right.x))
    intersection_height = max(
        0.0,
        min(left.bottom, right.bottom) - max(left.y, right.y),
    )
    intersection = intersection_width * intersection_height
    if intersection <= 0:
        return 0.0
    union = left.width * left.height + right.width * right.height - intersection
    return min(1.0, intersection / union) if union > 0 else 0.0


def normalized_edit_similarity(left: str, right: str) -> float:
    """Compare normalized OCR text without discarding the auditable raw strings."""

    normalized_left = normalize_ocr_text(left).casefold()
    normalized_right = normalize_ocr_text(right).casefold()
    if not normalized_left and not normalized_right:
        return 1.0
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def track_ocr_lines(
    results: list[OcrResult],
    *,
    config: OcrTrackingConfig | None = None,
) -> OcrTrackingResult:
    """Greedily match accepted lines in each pair of adjacent visual states."""

    settings = config or OcrTrackingConfig()
    ordered = sorted(
        results,
        key=lambda item: (item.keyframe_us, item.visual_state_id, item.id),
    )
    if not ordered:
        return OcrTrackingResult()

    seen_line_ids: set[str] = set()
    for result in ordered:
        for line in result.accepted_lines:
            if line.id in seen_line_ids:
                raise ValueError(f"duplicate OCR line id: {line.id}")
            seen_line_ids.add(line.id)

    assignments: list[OcrLineAssignment] = []
    deltas: list[OcrStateDelta] = []
    line_tracks: dict[str, str] = {}
    next_track = 1

    for line in ordered[0].accepted_lines:
        track_id = f"ocr-track-{next_track:05d}"
        next_track += 1
        line_tracks[line.id] = track_id
        assignments.append(
            OcrLineAssignment(
                visual_state_id=ordered[0].visual_state_id,
                line_id=line.id,
                track_id=track_id,
            )
        )

    for previous, current in zip(ordered, ordered[1:], strict=False):
        previous_lines = previous.accepted_lines
        current_lines = current.accepted_lines
        candidate_matches: list[tuple[float, float, float, OcrLine, OcrLine]] = []
        for previous_line in previous_lines:
            for current_line in current_lines:
                iou = box_iou(previous_line.box, current_line.box)
                similarity = normalized_edit_similarity(
                    previous_line.normalized_text,
                    current_line.normalized_text,
                )
                combined = settings.iou_weight * iou + settings.text_weight * similarity
                if (
                    iou >= settings.minimum_iou
                    and similarity >= settings.minimum_text_similarity
                    and combined >= settings.minimum_combined_score
                ):
                    candidate_matches.append(
                        (combined, iou, similarity, previous_line, current_line)
                    )

        candidate_matches.sort(
            key=lambda item: (
                -item[0],
                -item[1],
                -item[2],
                item[3].id,
                item[4].id,
            )
        )
        matched_previous: set[str] = set()
        matched_current: set[str] = set()
        matches: list[OcrLineMatch] = []
        added_tokens: list[str] = []
        removed_tokens: list[str] = []

        for combined, iou, similarity, previous_line, current_line in candidate_matches:
            if previous_line.id in matched_previous or current_line.id in matched_current:
                continue
            matched_previous.add(previous_line.id)
            matched_current.add(current_line.id)
            track_id = line_tracks[previous_line.id]
            line_tracks[current_line.id] = track_id
            line_added, line_removed = _token_delta(
                previous_line.normalized_text,
                current_line.normalized_text,
            )
            added_tokens.extend(line_added)
            removed_tokens.extend(line_removed)
            matches.append(
                OcrLineMatch(
                    previous_line_id=previous_line.id,
                    current_line_id=current_line.id,
                    track_id=track_id,
                    box_iou=iou,
                    text_similarity=similarity,
                    combined_score=combined,
                    added_tokens=line_added,
                    removed_tokens=line_removed,
                )
            )

        added_lines = [line for line in current_lines if line.id not in matched_current]
        removed_lines = [line for line in previous_lines if line.id not in matched_previous]
        for line in added_lines:
            track_id = f"ocr-track-{next_track:05d}"
            next_track += 1
            line_tracks[line.id] = track_id
            added_tokens.extend(tokenize_ocr_text(line.normalized_text))
        for line in removed_lines:
            removed_tokens.extend(tokenize_ocr_text(line.normalized_text))

        for line in current_lines:
            assignments.append(
                OcrLineAssignment(
                    visual_state_id=current.visual_state_id,
                    line_id=line.id,
                    track_id=line_tracks[line.id],
                )
            )
        deltas.append(
            OcrStateDelta(
                previous_state_id=previous.visual_state_id,
                current_state_id=current.visual_state_id,
                matches=matches,
                added_line_ids=[line.id for line in added_lines],
                removed_line_ids=[line.id for line in removed_lines],
                added_tokens=added_tokens,
                removed_tokens=removed_tokens,
            )
        )

    return OcrTrackingResult(assignments=assignments, deltas=deltas)


def _token_delta(previous: str, current: str) -> tuple[list[str], list[str]]:
    previous_tokens = tokenize_ocr_text(previous)
    current_tokens = tokenize_ocr_text(current)
    previous_counts = Counter(previous_tokens)
    current_counts = Counter(current_tokens)
    added_counts = current_counts - previous_counts
    removed_counts = previous_counts - current_counts
    added = _tokens_in_source_order(current_tokens, added_counts)
    removed = _tokens_in_source_order(previous_tokens, removed_counts)
    return added, removed


def _tokens_in_source_order(
    source: list[str],
    remaining: Counter[str],
) -> list[str]:
    output: list[str] = []
    for token in source:
        if remaining[token] > 0:
            output.append(token)
            remaining[token] -= 1
    return output
