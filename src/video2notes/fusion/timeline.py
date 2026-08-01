"""Interval-overlap evidence fusion with explicit conflict provenance."""

from __future__ import annotations

import heapq
import math
import re
from collections.abc import Iterator
from difflib import SequenceMatcher
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from video2notes.domain import BoundingBox, EvidenceModality, EvidenceSpan, VisualState


class FusionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LinkRelation(StrEnum):
    SAME_SIGNAL_CANDIDATE = "same_signal_candidate"
    CO_TEMPORAL_COMPLEMENT = "co_temporal_complement"
    VISUAL_CONTEXT = "visual_context"


class ConflictKind(StrEnum):
    TRANSCRIPT_DISAGREEMENT = "transcript_disagreement"
    OCR_DISAGREEMENT = "ocr_disagreement"


class EvidenceLink(FusionModel):
    left_id: str
    right_id: str
    relation: LinkRelation
    overlap_us: int = Field(gt=0)
    overlap_ratio: float = Field(gt=0, le=1)


class EvidenceConflict(FusionModel):
    left_id: str
    right_id: str
    kind: ConflictKind
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    text_similarity: float = Field(ge=0, le=1)
    severity: float = Field(ge=0, le=1)
    requires_secondary: bool
    resolution: str = "unresolved"

    @model_validator(mode="after")
    def validate_interval(self) -> EvidenceConflict:
        if self.end_us < self.start_us:
            raise ValueError("conflict end_us cannot be before start_us")
        return self


class EvidenceWindow(FusionModel):
    id: str
    run_id: str
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    evidence_ids: list[str]
    visual_state_ids: list[str] = Field(default_factory=list)
    link_ids: list[int] = Field(default_factory=list)
    conflict_ids: list[int] = Field(default_factory=list)
    boundary_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> EvidenceWindow:
        if self.end_us <= self.start_us:
            raise ValueError("evidence window must have positive duration")
        return self


class FusionResult(FusionModel):
    schema_version: int = 1
    run_id: str
    windows: list[EvidenceWindow]
    links: list[EvidenceLink]
    conflicts: list[EvidenceConflict]
    evidence: list[EvidenceSpan]
    visual_states: list[VisualState]


_TRANSCRIPT_MODALITIES = {
    EvidenceModality.PLATFORM_CAPTION,
    EvidenceModality.ASR,
}


def build_evidence_timeline(
    evidence: list[EvidenceSpan],
    visual_states: list[VisualState] | None = None,
    *,
    speech_gap_us: int = 1_200_000,
    maximum_window_us: int = 90_000_000,
    conflict_similarity_threshold: float = 0.72,
) -> FusionResult:
    """Fuse co-temporal evidence without changing any physical timestamp.

    Boundaries come from meaningful visual transitions and speech pauses.
    ``maximum_window_us`` is only a safety cap for unusually long continuous
    material; it is not a fixed-window sampling cadence.
    """

    states = visual_states or []
    if speech_gap_us < 0:
        raise ValueError("speech_gap_us cannot be negative")
    if maximum_window_us <= 0:
        raise ValueError("maximum_window_us must be positive")
    if not 0 <= conflict_similarity_threshold <= 1:
        raise ValueError("conflict similarity threshold must be in [0, 1]")

    run_ids = {item.run_id for item in evidence} | {item.run_id for item in states}
    if not run_ids:
        raise ValueError("at least one evidence span or visual state is required")
    if len(run_ids) != 1:
        raise ValueError("all fusion inputs must belong to one run")
    run_id = next(iter(run_ids))

    start_us = min([item.start_us for item in evidence] + [item.start_us for item in states])
    end_us = max([item.end_us for item in evidence] + [item.end_us for item in states])
    if end_us <= start_us:
        end_us = start_us + 1

    boundary_reasons: dict[int, set[str]] = {
        start_us: {"media_start"},
        end_us: {"media_end"},
    }
    for state in states:
        if start_us < state.transition_us < end_us:
            boundary_reasons.setdefault(state.transition_us, set()).add("visual_transition")

    speech = sorted(
        (item for item in evidence if item.modality in _TRANSCRIPT_MODALITIES),
        key=lambda item: (item.start_us, item.end_us, item.id),
    )
    previous_end: int | None = None
    for item in speech:
        if previous_end is not None and item.start_us - previous_end >= speech_gap_us:
            boundary_reasons.setdefault(item.start_us, set()).add("speech_pause")
        previous_end = max(previous_end or item.end_us, item.end_us)

    boundaries = _apply_maximum_window(
        sorted(boundary_reasons),
        maximum_window_us=maximum_window_us,
        boundary_reasons=boundary_reasons,
        candidate_starts=sorted(
            {item.start_us for item in evidence if start_us < item.start_us < end_us}
        ),
    )

    links = _build_links(evidence)
    conflicts = _detect_conflicts(
        evidence,
        similarity_threshold=conflict_similarity_threshold,
    )
    windows: list[EvidenceWindow] = []
    for index, (left, right) in enumerate(zip(boundaries, boundaries[1:], strict=False)):
        included_evidence = [item.id for item in evidence if _overlaps_half_open(item, left, right)]
        included_states = [item.id for item in states if _state_overlaps(item, left, right)]
        if not included_evidence and not included_states:
            continue
        evidence_set = set(included_evidence)
        link_ids = [
            link_index
            for link_index, link in enumerate(links)
            if link.left_id in evidence_set and link.right_id in evidence_set
        ]
        conflict_ids = [
            conflict_index
            for conflict_index, conflict in enumerate(conflicts)
            if conflict.left_id in evidence_set and conflict.right_id in evidence_set
        ]
        reasons = set(boundary_reasons.get(left, ()))
        reasons.update(boundary_reasons.get(right, ()))
        windows.append(
            EvidenceWindow(
                id=f"window-{index:05d}",
                run_id=run_id,
                start_us=left,
                end_us=right,
                evidence_ids=included_evidence,
                visual_state_ids=included_states,
                link_ids=link_ids,
                conflict_ids=conflict_ids,
                boundary_reasons=sorted(reasons),
            )
        )

    return FusionResult(
        run_id=run_id,
        windows=windows,
        links=links,
        conflicts=conflicts,
        evidence=sorted(evidence, key=lambda item: (item.start_us, item.end_us, item.id)),
        visual_states=sorted(
            states,
            key=lambda item: (item.start_us, item.end_us, item.id),
        ),
    )


