"""Stable, JSON-safe contracts for offline run diagnostics."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from video2notes.domain import EvidenceModality


class EvaluationModel(BaseModel):
    """Base model for evaluation results that must remain reproducible and read-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceIdentity(EvaluationModel):
    kind: str
    locator: str
    platform: str | None = None
    source_id: str | None = None
    canonical_url: str | None = None
    comparison_key: str


class StageTiming(EvaluationModel):
    stage_name: str
    status: str
    attempt: int = Field(ge=1)
    wall_time_seconds: float | None = Field(default=None, ge=0)
    share_of_recorded_stage_time: float | None = Field(default=None, ge=0, le=1)
    warning_count: int = Field(ge=0)


class EvidenceClassDiagnostics(EvaluationModel):
    modality: EvidenceModality
    span_count: int = Field(ge=0)
    spans_with_confidence: int = Field(ge=0)
    average_confidence: float | None = Field(default=None, ge=0, le=1)
    temporal_covered_us: int = Field(ge=0)
    temporal_coverage_ratio: float | None = Field(default=None, ge=0, le=1)


class EvidenceReferenceDiagnostics(EvaluationModel):
    timeline_evidence_count: int = Field(ge=0)
    note_evidence_count: int = Field(ge=0)
    citation_occurrence_count: int = Field(ge=0)
    unique_cited_evidence_count: int = Field(ge=0)
    cited_timeline_evidence_count: int = Field(ge=0)
    citation_coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    unknown_citation_ids: tuple[str, ...] = ()
    note_evidence_missing_from_timeline_ids: tuple[str, ...] = ()
    timeline_evidence_missing_from_note_ids: tuple[str, ...] = ()
    uncited_timeline_evidence_ids: tuple[str, ...] = ()
    all_references_valid: bool
    embedded_evidence_matches_timeline: bool
    is_complete: bool


class NoteDiagnostics(EvaluationModel):
    section_count: int = Field(ge=0)
    key_takeaway_count: int = Field(ge=0)
    fact_count: int = Field(ge=0)
    glossary_entry_count: int = Field(ge=0)
    screenshot_count: int = Field(ge=0)
    existing_screenshot_file_count: int = Field(ge=0)
    missing_screenshot_paths: tuple[str, ...] = ()
    non_whitespace_character_count: int = Field(ge=0)


class WarningDiagnostics(EvaluationModel):
    total_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)
    manifest_count: int = Field(ge=0)
    stage_count: int = Field(ge=0)
    note_count: int = Field(ge=0)
    unique_messages: tuple[str, ...] = ()


class OutputDiagnostics(EvaluationModel):
    markdown_declared: bool
    html_declared: bool
    pdf_declared: bool
    declared_artifact_count: int = Field(ge=0)
    existing_artifact_count: int = Field(ge=0)
    missing_artifact_paths: tuple[str, ...] = ()
    declared_evidence_count: int = Field(ge=0)
    evidence_count_matches_timeline: bool
    declared_visual_state_count: int = Field(ge=0)
    visual_state_count_matches_timeline: bool
    used_deterministic_note_fallback: bool


class RunDiagnostics(EvaluationModel):
    schema_version: int = 1
    run_directory: str
    run_id: str
    profile: str
    source: SourceIdentity
    media_duration_seconds: float = Field(ge=0)
    run_elapsed_wall_time_seconds: float = Field(ge=0)
    recorded_stage_wall_time_seconds: float = Field(ge=0)
    stages_with_recorded_wall_time: int = Field(ge=0)
    realtime_factor: float | None = Field(default=None, ge=0)
    stages: tuple[StageTiming, ...]
    evidence_count: int = Field(ge=0)
    evidence_temporal_covered_us: int = Field(ge=0)
    evidence_temporal_coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    evidence_spans_with_confidence: int = Field(ge=0)
    average_confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_by_modality: tuple[EvidenceClassDiagnostics, ...]
    conflict_count: int = Field(ge=0)
    unresolved_conflict_count: int = Field(ge=0)
    secondary_review_conflict_count: int = Field(ge=0)
    visual_state_count: int = Field(ge=0)
    visual_state_with_keyframe_count: int = Field(ge=0)
    note: NoteDiagnostics
    warnings: WarningDiagnostics
    evidence_references: EvidenceReferenceDiagnostics
    outputs: OutputDiagnostics


class ProfileComparison(EvaluationModel):
    profile: str
    run_id: str
    media_duration_seconds: float = Field(ge=0)
    recorded_stage_wall_time_seconds: float = Field(ge=0)
    realtime_factor: float | None = Field(default=None, ge=0)
    evidence_count: int = Field(ge=0)
    evidence_temporal_coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    evidence_spans_with_confidence: int = Field(ge=0)
    average_confidence: float | None = Field(default=None, ge=0, le=1)
    conflict_count: int = Field(ge=0)
    unresolved_conflict_count: int = Field(ge=0)
    visual_state_count: int = Field(ge=0)
    screenshot_count: int = Field(ge=0)
    section_count: int = Field(ge=0)
    key_takeaway_count: int = Field(ge=0)
    non_whitespace_character_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    evidence_references_complete: bool
    used_deterministic_note_fallback: bool


class RunComparison(EvaluationModel):
    schema_version: int = 1
    source: SourceIdentity
    profiles: tuple[ProfileComparison, ...]

    @model_validator(mode="after")
    def validate_profiles(self) -> Self:
        expected = ("fast", "balanced", "accurate")
        actual = tuple(item.profile for item in self.profiles)
        if actual != expected:
            raise ValueError(f"profiles must be ordered as {expected!r}")
        return self
