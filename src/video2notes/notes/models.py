"""One canonical note model shared by Markdown, HTML, and PDF."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from video2notes.domain import EvidenceSpan


class NoteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class NoteMetadata(NoteModel):
    title: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source_kind: str
    source_locator: str
    source_url: str | None = None
    author: str | None = None
    duration_us: int = Field(ge=0)
    languages: list[str] = Field(default_factory=list)
    quality_mode: str
    source_resolution: str | None = None
    quality_warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NoteScreenshot(NoteModel):
    relative_path: str
    timestamp_us: int = Field(ge=0)
    caption: str
    alt_text: str
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or normalized in {"", "."}:
            raise ValueError("screenshot path must stay inside the run directory")
        return str(path)


class FactCard(NoteModel):
    id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    kind: str = "fact"
    needs_review: bool = False


class NoteSection(NoteModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    title: str = Field(min_length=1)
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    summary: str
    body_markdown: str
    evidence_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    screenshots: list[NoteScreenshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end_us < self.start_us:
            raise ValueError("section end_us cannot be before start_us")
        for screenshot in self.screenshots:
            if not self.start_us <= screenshot.timestamp_us <= self.end_us:
                raise ValueError(
                    f"screenshot at {screenshot.timestamp_us} is outside section '{self.id}'"
                )
        return self


class NoteDocument(NoteModel):
    schema_version: int = 1
    metadata: NoteMetadata
    abstract: str
    key_takeaways: list[str] = Field(default_factory=list)
    sections: list[NoteSection]
    facts: list[FactCard] = Field(default_factory=list)
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        evidence_ids = {item.id for item in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("evidence IDs must be unique")
        for item in self.evidence:
            if item.run_id != self.metadata.run_id:
                raise ValueError("all evidence must belong to the note run")

        fact_ids = {item.id for item in self.facts}
        if len(fact_ids) != len(self.facts):
            raise ValueError("fact IDs must be unique")
        for fact in self.facts:
            _require_known(
                fact.evidence_ids,
                evidence_ids,
                context=f"fact '{fact.id}'",
            )

        section_ids = {item.id for item in self.sections}
        if len(section_ids) != len(self.sections):
            raise ValueError("section IDs must be unique")
        for section in self.sections:
            _require_known(
                section.evidence_ids,
                evidence_ids,
                context=f"section '{section.id}'",
            )
            _require_known(
                section.fact_ids,
                fact_ids,
                context=f"section '{section.id}' facts",
            )
            for screenshot in section.screenshots:
                _require_known(
                    screenshot.evidence_ids,
                    evidence_ids,
                    context=f"screenshot '{screenshot.relative_path}'",
                )
        return self


def _require_known(values: list[str], known: set[str], *, context: str) -> None:
    missing = sorted(set(values) - known)
    if missing:
        raise ValueError(f"{context} references unknown IDs: {', '.join(missing)}")