def _apply_maximum_window(
    boundaries: list[int],
    *,
    maximum_window_us: int,
    boundary_reasons: dict[int, set[str]],
    candidate_starts: list[int],
) -> list[int]:
    expanded = [boundaries[0]]
    for target_boundary in boundaries[1:]:
        cursor = expanded[-1]
        while target_boundary - cursor > maximum_window_us:
            limit = cursor + maximum_window_us
            candidates = [value for value in candidate_starts if cursor < value <= limit]
            split = candidates[-1] if candidates else limit
            if split <= cursor:
                split = limit
            boundary_reasons.setdefault(split, set()).add("maximum_context")
            expanded.append(split)
            cursor = split
        if target_boundary > expanded[-1]:
            expanded.append(target_boundary)
    return expanded


def _build_links(evidence: list[EvidenceSpan]) -> list[EvidenceLink]:
    links: list[EvidenceLink] = []
    for left, right in _overlapping_pairs(evidence):
        if left.modality is EvidenceModality.OCR and right.modality is EvidenceModality.OCR:
            # Stable OCR continuity is represented by ocr_track_id and its full
            # observation provenance.  A clique between every coexisting line
            # adds no semantic information and used to dominate the timeline.
            continue
        overlap = _overlap_us(left, right)
        shorter = max(1, min(left.end_us - left.start_us, right.end_us - right.start_us))
        relation = _link_relation(left.modality, right.modality)
        links.append(
            EvidenceLink(
                left_id=left.id,
                right_id=right.id,
                relation=relation,
                overlap_us=overlap,
                overlap_ratio=min(1.0, overlap / shorter),
            )
        )
    return links


def _link_relation(
    left: EvidenceModality,
    right: EvidenceModality,
) -> LinkRelation:
    if left in _TRANSCRIPT_MODALITIES and right in _TRANSCRIPT_MODALITIES:
        return LinkRelation.SAME_SIGNAL_CANDIDATE
    if EvidenceModality.VISUAL in {left, right}:
        return LinkRelation.VISUAL_CONTEXT
    return LinkRelation.CO_TEMPORAL_COMPLEMENT


