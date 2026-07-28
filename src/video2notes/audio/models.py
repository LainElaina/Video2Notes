"""Strict contracts for extracted audio and timestamped ASR hypotheses."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from video2notes.domain import EvidenceSpan


class AudioModel(BaseModel):
    """Base model that rejects silent schema drift."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TranscriptTimeline(StrEnum):
    """Physical clock represented by ASR word and segment timestamps."""

    AUDIO_FILE = "audio_file"
    CANONICAL_MEDIA = "canonical_media"


class AudioExtractionResult(AudioModel):
    """Description of a deterministic mono PCM extraction.

    A WAV container cannot retain the source stream PTS. The explicit mapping
    below states that sample zero in ``output_path`` corresponds to
    ``output_time_zero_canonical_us`` on the canonical media timeline.
    """

    schema_version: int = 1
    input_path: str
    output_path: str
    audio_stream_index: int = Field(ge=0)
    source_stream_start_us: int
    timeline_origin_us: int
    output_time_zero_canonical_us: int = Field(ge=0)
    duration_us: int = Field(ge=0)
    sample_rate: int = Field(default=16_000, ge=1)
    channels: int = Field(default=1, ge=1)
    sample_format: str = "s16"
    codec: str = "pcm_s16le"

    @model_validator(mode="after")
    def validate_timeline_mapping(self) -> Self:
        expected = self.source_stream_start_us - self.timeline_origin_us
        if self.output_time_zero_canonical_us != expected:
            raise ValueError(
                "output sample zero must map to audio stream start minus timeline origin"
            )
        return self


class ASRWord(AudioModel):
    """One word hypothesis with provider provenance and both confidence forms."""

    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    text: str
    language: str | None = None
    speaker: str | None = None
    raw_confidence: float | None = None
    calibrated_confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_method: str | None = None
    provider: str
    model: str
    version: str

    @field_validator("text", "provider", "model", "version")
    @classmethod
    def reject_empty_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required ASR text and provenance fields cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end_us < self.start_us:
            raise ValueError("ASR word end_us cannot be before start_us")
        return self


class ASRSegment(AudioModel):
    """One ASR segment retaining its complete word-level hypothesis."""

    id: str
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    text: str
    words: list[ASRWord] = Field(default_factory=list)
    language: str | None = None
    speaker: str | None = None
    raw_confidence: float | None = None
    calibrated_confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_method: str | None = None
    provider: str
    model: str
    version: str

    @field_validator("id", "text", "provider", "model", "version")
    @classmethod
    def reject_empty_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required ASR segment fields cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_segment(self) -> Self:
        if self.end_us < self.start_us:
            raise ValueError("ASR segment end_us cannot be before start_us")
        for word in self.words:
            if word.start_us < self.start_us or word.end_us > self.end_us:
                raise ValueError("ASR word timestamps must stay inside their segment")
            if (
                word.provider != self.provider
                or word.model != self.model
                or word.version != self.version
            ):
                raise ValueError("ASR word provenance must match its segment")
        return self


class ASRTranscript(AudioModel):
    """Versioned ASR output before or after canonical timeline calibration."""

    schema_version: int = 1
    provider: str
    model: str
    version: str
    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0, le=1)
    sample_rate: int = Field(default=16_000, ge=1)
    channels: int = Field(default=1, ge=1)
    timeline: TranscriptTimeline = TranscriptTimeline.AUDIO_FILE
    timeline_offset_us: int = Field(default=0, ge=0)
    segments: list[ASRSegment] = Field(default_factory=list)

    @field_validator("provider", "model", "version")
    @classmethod
    def reject_empty_provenance(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ASR transcript provenance cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        for segment in self.segments:
            if (
                segment.provider != self.provider
                or segment.model != self.model
                or segment.version != self.version
            ):
                raise ValueError("ASR segment provenance must match its transcript")
        return self


class ASREvidenceResult(AudioModel):
    """Canonical transcript plus fusion-ready segment evidence."""

    schema_version: int = 1
    run_id: str
    transcript: ASRTranscript
    evidence: list[EvidenceSpan]

    @model_validator(mode="after")
    def validate_canonical_output(self) -> Self:
        if self.transcript.timeline is not TranscriptTimeline.CANONICAL_MEDIA:
            raise ValueError("ASR evidence result must use the canonical media timeline")
        if any(item.run_id != self.run_id for item in self.evidence):
            raise ValueError("all ASR evidence must belong to the result run")
        return self
