"""Interval-overlap evidence fusion with explicit conflict provenance."""

from __future__ import annotations

from difflib import SequenceMatcher
from enum import StrEnum
from itertools import combinations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from video2notes.domain import EvidenceModality, EvidenceSpan, VisualState


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
    for left, right in combinations(evidence, 2):
        overlap = _overlap_us(left, right)
        if overlap <= 0:
            continue
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
    for left, right in combinations(evidence, 2):
        kind = _conflict_kind(left.modality, right.modality)
        if kind is None:
            continue
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


def _conflict_kind(
    left: EvidenceModality,
    right: EvidenceModality,
) -> ConflictKind | None:
    if left in _TRANSCRIPT_MODALITIES and right in _TRANSCRIPT_MODALITIES:
        return ConflictKind.TRANSCRIPT_DISAGREEMENT
    if left is EvidenceModality.OCR and right is EvidenceModality.OCR:
        return ConflictKind.OCR_DISAGREEMENT
    return None


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