def _detect_conflicts(
    evidence: list[EvidenceSpan],
    *,
    similarity_threshold: float,
) -> list[EvidenceConflict]:
    conflicts: list[EvidenceConflict] = []
    transcripts = [item for item in evidence if item.modality in _TRANSCRIPT_MODALITIES]
    ocr = [item for item in evidence if item.modality is EvidenceModality.OCR]
    candidates: list[tuple[EvidenceSpan, EvidenceSpan, ConflictKind]] = [
        (left, right, ConflictKind.TRANSCRIPT_DISAGREEMENT)
        for left, right in _overlapping_pairs(transcripts)
    ]
    candidates.extend(
        (left, right, ConflictKind.OCR_DISAGREEMENT)
        for left, right in _ocr_region_candidates(ocr)
    )
    for left, right, kind in candidates:
        overlap = _overlap_us(left, right)
        if overlap <= 0:
            continue
        left_text = _normalized_text(left)
        right_text = _normalized_text(right)
        if not left_text or not right_text:
            continue
        similarity = SequenceMatcher(None, left_text, right_text).ratio()
        if similarity >= similarity_threshold:
            continue
        if kind is ConflictKind.OCR_DISAGREEMENT and not _ocr_texts_are_mutually_exclusive(
            left,
            right,
            similarity=similarity,
        ):
            continue
        confidence = min(
            left.confidence if left.confidence is not None else 0.5,
            right.confidence if right.confidence is not None else 0.5,
        )
        severity = min(1.0, (1.0 - similarity) * (0.5 + 0.5 * confidence))
        conflicts.append(
            EvidenceConflict(
                left_id=left.id,
                right_id=right.id,
                kind=kind,
                start_us=max(left.start_us, right.start_us),
                end_us=min(left.end_us, right.end_us),
                text_similarity=similarity,
                severity=severity,
                requires_secondary=severity >= 0.25,
            )
        )
    return conflicts


def _overlapping_pairs(
    evidence: list[EvidenceSpan],
) -> Iterator[tuple[EvidenceSpan, EvidenceSpan]]:
    """Yield interval-overlapping pairs without comparing the full collection."""

    active: list[EvidenceSpan] = []
    for current in sorted(evidence, key=lambda item: (item.start_us, item.end_us, item.id)):
        active = [item for item in active if item.end_us > current.start_us]
        for previous in active:
            if _overlap_us(previous, current) > 0:
                yield previous, current
        active.append(current)


_MAX_OCR_REGION_CELLS_PER_BOX = 256
_MAX_UNFRAMED_PIXEL_EXTENT = 1_000_000.0


def _ocr_region_candidates(
    evidence: list[EvidenceSpan],
) -> Iterator[tuple[EvidenceSpan, EvidenceSpan]]:
    """Yield only OCR pairs sharing both a temporal and spatial bucket."""

    buckets: dict[tuple[str, int, int], list[EvidenceSpan]] = {}
    for item in evidence:
        for key in _ocr_region_keys(item):
            buckets.setdefault(key, []).append(item)

    yielded: set[tuple[str, str]] = set()
    for bucket in buckets.values():
        for left, right in _overlapping_pairs(bucket):
            pair_key = (
                (left.id, right.id)
                if left.id <= right.id
                else (right.id, left.id)
            )
            if pair_key in yielded:
                continue
            yielded.add(pair_key)
            if not _ocr_observations_are_distinct(left, right):
                continue
            if _ocr_evidence_same_region(left, right):
                yield left, right


def _ocr_region_keys(evidence: EvidenceSpan) -> set[tuple[str, int, int]]:
    keys: set[tuple[str, int, int]] = set()
    frame_width = _positive_number(evidence.provenance.get("frame_width"))
    frame_height = _positive_number(evidence.provenance.get("frame_height"))
    for box in evidence.bounding_boxes:
        if not all(math.isfinite(value) for value in (box.x, box.y, box.width, box.height)):
            continue
        grid_width: int | None
        grid_height: int | None
        if box.coordinate_space == "normalized":
            prefix = "normalized"
            x, y, width, height = box.x, box.y, box.width, box.height
            cell_width, cell_height = 1 / 12, 1 / 20
            grid_width, grid_height = 12, 20
        elif box.coordinate_space == "pixels" and frame_width and frame_height:
            prefix = "normalized"
            x = box.x / frame_width
            y = box.y / frame_height
            width = box.width / frame_width
            height = box.height / frame_height
            cell_width, cell_height = 1 / 12, 1 / 20
            grid_width, grid_height = 12, 20
        else:
            prefix = f"pixels:{box.coordinate_space}"
            x = min(box.x, _MAX_UNFRAMED_PIXEL_EXTENT)
            y = min(box.y, _MAX_UNFRAMED_PIXEL_EXTENT)
            width = min(box.width, _MAX_UNFRAMED_PIXEL_EXTENT)
            height = min(box.height, _MAX_UNFRAMED_PIXEL_EXTENT)
            cell_width, cell_height = 160.0, 64.0
            grid_width = grid_height = None
        margin_x = width * 0.2
        margin_y = height * 0.35
        left = max(0.0, x - margin_x)
        top = max(0.0, y - margin_y)
        right = x + width + margin_x
        bottom = y + height + margin_y
        if grid_width is not None and grid_height is not None:
            left = min(1.0, left)
            top = min(1.0, top)
            right = min(1.0, right)
            bottom = min(1.0, bottom)
        first_x = math.floor(left / cell_width)
        last_x = math.floor(right / cell_width)
        first_y = math.floor(top / cell_height)
        last_y = math.floor(bottom / cell_height)
        if grid_width is not None and grid_height is not None:
            first_x = min(grid_width - 1, first_x)
            last_x = min(grid_width - 1, last_x)
            first_y = min(grid_height - 1, first_y)
            last_y = min(grid_height - 1, last_y)
        for column, row in _bounded_region_cells(first_x, last_x, first_y, last_y):
            keys.add((prefix, column, row))
    return keys


