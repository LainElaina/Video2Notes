"""Versioned, JSON-safe domain models for the evidence-first pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from fractions import Fraction
from pathlib import PurePosixPath
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Rational(StrictModel):
    numerator: int
    denominator: int

    @model_validator(mode="after")
    def validate_denominator(self) -> Self:
        if self.denominator == 0:
            raise ValueError("rational denominator cannot be zero")
        return self

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def timestamp_us(self, pts: int) -> int:
        return round(pts * self.fraction * 1_000_000)


class MediaTimestamp(StrictModel):
    pts: int | None
    time_base: Rational
    source_time_us: int
    time_us: int
    timestamp_kind: str = "pts"

    @classmethod
    def from_pts(
        cls,
        pts: int,
        time_base: Rational,
        *,
        timeline_origin_us: int,
        timestamp_kind: str = "pts",
    ) -> Self:
        source_time_us = time_base.timestamp_us(pts)
        return cls(
            pts=pts,
            time_base=time_base,
            source_time_us=source_time_us,
            time_us=source_time_us - timeline_origin_us,
            timestamp_kind=timestamp_kind,
        )


class ArtifactKind(StrEnum):
    SOURCE = "source"
    MEDIA = "media"
    SUBTITLE = "subtitle"
    AUDIO = "audio"
    ASR = "asr"
    VISUAL = "visual"
    OCR = "ocr"
    EVIDENCE = "evidence"
    NOTE = "note"
    RENDER = "render"
    SUPPORTING = "supporting"
    LOG = "log"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvidenceModality(StrEnum):
    METADATA = "metadata"
    PLATFORM_CAPTION = "platform_caption"
    ASR = "asr"
    OCR = "ocr"
    VISUAL = "visual"


class SourceDescriptor(StrictModel):
    kind: str
    locator: str
    platform: str | None = None
    canonical_url: str | None = None
    source_id: str | None = None
    title: str | None = None
    author: str | None = None


class MediaStream(StrictModel):
    index: int
    codec_type: str
    codec_name: str | None = None
    time_base: Rational
    start_pts: int | None = None
    start_time_us: int | None = None
    duration_ts: int | None = None
    duration_us: int | None = None
    avg_frame_rate: Rational | None = None
    real_frame_rate: Rational | None = None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    language: str | None = None


class MediaManifest(StrictModel):
    schema_version: int = 1
    source_path: str
    source_sha256: str
    container_format: str | None = None
    file_size: int
    duration_us: int
    timeline_origin_us: int
    streams: list[MediaStream]
    probed_at: datetime = Field(default_factory=utc_now)

    @property
    def video_stream(self) -> MediaStream | None:
        return next((item for item in self.streams if item.codec_type == "video"), None)

    @property
    def audio_stream(self) -> MediaStream | None:
        return next((item for item in self.streams if item.codec_type == "audio"), None)


class ArtifactRef(StrictModel):
    kind: ArtifactKind
    relative_path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    media_type: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("artifact path must stay inside its run directory")
        if normalized in {"", "."}:
            raise ValueError("artifact path cannot be empty")
        return str(path)


class ModelInvocation(StrictModel):
    provider: str
    model: str
    version: str | None = None
    role: str
    locality: str | None = None
    cost: float | None = Field(default=None, ge=0)


class StageRecord(StrictModel):
    stage_name: str
    stage_version: str
    fingerprint: str
    status: StageStatus = StageStatus.PENDING
    attempt: int = Field(default=1, ge=1)
    config_hash: str
    inputs: list[ArtifactRef] = Field(default_factory=list)
    outputs: list[ArtifactRef] = Field(default_factory=list)
    model_invocations: list[ModelInvocation] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    wall_time_seconds: float | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    peak_vram_bytes: int | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    error: str | None = None


class ArtifactManifest(StrictModel):
    schema_version: int = 1
    run_id: str
    source: SourceDescriptor
    profile: str
    status: RunStatus = RunStatus.CREATED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    stages: dict[str, StageRecord] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class BoundingBox(StrictModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    coordinate_space: str = "pixels"


class EvidenceSpan(StrictModel):
    schema_version: int = 1
    id: str
    run_id: str
    modality: EvidenceModality
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    language: str | None = None
    raw_text: str | None = None
    normalized_text: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_kind: str | None = None
    provider: str | None = None
    model: str | None = None
    version: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    bounding_boxes: list[BoundingBox] = Field(default_factory=list)
    speaker: str | None = None
    parent_hypothesis_id: str | None = None
    correction_of: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end_us < self.start_us:
            raise ValueError("evidence end_us cannot be before start_us")
        return self


class VisualState(StrictModel):
    schema_version: int = 1
    id: str
    run_id: str
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    transition_us: int = Field(ge=0)
    stable_keyframe_us: int = Field(ge=0)
    transition_pts: int | None = None
    keyframe_pts: int | None = None
    stream_time_base: Rational | None = None
    keyframe_artifact: ArtifactRef | None = None
    change_reason: str
    ocr_span_ids: list[str] = Field(default_factory=list)
    masks: list[ArtifactRef] = Field(default_factory=list)
    quality: dict[str, float | int | str | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.end_us < self.start_us:
            raise ValueError("visual state end_us cannot be before start_us")
        if not self.start_us <= self.transition_us <= self.end_us:
            raise ValueError("visual transition must be inside the state interval")
        if not self.transition_us <= self.stable_keyframe_us <= self.end_us:
            raise ValueError("stable keyframe must follow the transition")
        return self
