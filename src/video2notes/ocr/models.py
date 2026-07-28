"""Strict contracts for OCR inference, evidence, tracking, and frame selection."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from video2notes.domain import ArtifactRef, EvidenceSpan, Rational


class OcrModel(BaseModel):
    """Base model that rejects silently misspelled or stale fields."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class OcrLineDecision(StrEnum):
    ACCEPTED = "accepted"
    ABSTAINED = "abstained"


class OcrFrameStatus(StrEnum):
    PROCESSED = "processed"
    ABSTAINED = "abstained"


class OcrAbstentionScope(StrEnum):
    FRAME = "frame"
    LINE = "line"


class OcrBox(OcrModel):
    """Axis-aligned OCR box in the coordinate space reported by the engine."""

    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    coordinate_space: str = "pixels"

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


class OcrModelInvocation(OcrModel):
    """Reproducibility details for one local OCR engine invocation."""

    engine: str = Field(min_length=1)
    version: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    detection_model: str | None = None
    recognition_model: str | None = None
    device: str = "cpu"
    language_hints: list[str] = Field(default_factory=list)
    local_models_only: bool = True


class BackendOcrLine(OcrModel):
    """Raw line emitted by an OCR backend before conservative acceptance."""

    raw_text: str
    box: OcrBox
    confidence: float = Field(ge=0, le=1)
    script: str | None = None
    language: str | None = None


class BackendOcrOutput(OcrModel):
    """Backend-neutral output used by real and deterministic fake engines."""

    lines: list[BackendOcrLine]
    invocation: OcrModelInvocation


class OcrLine(OcrModel):
    """One OCR hypothesis, including rejected hypotheses for auditability."""

    id: str = Field(min_length=1)
    raw_text: str
    normalized_text: str
    box: OcrBox
    script: str = Field(min_length=1)
    language: str | None = None
    confidence: float = Field(ge=0, le=1)
    crop_readability: float = Field(ge=0, le=1)
    decision: OcrLineDecision
    abstain_reason: str | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.decision is OcrLineDecision.ACCEPTED:
            if not self.normalized_text:
                raise ValueError("accepted OCR lines require normalized text")
            if self.abstain_reason is not None:
                raise ValueError("accepted OCR lines cannot have an abstain reason")
        elif not self.abstain_reason:
            raise ValueError("abstained OCR lines require an explicit reason")
        return self


class OcrAbstention(OcrModel):
    scope: OcrAbstentionScope
    reason: str = Field(min_length=1)
    detail: str | None = None
    line_id: str | None = None

    @model_validator(mode="after")
    def validate_line_scope(self) -> Self:
        if self.scope is OcrAbstentionScope.LINE and self.line_id is None:
            raise ValueError("line abstentions require line_id")
        if self.scope is OcrAbstentionScope.FRAME and self.line_id is not None:
            raise ValueError("frame abstentions cannot reference line_id")
        return self


class OcrResult(OcrModel):
    """OCR result bound to the exact PTS and canonical time of a visual state."""

    schema_version: int = 1
    id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    visual_state_id: str = Field(min_length=1)
    keyframe_artifact: ArtifactRef | None
    keyframe_pts: int | None
    keyframe_time_base: Rational | None
    keyframe_us: int = Field(ge=0)
    state_start_us: int = Field(ge=0)
    state_end_us: int = Field(ge=0)
    image_width: int | None = Field(default=None, gt=0)
    image_height: int | None = Field(default=None, gt=0)
    frame_readability: float | None = Field(default=None, ge=0, le=1)
    status: OcrFrameStatus
    lines: list[OcrLine] = Field(default_factory=list)
    abstentions: list[OcrAbstention] = Field(default_factory=list)
    invocation: OcrModelInvocation | None = None

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.state_end_us < self.state_start_us:
            raise ValueError("OCR state end cannot be before its start")
        if not self.state_start_us <= self.keyframe_us <= self.state_end_us:
            raise ValueError("OCR keyframe must be inside its visual state")
        if self.status is OcrFrameStatus.PROCESSED and self.invocation is None:
            raise ValueError("processed OCR results require model invocation details")
        if self.status is OcrFrameStatus.ABSTAINED:
            if not self.abstentions:
                raise ValueError("abstained OCR results require an explicit reason")
            if self.lines:
                raise ValueError("frame-level abstention cannot contain OCR lines")
        return self

    @property
    def accepted_lines(self) -> list[OcrLine]:
        return [line for line in self.lines if line.decision is OcrLineDecision.ACCEPTED]


class OcrLineAssignment(OcrModel):
    visual_state_id: str
    line_id: str
    track_id: str


class OcrLineMatch(OcrModel):
    previous_line_id: str
    current_line_id: str
    track_id: str
    box_iou: float = Field(ge=0, le=1)
    text_similarity: float = Field(ge=0, le=1)
    combined_score: float = Field(ge=0, le=1)
    added_tokens: list[str] = Field(default_factory=list)
    removed_tokens: list[str] = Field(default_factory=list)


class OcrStateDelta(OcrModel):
    previous_state_id: str
    current_state_id: str
    matches: list[OcrLineMatch] = Field(default_factory=list)
    added_line_ids: list[str] = Field(default_factory=list)
    removed_line_ids: list[str] = Field(default_factory=list)
    added_tokens: list[str] = Field(default_factory=list)
    removed_tokens: list[str] = Field(default_factory=list)


class OcrTrackingResult(OcrModel):
    assignments: list[OcrLineAssignment] = Field(default_factory=list)
    deltas: list[OcrStateDelta] = Field(default_factory=list)


class FrameCoverageScore(OcrModel):
    """Explanation of one greedy set-cover selection decision."""

    selection_order: int = Field(ge=1)
    result_id: str
    visual_state_id: str
    keyframe_artifact: ArtifactRef | None
    candidate_tokens: list[str]
    newly_covered_tokens: list[str]
    redundant_tokens: list[str]
    clarity_score: float = Field(ge=0, le=1)
    mean_confidence: float = Field(ge=0, le=1)
    weighted_coverage_gain: float = Field(ge=0)
    rarity_bonus: float = Field(ge=0)
    redundancy_penalty: float = Field(ge=0)
    selection_score: float


class ScrollFrameSelection(OcrModel):
    """Minimal-ish keyframe cover for scrolling or incrementally revealed text."""

    all_unique_tokens: list[str]
    selected_frames: list[FrameCoverageScore]
    covered_tokens: list[str]
    uncovered_tokens: list[str]
    coverage_ratio: float = Field(ge=0, le=1)
    candidate_frame_count: int = Field(ge=0)
    algorithm: str = "greedy_weighted_set_cover_v1"


class OcrEvidenceBundle(OcrModel):
    results: list[OcrResult]
    evidence: list[EvidenceSpan]
    tracking: OcrTrackingResult
    scroll_selection: ScrollFrameSelection