def _bounded_region_cells(
    first_x: int,
    last_x: int,
    first_y: int,
    last_y: int,
) -> Iterator[tuple[int, int]]:
    """Enumerate a representative, bounded grid for one OCR box."""

    column_count = max(0, last_x - first_x + 1)
    row_count = max(0, last_y - first_y + 1)
    if column_count == 0 or row_count == 0:
        return
    columns: range | tuple[int, ...]
    rows: range | tuple[int, ...]
    if column_count * row_count <= _MAX_OCR_REGION_CELLS_PER_BOX:
        columns = range(first_x, last_x + 1)
        rows = range(first_y, last_y + 1)
    else:
        axis_budget = math.isqrt(_MAX_OCR_REGION_CELLS_PER_BOX)
        column_budget = min(column_count, axis_budget)
        row_budget = min(
            row_count,
            max(1, _MAX_OCR_REGION_CELLS_PER_BOX // column_budget),
        )
        column_budget = min(
            column_count,
            max(1, _MAX_OCR_REGION_CELLS_PER_BOX // row_budget),
        )
        columns = _sample_region_axis(first_x, last_x, column_budget)
        rows = _sample_region_axis(first_y, last_y, row_budget)
    for column in columns:
        for row in rows:
            yield column, row


def _sample_region_axis(first: int, last: int, sample_count: int) -> tuple[int, ...]:
    value_count = last - first + 1
    if sample_count >= value_count:
        return tuple(range(first, last + 1))
    if sample_count == 1:
        return ((first + last) // 2,)
    return tuple(
        first + ((value_count - 1) * index) // (sample_count - 1)
        for index in range(sample_count)
    )


def _ocr_observations_are_distinct(left: EvidenceSpan, right: EvidenceSpan) -> bool:
    left_states = _ocr_state_ids(left)
    right_states = _ocr_state_ids(right)
    return not left_states or not right_states or left_states.isdisjoint(right_states)


def _ocr_state_ids(evidence: EvidenceSpan) -> set[str]:
    states: set[str] = set()
    direct = evidence.provenance.get("visual_state_id")
    if isinstance(direct, str) and direct:
        states.add(direct)
    observations = evidence.provenance.get("observations")
    if isinstance(observations, list):
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            provenance = observation.get("provenance")
            if not isinstance(provenance, dict):
                continue
            state_id = provenance.get("visual_state_id")
            if isinstance(state_id, str) and state_id:
                states.add(state_id)
    return states


def _ocr_evidence_same_region(left: EvidenceSpan, right: EvidenceSpan) -> bool:
    left_regions = _ocr_observation_regions(left)
    right_regions = _ocr_observation_regions(right)
    for (_, _, left_boxes), (_, _, right_boxes) in _overlapping_observation_regions(
        left_regions,
        right_regions,
    ):
        if any(
            _boxes_same_region(left_box, right_box)
            for left_box in left_boxes
            for right_box in right_boxes
        ):
            return True
    return False


_OcrObservationRegion = tuple[int, int, list[BoundingBox]]


def _overlapping_observation_regions(
    left: list[_OcrObservationRegion],
    right: list[_OcrObservationRegion],
) -> Iterator[tuple[_OcrObservationRegion, _OcrObservationRegion]]:
    """Sweep two interval collections and emit only real temporal overlaps.

    Complexity is O((L + R) log(L + R) + K), where K is the number of actual
    overlaps.  Stable OCR tracks normally have non-overlapping observations,
    so this stays linear after sorting instead of comparing every frame pair.
    """

    events: list[tuple[int, int, int, _OcrObservationRegion]] = []
    for side, regions in enumerate((left, right)):
        events.extend(
            (region[0], side, index, region)
            for index, region in enumerate(regions)
            if region[1] > region[0]
        )
    active: list[dict[int, _OcrObservationRegion]] = [{}, {}]
    expirations: list[list[tuple[int, int]]] = [[], []]
    for start_us, side, index, region in sorted(
        events,
        key=lambda item: (item[0], item[1], item[2]),
    ):
        for active_side in (0, 1):
            expiry = expirations[active_side]
            while expiry and expiry[0][0] <= start_us:
                _, expired_index = heapq.heappop(expiry)
                active[active_side].pop(expired_index, None)

        opposite = 1 - side
        for other in active[opposite].values():
            yield (region, other) if side == 0 else (other, region)

        active[side][index] = region
        heapq.heappush(expirations[side], (region[1], index))


def _ocr_observation_regions(
    evidence: EvidenceSpan,
) -> list[tuple[int, int, list[BoundingBox]]]:
    observations = evidence.provenance.get("observations")
    parsed: list[tuple[int, int, list[BoundingBox]]] = []
    if isinstance(observations, list):
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            start_us = observation.get("start_us")
            end_us = observation.get("end_us")
            boxes = observation.get("bounding_boxes")
            if (
                not isinstance(start_us, int)
                or isinstance(start_us, bool)
                or not isinstance(end_us, int)
                or isinstance(end_us, bool)
                or not isinstance(boxes, list)
            ):
                continue
            try:
                parsed_boxes = [BoundingBox.model_validate(box) for box in boxes]
            except ValueError:
                continue
            parsed.append((start_us, end_us, parsed_boxes))
    return parsed or [(evidence.start_us, evidence.end_us, evidence.bounding_boxes)]


def _boxes_same_region(left_box: BoundingBox, right_box: BoundingBox) -> bool:
    if left_box.coordinate_space != right_box.coordinate_space:
        return False
    intersection_width = max(
        0.0,
        min(left_box.x + left_box.width, right_box.x + right_box.width)
        - max(left_box.x, right_box.x),
    )
    intersection_height = max(
        0.0,
        min(left_box.y + left_box.height, right_box.y + right_box.height)
        - max(left_box.y, right_box.y),
    )
    smaller_area = min(
        left_box.width * left_box.height,
        right_box.width * right_box.height,
    )
    coverage = (
        intersection_width * intersection_height / smaller_area
        if smaller_area > 0
        else 0.0
    )
    center_dx = abs(
        (left_box.x + left_box.width / 2) - (right_box.x + right_box.width / 2)
    )
    center_dy = abs(
        (left_box.y + left_box.height / 2) - (right_box.y + right_box.height / 2)
    )
    return coverage >= 0.35 or (
        center_dx <= 0.35 * max(left_box.width, right_box.width)
        and center_dy <= 0.60 * max(left_box.height, right_box.height)
    )


def _ocr_texts_are_mutually_exclusive(
    left: EvidenceSpan,
    right: EvidenceSpan,
    *,
    similarity: float,
) -> bool:
    left_text = _normalized_text(left)
    right_text = _normalized_text(right)
    if not left_text or not right_text or left_text in right_text or right_text in left_text:
        return False
    left_track = left.provenance.get("ocr_track_id")
    right_track = right.provenance.get("ocr_track_id")
    if isinstance(left_track, str) and isinstance(right_track, str):
        return bool(left_track and left_track == right_track)
    if similarity >= 0.28:
        return True
    return bool(_VALUE_ONLY.fullmatch(left_text) and _VALUE_ONLY.fullmatch(right_text))


def _positive_number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


_VALUE_ONLY = re.compile(r"[^\w]*\d[\d\W_]*", re.UNICODE)


def _normalized_text(evidence: EvidenceSpan) -> str:
    text = evidence.normalized_text or evidence.raw_text or ""
    return "".join(text.casefold().split())


def _overlap_us(left: EvidenceSpan, right: EvidenceSpan) -> int:
    return max(0, min(left.end_us, right.end_us) - max(left.start_us, right.start_us))


def _overlaps_half_open(evidence: EvidenceSpan, start_us: int, end_us: int) -> bool:
    if evidence.start_us == evidence.end_us:
        return start_us <= evidence.start_us < end_us
    return evidence.start_us < end_us and evidence.end_us > start_us


def _state_overlaps(state: VisualState, start_us: int, end_us: int) -> bool:
    if state.start_us == state.end_us:
        return start_us <= state.start_us < end_us
    return state.start_us < end_us and state.end_us > start_us
