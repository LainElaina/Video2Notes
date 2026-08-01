"""Read completed run artifacts and calculate intrinsic, offline diagnostics."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from video2notes.domain import (
    ArtifactManifest,
    ArtifactRef,
    EvidenceModality,
    EvidenceSpan,
    MediaManifest,
    RunStatus,
    SourceDescriptor,
    StageStatus,
)
from video2notes.fusion import FusionResult
from video2notes.notes.models import NoteDocument

from .models import (
    EvidenceClassDiagnostics,
    EvidenceReferenceDiagnostics,
    NoteDiagnostics,
    OutputDiagnostics,
    ProfileComparison,
    RunComparison,
    RunDiagnostics,
    SourceIdentity,
    StageTiming,
    WarningDiagnostics,
)

_REQUIRED_PROFILES = ("fast", "balanced", "accurate")
_MANIFEST_PATH = Path("manifest.json")
_MEDIA_MANIFEST_PATH = Path("media/media-manifest.json")
_EVIDENCE_TIMELINE_PATH = Path("evidence/timeline.json")
_NOTE_DOCUMENT_PATH = Path("notes/document.json")
_OUTCOME_PATH = Path("render/outcome.json")


class EvaluationError(ValueError):
    """Base error for invalid or incomparable run artifacts."""


class RunArtifactError(EvaluationError):
    """A required artifact is absent, malformed, or internally inconsistent."""


class RunNotCompleteError(EvaluationError):
    """The run or one of its stages has not completed."""


class RunSourceMismatchError(EvaluationError):
    """Runs do not describe the same logical source."""


class RunProfileSetError(EvaluationError):
    """A comparison does not contain exactly one run for each required profile."""


class _PipelineOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    markdown: ArtifactRef
    html: ArtifactRef
    pdf: ArtifactRef | None = None
    note_document: ArtifactRef
    evidence_count: int = Field(ge=0)
    visual_state_count: int = Field(ge=0)
    used_deterministic_note_fallback: bool


ModelT = TypeVar("ModelT", bound=BaseModel)


def diagnose_run(run_directory: str | Path) -> RunDiagnostics:
    """Calculate intrinsic diagnostics from a completed pipeline run.

    The calculation does not invoke media decoders or model providers. Confidence
    values are reported exactly as recorded by the producing components; they are
    not treated as ground-truth quality measurements.
    """

    root = Path(run_directory).expanduser().resolve()
    if not root.is_dir():
        raise RunArtifactError(f"run directory does not exist: {root}")

    manifest = _read_model(root / _MANIFEST_PATH, ArtifactManifest)
    _require_completed(manifest)
    media = _read_model(root / _MEDIA_MANIFEST_PATH, MediaManifest)
    fusion = _read_model(root / _EVIDENCE_TIMELINE_PATH, FusionResult)
    note = _read_model(root / _NOTE_DOCUMENT_PATH, NoteDocument)
    outcome = _read_model(root / _OUTCOME_PATH, _PipelineOutcome)
    _require_consistent_run_ids(manifest, fusion, note, outcome)

    duration_us = media.duration_us
    stage_wall_time = sum(item.wall_time_seconds or 0.0 for item in manifest.stages.values())
    stages_with_time = sum(item.wall_time_seconds is not None for item in manifest.stages.values())
    stages = tuple(
        StageTiming(
            stage_name=name,
            status=record.status.value,
            attempt=record.attempt,
            wall_time_seconds=record.wall_time_seconds,
            share_of_recorded_stage_time=(
                (record.wall_time_seconds or 0.0) / stage_wall_time
                if stage_wall_time > 0 and record.wall_time_seconds is not None
                else None
            ),
            warning_count=len(record.warnings),
        )
        for name, record in sorted(manifest.stages.items())
    )

    evidence_ids = [item.id for item in fusion.evidence]
    if len(set(evidence_ids)) != len(evidence_ids):
        raise RunArtifactError("evidence timeline contains duplicate evidence IDs")
    evidence_covered_us = _temporal_coverage(fusion.evidence, duration_us=duration_us)
    confidences = [item.confidence for item in fusion.evidence if item.confidence is not None]
    evidence_by_modality = tuple(
        _diagnose_modality(modality, fusion.evidence, duration_us=duration_us)
        for modality in EvidenceModality
    )

    screenshots = [screenshot for section in note.sections for screenshot in section.screenshots]
    existing_screenshot_file_count = sum(
        _run_relative_file_exists(root, screenshot.relative_path) for screenshot in screenshots
    )
    missing_screenshot_paths = tuple(
        sorted(
            {
                screenshot.relative_path
                for screenshot in screenshots
                if not _run_relative_file_exists(root, screenshot.relative_path)
            }
        )
    )
    note_diagnostics = NoteDiagnostics(
        section_count=len(note.sections),
        key_takeaway_count=len(note.key_takeaways),
        fact_count=len(note.facts),
        glossary_entry_count=len(note.glossary),
        screenshot_count=len(screenshots),
        existing_screenshot_file_count=existing_screenshot_file_count,
        missing_screenshot_paths=missing_screenshot_paths,
        non_whitespace_character_count=_note_character_count(note),
    )

    warnings = _diagnose_warnings(manifest, note)
    references = _diagnose_references(fusion, note)
    outputs = _diagnose_outputs(root, outcome, fusion)
    unresolved_conflicts = sum(
        item.resolution.strip().casefold() in {"", "unresolved"} for item in fusion.conflicts
    )

    return RunDiagnostics(
        run_directory=str(root),
        run_id=manifest.run_id,
        profile=manifest.profile,
        source=_source_identity(
            manifest.source,
            canonical_url_fallback=note.metadata.source_url,
            platform_fallback=note.metadata.source_kind,
            media_sha256=media.source_sha256,
        ),
        media_duration_seconds=duration_us / 1_000_000,
        run_elapsed_wall_time_seconds=max(
            0.0,
            (manifest.updated_at - manifest.created_at).total_seconds(),
        ),
        recorded_stage_wall_time_seconds=stage_wall_time,
        stages_with_recorded_wall_time=stages_with_time,
        realtime_factor=(stage_wall_time * 1_000_000 / duration_us if duration_us > 0 else None),
        stages=stages,
        evidence_count=len(fusion.evidence),
        evidence_temporal_covered_us=evidence_covered_us,
        evidence_temporal_coverage_ratio=(
            evidence_covered_us / duration_us if duration_us > 0 else None
        ),
        evidence_spans_with_confidence=len(confidences),
        average_confidence=_mean(confidences),
        evidence_by_modality=evidence_by_modality,
        conflict_count=len(fusion.conflicts),
        unresolved_conflict_count=unresolved_conflicts,
        secondary_review_conflict_count=sum(item.requires_secondary for item in fusion.conflicts),
        visual_state_count=len(fusion.visual_states),
        visual_state_with_keyframe_count=sum(
            item.keyframe_artifact is not None for item in fusion.visual_states
        ),
        note=note_diagnostics,
        warnings=warnings,
        evidence_references=references,
        outputs=outputs,
    )


def compare_runs(run_directories: Sequence[str | Path]) -> RunComparison:
    """Compare exactly one completed fast, balanced, and accurate run of one source."""

    diagnostics = [diagnose_run(path) for path in run_directories]
    by_profile: dict[str, RunDiagnostics] = {}
    for item in diagnostics:
        if item.profile in by_profile:
            raise RunProfileSetError(f"duplicate comparison profile: {item.profile}")
        by_profile[item.profile] = item

    actual_profiles = set(by_profile)
    expected_profiles = set(_REQUIRED_PROFILES)
    if actual_profiles != expected_profiles:
        missing = sorted(expected_profiles - actual_profiles)
        unexpected = sorted(actual_profiles - expected_profiles)
        details: list[str] = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unexpected:
            details.append(f"unexpected={','.join(unexpected)}")
        raise RunProfileSetError(
            "comparison requires exactly fast, balanced, and accurate runs"
            + (f" ({'; '.join(details)})" if details else "")
        )

    ordered = [by_profile[profile] for profile in _REQUIRED_PROFILES]
    comparison_keys = {item.source.comparison_key for item in ordered}
    if len(comparison_keys) != 1:
        source_details = ", ".join(
            f"{item.profile}={item.source.comparison_key}" for item in ordered
        )
        raise RunSourceMismatchError(f"runs describe different sources: {source_details}")

    return RunComparison(
        source=ordered[0].source,
        profiles=tuple(_profile_comparison(item) for item in ordered),
    )


def _require_completed(manifest: ArtifactManifest) -> None:
    if manifest.status is not RunStatus.COMPLETED:
        raise RunNotCompleteError(
            f"run {manifest.run_id!r} is {manifest.status.value}, not completed"
        )
    incomplete_stages = sorted(
        name
        for name, record in manifest.stages.items()
        if record.status is not StageStatus.COMPLETED
    )
    if incomplete_stages:
        raise RunNotCompleteError(
            f"run {manifest.run_id!r} contains incomplete stages: " + ", ".join(incomplete_stages)
        )


def _require_consistent_run_ids(
    manifest: ArtifactManifest,
    fusion: FusionResult,
    note: NoteDocument,
    outcome: _PipelineOutcome,
) -> None:
    expected = manifest.run_id
    observed = {
        "timeline": fusion.run_id,
        "note": note.metadata.run_id,
        "outcome": outcome.run_id,
    }
    mismatches = [f"{name}={value}" for name, value in observed.items() if value != expected]
    for index, evidence in enumerate(fusion.evidence):
        if evidence.run_id != expected:
            mismatches.append(f"evidence[{index}]={evidence.run_id}")
    for index, state in enumerate(fusion.visual_states):
        if state.run_id != expected:
            mismatches.append(f"visual_state[{index}]={state.run_id}")
    for index, window in enumerate(fusion.windows):
        if window.run_id != expected:
            mismatches.append(f"window[{index}]={window.run_id}")
    if mismatches:
        raise RunArtifactError(f"run ID mismatch for {expected!r}: " + ", ".join(mismatches))


def _diagnose_modality(
    modality: EvidenceModality,
    evidence: Iterable[EvidenceSpan],
    *,
    duration_us: int,
) -> EvidenceClassDiagnostics:
    spans = [item for item in evidence if item.modality is modality]
    confidences = [item.confidence for item in spans if item.confidence is not None]
    covered_us = _temporal_coverage(spans, duration_us=duration_us)
    return EvidenceClassDiagnostics(
        modality=modality,
        span_count=len(spans),
        spans_with_confidence=len(confidences),
        average_confidence=_mean(confidences),
        temporal_covered_us=covered_us,
        temporal_coverage_ratio=(covered_us / duration_us if duration_us > 0 else None),
    )


def _temporal_coverage(evidence: Iterable[EvidenceSpan], *, duration_us: int) -> int:
    if duration_us <= 0:
        return 0
    intervals: list[tuple[int, int]] = []
    for item in evidence:
        start = max(0, min(item.start_us, duration_us))
        end = max(0, min(item.end_us, duration_us))
        if end > start:
            intervals.append((start, end))
    if not intervals:
        return 0

    intervals.sort()
    covered_us = 0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        covered_us += current_end - current_start
        current_start, current_end = start, end
    return covered_us + current_end - current_start


def _diagnose_references(
    fusion: FusionResult,
    note: NoteDocument,
) -> EvidenceReferenceDiagnostics:
    timeline_ids = {item.id for item in fusion.evidence}
    note_ids = {item.id for item in note.evidence}
    citation_ids: list[str] = []
    for fact in note.facts:
        citation_ids.extend(fact.evidence_ids)
    for section in note.sections:
        citation_ids.extend(section.evidence_ids)
        for screenshot in section.screenshots:
            citation_ids.extend(screenshot.evidence_ids)

    unique_citations = set(citation_ids)
    cited_timeline_ids = unique_citations & timeline_ids
    unknown_citations = unique_citations - timeline_ids
    note_missing_from_timeline = note_ids - timeline_ids
    timeline_missing_from_note = timeline_ids - note_ids
    all_references_valid = not unknown_citations
    embedded_matches = note_ids == timeline_ids
    return EvidenceReferenceDiagnostics(
        timeline_evidence_count=len(timeline_ids),
        note_evidence_count=len(note_ids),
        citation_occurrence_count=len(citation_ids),
        unique_cited_evidence_count=len(unique_citations),
        cited_timeline_evidence_count=len(cited_timeline_ids),
        citation_coverage_ratio=(
            len(cited_timeline_ids) / len(timeline_ids) if timeline_ids else None
        ),
        unknown_citation_ids=tuple(sorted(unknown_citations)),
        note_evidence_missing_from_timeline_ids=tuple(sorted(note_missing_from_timeline)),
        timeline_evidence_missing_from_note_ids=tuple(sorted(timeline_missing_from_note)),
        uncited_timeline_evidence_ids=tuple(sorted(timeline_ids - unique_citations)),
        all_references_valid=all_references_valid,
        embedded_evidence_matches_timeline=embedded_matches,
        is_complete=all_references_valid and embedded_matches,
    )


def _diagnose_warnings(
    manifest: ArtifactManifest,
    note: NoteDocument,
) -> WarningDiagnostics:
    manifest_warnings = list(manifest.warnings)
    stage_warnings = [warning for stage in manifest.stages.values() for warning in stage.warnings]
    note_warnings = list(note.metadata.quality_warnings)
    all_warnings = manifest_warnings + stage_warnings + note_warnings
    return WarningDiagnostics(
        total_count=len(all_warnings),
        unique_count=len(set(all_warnings)),
        manifest_count=len(manifest_warnings),
        stage_count=len(stage_warnings),
        note_count=len(note_warnings),
        unique_messages=tuple(sorted(set(all_warnings))),
    )


def _diagnose_outputs(
    root: Path,
    outcome: _PipelineOutcome,
    fusion: FusionResult,
) -> OutputDiagnostics:
    declared = [outcome.markdown, outcome.html, outcome.note_document]
    if outcome.pdf is not None:
        declared.append(outcome.pdf)
    missing = tuple(
        sorted(
            artifact.relative_path
            for artifact in declared
            if not _run_relative_file_exists(root, artifact.relative_path)
        )
    )
    return OutputDiagnostics(
        markdown_declared=True,
        html_declared=True,
        pdf_declared=outcome.pdf is not None,
        declared_artifact_count=len(declared),
        existing_artifact_count=len(declared) - len(missing),
        missing_artifact_paths=missing,
        declared_evidence_count=outcome.evidence_count,
        evidence_count_matches_timeline=outcome.evidence_count == len(fusion.evidence),
        declared_visual_state_count=outcome.visual_state_count,
        visual_state_count_matches_timeline=(
            outcome.visual_state_count == len(fusion.visual_states)
        ),
        used_deterministic_note_fallback=outcome.used_deterministic_note_fallback,
    )


def _note_character_count(note: NoteDocument) -> int:
    text_parts = [note.abstract, *note.key_takeaways]
    for section in note.sections:
        text_parts.extend(
            [
                section.title,
                section.summary,
                section.body_markdown,
                *(screenshot.caption for screenshot in section.screenshots),
                *(screenshot.alt_text for screenshot in section.screenshots),
            ]
        )
    text_parts.extend(fact.claim for fact in note.facts)
    for term, meaning in note.glossary.items():
        text_parts.extend((term, meaning))
    return sum(not character.isspace() for text in text_parts for character in text)


def _source_identity(
    source: SourceDescriptor,
    *,
    canonical_url_fallback: str | None,
    platform_fallback: str,
    media_sha256: str,
) -> SourceIdentity:
    platform = source.platform or platform_fallback
    canonical_url = source.canonical_url or canonical_url_fallback
    namespace = (platform or source.kind).strip().casefold()
    if source.source_id and source.source_id.strip():
        comparison_key = f"source-id:{namespace}:{source.source_id.strip()}"
    elif source.kind.strip().casefold() == "local":
        comparison_key = f"local-sha256:{media_sha256}"
    elif canonical_url and canonical_url.strip():
        comparison_key = f"canonical-url:{_normalize_url(canonical_url)}"
    elif _is_url(source.locator):
        comparison_key = f"locator-url:{_normalize_url(source.locator)}"
    else:
        comparison_key = f"locator:{source.kind.strip().casefold()}:{source.locator.strip()}"
    return SourceIdentity(
        kind=source.kind,
        locator=source.locator,
        platform=platform,
        source_id=source.source_id,
        canonical_url=canonical_url,
        comparison_key=comparison_key,
    )


def _normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    normalized_query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/") or "/",
            normalized_query,
            "",
        )
    )


def _is_url(value: str) -> bool:
    parts = urlsplit(value.strip())
    return parts.scheme.casefold() in {"http", "https"} and bool(parts.netloc)


def _profile_comparison(item: RunDiagnostics) -> ProfileComparison:
    return ProfileComparison(
        profile=item.profile,
        run_id=item.run_id,
        media_duration_seconds=item.media_duration_seconds,
        recorded_stage_wall_time_seconds=item.recorded_stage_wall_time_seconds,
        realtime_factor=item.realtime_factor,
        evidence_count=item.evidence_count,
        evidence_temporal_coverage_ratio=item.evidence_temporal_coverage_ratio,
        evidence_spans_with_confidence=item.evidence_spans_with_confidence,
        average_confidence=item.average_confidence,
        conflict_count=item.conflict_count,
        unresolved_conflict_count=item.unresolved_conflict_count,
        visual_state_count=item.visual_state_count,
        screenshot_count=item.note.screenshot_count,
        section_count=item.note.section_count,
        key_takeaway_count=item.note.key_takeaway_count,
        non_whitespace_character_count=item.note.non_whitespace_character_count,
        warning_count=item.warnings.total_count,
        evidence_references_complete=item.evidence_references.is_complete,
        used_deterministic_note_fallback=item.outputs.used_deterministic_note_fallback,
    )


def _run_relative_file_exists(root: Path, relative_path: str) -> bool:
    path = (root / relative_path).resolve()
    return path.is_relative_to(root) and path.is_file()


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _read_model(path: Path, model: type[ModelT]) -> ModelT:
    if not path.is_file():
        raise RunArtifactError(f"required run artifact does not exist: {path}")
    try:
        payload = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RunArtifactError(f"cannot read run artifact {path}: {error}") from error
    try:
        return model.model_validate_json(payload)
    except ValidationError as error:
        raise RunArtifactError(f"invalid run artifact {path}: {error}") from error
