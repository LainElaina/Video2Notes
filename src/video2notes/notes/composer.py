"""Evidence-first fact extraction, drafting, and deterministic fallback."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from video2notes.domain import EvidenceModality, EvidenceSpan
from video2notes.fusion import EvidenceWindow, FusionResult
from video2notes.llm import (
    GenerationError,
    GenerationRequest,
    GenerationResult,
    ImageInput,
    StructuredGenerationBackend,
)
from video2notes.materials import MaterialKind, MaterialStatus, RunMaterial

from .models import (
    FactCard,
    NoteDocument,
    NoteMetadata,
    NoteScreenshot,
    NoteSection,
    SupportingMaterial,
    SupportingMaterialKind,
)
from .reporting import ReportPreset, ReportSpec, ResolvedReportSpec


class ComposerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateFact(ComposerModel):
    claim: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    kind: str = "fact"
    needs_review: bool = False


class FactExtractionEnvelope(ComposerModel):
    facts: list[CandidateFact]


class CandidateSection(ComposerModel):
    title: str = Field(min_length=1)
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    summary: str
    body_markdown: str
    evidence_ids: list[str]
    fact_ids: list[str]
    material_ids: list[str] = Field(default_factory=list)


class NoteDraftEnvelope(ComposerModel):
    abstract: str
    key_takeaways: list[str]
    sections: list[CandidateSection]
    glossary: dict[str, str] = Field(default_factory=dict)


class VerificationItem(ComposerModel):
    fact_id: str
    supported: bool
    reason: str


class VerificationEnvelope(ComposerModel):
    items: list[VerificationItem]


class InvocationSummary(ComposerModel):
    provider: str
    model: str
    role: str
    latency_seconds: float
    input_tokens: int | None = None
    output_tokens: int | None = None


class NoteCompositionResult(ComposerModel):
    note: NoteDocument
    invocations: list[InvocationSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    used_deterministic_fallback: bool = False


class EvidenceNoteComposer:
    """Use independently routed models for extraction, drafting, and verification."""

    def __init__(
        self,
        *,
        fact_backend: StructuredGenerationBackend | None = None,
        draft_backend: StructuredGenerationBackend | None = None,
        verifier_backend: StructuredGenerationBackend | None = None,
        fallback_on_error: bool = True,
    ):
        if (fact_backend is None) != (draft_backend is None):
            raise ValueError("fact and draft backends must be configured together")
        self.fact_backend = fact_backend
        self.draft_backend = draft_backend
        self.verifier_backend = verifier_backend
        self.fallback_on_error = fallback_on_error

    def compose(
        self,
        metadata: NoteMetadata,
        fusion: FusionResult,
        *,
        screenshots_by_window: Mapping[str, list[NoteScreenshot]] | None = None,
        report_spec: ReportSpec | None = None,
        supporting_materials: Sequence[RunMaterial | SupportingMaterial] = (),
        artifact_root: str | Path | None = None,
        verification_passes: int = 1,
        max_model_concurrency: int = 1,
    ) -> NoteCompositionResult:
        if fusion.run_id != metadata.run_id:
            raise ValueError("note metadata and fusion result must belong to one run")
        if verification_passes < 0:
            raise ValueError("verification_passes cannot be negative")
        if max_model_concurrency < 1:
            raise ValueError("max_model_concurrency must be positive")
        resolved_report = (report_spec or ReportSpec()).resolve()
        metadata = metadata.model_copy(
            update={
                "report_preset": resolved_report.preset.value,
                "report_template_version": resolved_report.template_version,
            }
        )
        screenshots = screenshots_by_window or {}
        materials = _canonical_supporting_materials(supporting_materials)
        if self.fact_backend is None or self.draft_backend is None:
            note = build_deterministic_note(
                metadata,
                fusion,
                screenshots_by_window=screenshots,
                report_spec=report_spec,
                supporting_materials=materials,
            )
            return NoteCompositionResult(
                note=note,
                warnings=["No note LLM is configured; generated an extractive evidence note."],
                used_deterministic_fallback=True,
            )

        invocations: list[InvocationSummary] = []
        warnings: list[str] = []
        try:
            facts = self._extract_facts(
                fusion,
                invocations,
                max_model_concurrency=max_model_concurrency,
            )
            draft = self._draft(
                metadata,
                fusion,
                facts,
                invocations,
                report_spec=resolved_report,
                supporting_materials=materials,
                artifact_root=artifact_root,
            )
            note = _note_from_draft(
                metadata,
                fusion,
                facts,
                draft,
                screenshots_by_window=screenshots,
                report_spec=resolved_report,
                supporting_materials=materials,
            )
            if self.verifier_backend is not None and facts:
                for _ in range(verification_passes):
                    if not note.facts:
                        break
                    note = self._verify(note, fusion, invocations)
        except (GenerationError, ValueError) as error:
            if not self.fallback_on_error:
                raise
            warnings.append(
                f"Structured note generation failed ({type(error).__name__}); "
                "used deterministic evidence fallback."
            )
            note = build_deterministic_note(
                metadata,
                fusion,
                screenshots_by_window=screenshots,
                report_spec=report_spec,
                supporting_materials=materials,
            )
            return NoteCompositionResult(
                note=note,
                invocations=invocations,
                warnings=warnings,
                used_deterministic_fallback=True,
            )

        return NoteCompositionResult(
            note=note,
            invocations=invocations,
            warnings=warnings,
        )

    def _extract_facts(
        self,
        fusion: FusionResult,
        invocations: list[InvocationSummary],
        *,
        max_model_concurrency: int,
    ) -> list[FactCard]:
        if self.fact_backend is None:
            raise RuntimeError("fact backend is not configured")
        evidence_by_id = {item.id: item for item in fusion.evidence}
        windows = [window for window in fusion.windows if window.evidence_ids]

        def generate(window: EvidenceWindow) -> GenerationResult:
            payload = _window_prompt_payload(window, evidence_by_id)
            assert self.fact_backend is not None
            return self.fact_backend.generate(
                GenerationRequest(
                    role="notes.fact_extractor",
                    system_prompt=_FACT_SYSTEM_PROMPT,
                    user_prompt=json.dumps(payload, ensure_ascii=False, indent=2),
                    schema_name="video_fact_cards",
                    json_schema=FactExtractionEnvelope.model_json_schema(),
                    temperature=0,
                    max_output_tokens=6_144,
                )
            )

        worker_count = min(max_model_concurrency, len(windows))
        if worker_count > 1:
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="video2notes-facts",
            ) as executor:
                results = list(executor.map(generate, windows))
        else:
            results = [generate(window) for window in windows]

        facts: list[FactCard] = []
        for window, result in zip(windows, results, strict=True):
            invocations.append(_invocation_summary(result))
            envelope = FactExtractionEnvelope.model_validate(result.parsed)
            allowed = set(window.evidence_ids)
            for index, candidate in enumerate(envelope.facts):
                unknown = set(candidate.evidence_ids) - allowed
                if unknown:
                    raise ValueError(
                        "fact extractor cited evidence outside its window: "
                        + ", ".join(sorted(unknown))
                    )
                facts.append(
                    FactCard(
                        id=f"fact-{window.id}-{index:03d}",
                        claim=candidate.claim,
                        evidence_ids=candidate.evidence_ids,
                        confidence=candidate.confidence,
                        kind=candidate.kind,
                        needs_review=candidate.needs_review,
                    )
                )
        return facts

    def _draft(
        self,
        metadata: NoteMetadata,
        fusion: FusionResult,
        facts: list[FactCard],
        invocations: list[InvocationSummary],
        *,
        report_spec: ResolvedReportSpec,
        supporting_materials: Sequence[SupportingMaterial],
        artifact_root: str | Path | None,
    ) -> NoteDraftEnvelope:
        if self.draft_backend is None:
            raise RuntimeError("draft backend is not configured")
        material_payload, material_images = _material_prompt_payload(
            supporting_materials,
            artifact_root=artifact_root,
        )
        result = self.draft_backend.generate(
            GenerationRequest(
                role="notes.drafter",
                system_prompt=_DRAFT_SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    {
                        "title": metadata.title,
                        "duration_us": metadata.duration_us,
                        "report_spec": report_spec.model_dump(mode="json"),
                        "facts": [item.model_dump(mode="json") for item in facts],
                        "windows": [item.model_dump(mode="json") for item in fusion.windows],
                        "supporting_materials": material_payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                schema_name="video_note_document",
                json_schema=NoteDraftEnvelope.model_json_schema(),
                images=material_images,
                temperature=0.2,
                max_output_tokens=report_spec.max_output_tokens,
            )
        )
        invocations.append(_invocation_summary(result))
        return NoteDraftEnvelope.model_validate(result.parsed)

    def _verify(
        self,
        note: NoteDocument,
        fusion: FusionResult,
        invocations: list[InvocationSummary],
    ) -> NoteDocument:
        if self.verifier_backend is None:
            return note
        evidence_by_id = {item.id: item for item in fusion.evidence}
        result = self.verifier_backend.generate(
            GenerationRequest(
                role="notes.verifier",
                system_prompt=_VERIFY_SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    {
                        "facts": [item.model_dump(mode="json") for item in note.facts],
                        "evidence": {
                            identifier: evidence_by_id[identifier].model_dump(mode="json")
                            for identifier in sorted(
                                {
                                    evidence_id
                                    for fact in note.facts
                                    for evidence_id in fact.evidence_ids
                                }
                            )
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                schema_name="video_note_verification",
                json_schema=VerificationEnvelope.model_json_schema(),
                temperature=0,
                max_output_tokens=4_096,
            )
        )
        invocations.append(_invocation_summary(result))
        verification = VerificationEnvelope.model_validate(result.parsed)
        facts_by_id = {item.id: item for item in note.facts}
        seen: set[str] = set()
        for item in verification.items:
            fact = facts_by_id.get(item.fact_id)
            if fact is None:
                raise ValueError(f"verifier referenced unknown fact: {item.fact_id}")
            if item.fact_id in seen:
                raise ValueError(f"verifier repeated fact: {item.fact_id}")
            seen.add(item.fact_id)
            if not item.supported:
                fact.needs_review = True
        return note


def build_deterministic_note(
    metadata: NoteMetadata,
    fusion: FusionResult,
    *,
    screenshots_by_window: Mapping[str, list[NoteScreenshot]] | None = None,
    report_spec: ReportSpec | None = None,
    supporting_materials: Sequence[RunMaterial | SupportingMaterial] = (),
) -> NoteDocument:
    """Generate a useful extractive Markdown source without any LLM dependency."""

    if fusion.run_id != metadata.run_id:
        raise ValueError("note metadata and fusion result must belong to one run")
    resolved_report = (report_spec or ReportSpec()).resolve()
    metadata = metadata.model_copy(
        update={
            "report_preset": resolved_report.preset.value,
            "report_template_version": resolved_report.template_version,
        }
    )
    screenshots = screenshots_by_window or {}
    materials = _canonical_supporting_materials(supporting_materials)
    evidence_by_id = {item.id: item for item in fusion.evidence}
    facts: list[FactCard] = []
    sections: list[NoteSection] = []
    speech_takeaway_candidates: list[EvidenceSpan] = []
    ocr_takeaway_candidates: list[EvidenceSpan] = []
    emitted_evidence_ids: set[str] = set()
    emitted_texts: dict[EvidenceModality, list[str]] = {}
    ocr_fact_limit_per_window = (
        8 if len(fusion.windows) <= 4 else 4 if len(fusion.windows) <= 8 else 2
    )

    for section_index, window in enumerate(fusion.windows):
        window_evidence = [
            evidence_by_id[item] for item in window.evidence_ids if item in evidence_by_id
        ]
        # Source metadata already has a dedicated, structured home in NoteMetadata.
        # Keeping it in the evidence graph preserves provenance, but turning a
        # filename/title/author tuple into a repeated "fact" makes deterministic
        # fallback notes read like an import manifest instead of a video summary.
        textual = [
            item
            for item in window_evidence
            if item.modality is not EvidenceModality.METADATA and _evidence_text(item)
        ]
        if not textual and not screenshots.get(window.id):
            continue

        fact_ids: list[str] = []
        body_lines: list[str] = []
        selected_textual: list[EvidenceSpan] = []
        ocr_fact_limit = min(
            resolved_report.max_facts_per_section,
            ocr_fact_limit_per_window,
        )
        ocr_fact_count = 0
        for evidence in sorted(textual, key=_deterministic_evidence_sort_key):
            if evidence.id in emitted_evidence_ids:
                continue
            text = _evidence_text(evidence)
            normalized = _canonical_note_text(text)
            if not normalized or _duplicates_emitted_text(
                normalized,
                evidence.modality,
                emitted_texts,
            ):
                continue
            if evidence.modality is EvidenceModality.OCR:
                if (
                    resolved_report.preset is ReportPreset.CONCISE
                    and _is_persistent_edge_chrome(evidence)
                ):
                    continue
                if ocr_fact_count >= ocr_fact_limit:
                    continue
                ocr_fact_count += 1
            fact_id = f"fact-{window.id}-{len(fact_ids):03d}"
            facts.append(
                FactCard(
                    id=fact_id,
                    claim=text,
                    evidence_ids=[evidence.id],
                    confidence=evidence.confidence,
                    kind=_fact_kind(evidence.modality),
                    needs_review=(evidence.confidence is not None and evidence.confidence < 0.5),
                )
            )
            fact_ids.append(fact_id)
            selected_textual.append(evidence)
            emitted_evidence_ids.add(evidence.id)
            emitted_texts.setdefault(evidence.modality, []).append(normalized)
            if evidence.modality in {
                EvidenceModality.ASR,
                EvidenceModality.PLATFORM_CAPTION,
            }:
                speech_takeaway_candidates.append(evidence)
            elif evidence.modality is EvidenceModality.OCR:
                ocr_takeaway_candidates.append(evidence)
            label = _modality_label(evidence.modality)
            body_lines.append(f"- **{label}** {text}")
            if len(fact_ids) >= resolved_report.max_facts_per_section:
                break

        if not fact_ids and not screenshots.get(window.id):
            continue
        title_source = selected_textual[0] if selected_textual else None
        title_text = _evidence_text(title_source) if title_source is not None else "关键画面"
        title = _short_title(title_text, fallback=f"片段 {section_index + 1}")
        summary = title_text
        section_number = len(sections) + 1
        sections.append(
            NoteSection(
                id=f"section-{section_number:03d}",
                title=title,
                start_us=window.start_us,
                end_us=window.end_us,
                summary=summary,
                body_markdown="\n".join(body_lines) or "本节仅包含视觉证据，请查看关键截图。",
                evidence_ids=[item.id for item in window_evidence],
                fact_ids=fact_ids,
                material_ids=_timed_material_ids_for_window(
                    materials,
                    start_us=window.start_us,
                    end_us=window.end_us,
                ),
                screenshots=list(screenshots.get(window.id, [])),
            )
        )

    sections = _coalesce_deterministic_sections(
        sections,
        quality_mode=metadata.quality_mode,
        max_sections=resolved_report.max_sections,
    )
    takeaway_candidates = _select_deterministic_takeaways(
        speech_takeaway_candidates,
        ocr_takeaway_candidates,
        limit=resolved_report.max_takeaways,
    )
    abstract = (
        "；".join(takeaway_candidates[:3])
        if takeaway_candidates
        else "视频中没有可安全提取的文字证据。"
    )
    return NoteDocument(
        metadata=metadata,
        abstract=abstract,
        key_takeaways=takeaway_candidates[: resolved_report.max_takeaways],
        sections=sections,
        facts=facts,
        evidence=fusion.evidence,
        supporting_materials=materials,
    )


def _deterministic_evidence_sort_key(
    evidence: EvidenceSpan,
) -> tuple[int, int, int, float, int, int, str]:
    text_length = len(_canonical_note_text(_evidence_text(evidence)))
    confidence = evidence.confidence if evidence.confidence is not None else 0.5
    if evidence.modality in {
        EvidenceModality.ASR,
        EvidenceModality.PLATFORM_CAPTION,
    }:
        return (0, 0, evidence.start_us, 0.0, 0, evidence.end_us, evidence.id)
    if evidence.modality is EvidenceModality.OCR:
        return (
            1,
            _ocr_information_tier(evidence),
            0,
            -confidence,
            -text_length,
            evidence.start_us,
            evidence.id,
        )
    return (2, 0, evidence.start_us, -confidence, -text_length, evidence.end_us, evidence.id)


def _ocr_information_tier(evidence: EvidenceSpan) -> int:
    text = _evidence_text(evidence)
    canonical = _canonical_note_text(text)
    if any(character.isdigit() for character in text):
        semantic_characters = sum(character.isalpha() for character in text)
        if semantic_characters >= 3 and len(canonical) >= 8:
            return 0
        if semantic_characters >= 2 and len(canonical) >= 6:
            return 1
        # Short counters remain eligible facts and exact numeric changes are
        # never deduplicated, but they should not outrank a complete sentence.
        return 2
    ascii_letters = [
        character for character in text if character.isascii() and character.isalpha()
    ]
    if (
        ascii_letters
        and len(ascii_letters) >= 2
        and all(character.isupper() for character in ascii_letters)
        and not any(not character.isascii() and character.isalpha() for character in text)
    ):
        return 3
    if len(canonical) >= 8:
        return 0
    if len(canonical) >= 5:
        return 1
    if len(canonical) >= 2:
        return 2
    return 4


def _select_deterministic_takeaways(
    speech: Sequence[EvidenceSpan],
    ocr: Sequence[EvidenceSpan],
    *,
    limit: int,
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()

    def add(evidence: EvidenceSpan) -> None:
        text = _evidence_text(evidence)
        canonical = _canonical_note_text(text)
        if (
            evidence.modality
            in {EvidenceModality.ASR, EvidenceModality.PLATFORM_CAPTION}
            and len(canonical) < 2
        ):
            # Keep acknowledgements such as “好”/“嗯” in the timed fact layer,
            # but do not spend scarce overview slots on non-informative fillers.
            return
        if canonical and canonical not in seen and len(selected) < limit:
            seen.add(canonical)
            selected.append(text)

    for evidence in sorted(speech, key=lambda item: (item.start_us, item.end_us, item.id)):
        add(evidence)
    if len(selected) >= limit:
        return selected
    for evidence in sorted(
        ocr,
        key=lambda item: (
            _ocr_information_tier(item),
            -(item.confidence if item.confidence is not None else 0.5),
            -len(_canonical_note_text(_evidence_text(item))),
            item.start_us,
            item.id,
        ),
    ):
        if _ocr_information_tier(evidence) <= 1 and not _is_persistent_edge_chrome(evidence):
            add(evidence)
    return selected


_SECTION_TARGET_US = {
    "fast": 5_000_000,
    "balanced": 4_000_000,
    "accurate": 3_000_000,
}


def _coalesce_deterministic_sections(
    sections: Sequence[NoteSection],
    *,
    quality_mode: str,
    max_sections: int,
) -> list[NoteSection]:
    if not sections:
        return []
    preferred = _SECTION_TARGET_US.get(quality_mode.casefold(), 4_000_000)
    total_span = max(1, sections[-1].end_us - sections[0].start_us)
    capacity_floor = (total_span + max_sections - 1) // max_sections
    readable_floor = max(round(preferred * 0.75), capacity_floor)
    groups: list[list[NoteSection]] = []
    pending: list[NoteSection] = []
    for section in sections:
        pending.append(section)
        if pending[-1].end_us - pending[0].start_us >= readable_floor:
            groups.append(pending)
            pending = []
    if pending:
        if groups:
            groups[-1].extend(pending)
        else:
            groups.append(pending)
    while len(groups) > max_sections:
        groups[-2].extend(groups.pop())
    return [
        _merge_deterministic_section_group(group, index=index)
        for index, group in enumerate(groups, start=1)
    ]


def _merge_deterministic_section_group(
    group: Sequence[NoteSection],
    *,
    index: int,
) -> NoteSection:
    first = group[0]
    representative = max(
        group,
        key=lambda section: (
            int(
                "**语音**" in section.body_markdown
                or "**平台字幕**" in section.body_markdown
            ),
            len(_canonical_note_text(section.title)),
            -section.start_us,
        ),
    )
    return NoteSection(
        id=f"section-{index:03d}",
        title=representative.title,
        start_us=first.start_us,
        end_us=group[-1].end_us,
        summary=representative.summary,
        body_markdown="\n\n".join(
            section.body_markdown for section in group if section.body_markdown
        ),
        evidence_ids=_stable_unique(
            identifier for section in group for identifier in section.evidence_ids
        ),
        fact_ids=_stable_unique(
            identifier for section in group for identifier in section.fact_ids
        ),
        material_ids=_stable_unique(
            identifier for section in group for identifier in section.material_ids
        ),
        screenshots=[screenshot for section in group for screenshot in section.screenshots],
    )


def _stable_unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _canonical_note_text(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())


def _duplicates_emitted_text(
    normalized: str,
    modality: EvidenceModality,
    emitted: Mapping[EvidenceModality, list[str]],
) -> bool:
    # Only exact canonical text is safe to suppress globally.  Fuzzy OCR text
    # can differ by a single digit (for example 20.0 -> 20.1) while describing
    # a real state change, so similarity-based collapsing belongs in the
    # time/track-aware OCR stage rather than the final note composer.
    return normalized in emitted.get(modality, [])


def _is_persistent_edge_chrome(evidence: EvidenceSpan) -> bool:
    count = evidence.provenance.get("observation_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 3:
        return False
    text = _canonical_note_text(_evidence_text(evidence))
    if not text or len(text) > 24:
        return False
    frame_width = evidence.provenance.get("frame_width")
    frame_height = evidence.provenance.get("frame_height")
    if (
        not isinstance(frame_width, (int, float))
        or not isinstance(frame_height, (int, float))
        or isinstance(frame_width, bool)
        or isinstance(frame_height, bool)
        or frame_width <= 0
        or frame_height <= 0
    ):
        return False
    for box in evidence.bounding_boxes:
        if box.coordinate_space == "normalized":
            center_x = box.x + box.width / 2
            center_y = box.y + box.height / 2
        elif box.coordinate_space == "pixels":
            center_x = (box.x + box.width / 2) / float(frame_width)
            center_y = (box.y + box.height / 2) / float(frame_height)
        else:
            continue
        if center_x <= 0.12 or center_x >= 0.88 or center_y <= 0.08 or center_y >= 0.92:
            return True
    return False


def _note_from_draft(
    metadata: NoteMetadata,
    fusion: FusionResult,
    facts: list[FactCard],
    draft: NoteDraftEnvelope,
    *,
    screenshots_by_window: Mapping[str, list[NoteScreenshot]],
    report_spec: ResolvedReportSpec,
    supporting_materials: Sequence[SupportingMaterial],
) -> NoteDocument:
    evidence_ids = {item.id for item in fusion.evidence}
    fact_ids = {item.id for item in facts}
    material_ids = {item.id for item in supporting_materials}
    all_screenshots = [
        screenshot
        for window_screenshots in screenshots_by_window.values()
        for screenshot in window_screenshots
    ]
    sections: list[NoteSection] = []
    for index, candidate in enumerate(draft.sections[: report_spec.max_sections]):
        unknown_evidence = set(candidate.evidence_ids) - evidence_ids
        unknown_facts = set(candidate.fact_ids) - fact_ids
        unknown_materials = set(candidate.material_ids) - material_ids
        if unknown_evidence or unknown_facts or unknown_materials:
            raise ValueError("draft cited unknown fact, evidence, or supporting material IDs")
        if candidate.end_us < candidate.start_us:
            raise ValueError("draft section has a reversed interval")
        section_screenshots = [
            item
            for item in all_screenshots
            if candidate.start_us <= item.timestamp_us <= candidate.end_us
        ]
        sections.append(
            NoteSection(
                id=f"section-{index + 1:03d}",
                title=candidate.title,
                start_us=candidate.start_us,
                end_us=candidate.end_us,
                summary=candidate.summary,
                body_markdown=candidate.body_markdown,
                evidence_ids=candidate.evidence_ids,
                fact_ids=candidate.fact_ids,
                material_ids=candidate.material_ids,
                screenshots=section_screenshots,
            )
        )
    return NoteDocument(
        metadata=metadata,
        abstract=draft.abstract,
        key_takeaways=draft.key_takeaways[: report_spec.max_takeaways],
        sections=sections,
        facts=facts,
        evidence=fusion.evidence,
        supporting_materials=list(supporting_materials),
        glossary=draft.glossary if report_spec.include_glossary else {},
    )


def _canonical_supporting_materials(
    materials: Sequence[RunMaterial | SupportingMaterial],
) -> list[SupportingMaterial]:
    canonical: list[SupportingMaterial] = []
    for material in materials:
        if isinstance(material, SupportingMaterial):
            canonical.append(material)
            continue
        if material.status is not MaterialStatus.ACTIVE:
            continue
        canonical.append(
            SupportingMaterial(
                id=material.id,
                kind=(
                    SupportingMaterialKind.TEXT
                    if material.kind is MaterialKind.TEXT
                    else SupportingMaterialKind.IMAGE
                ),
                title=material.title,
                artifact_path=material.artifact.relative_path,
                media_type=material.media_type,
                sha256=material.sha256,
                size_bytes=material.size_bytes,
                text_content=material.text_content,
                original_name=material.original_name,
                start_us=material.start_us,
                end_us=material.end_us,
            )
        )
    identifiers = [item.id for item in canonical]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("supporting material IDs must be unique")
    return canonical


def _material_prompt_payload(
    materials: Sequence[SupportingMaterial],
    *,
    artifact_root: str | Path | None,
) -> tuple[list[dict[str, object]], list[ImageInput]]:
    root = Path(artifact_root).expanduser().resolve() if artifact_root is not None else None
    payload: list[dict[str, object]] = []
    images: list[ImageInput] = []
    for material in materials:
        item = material.model_dump(mode="json")
        item["source_classification"] = "external_supporting_material"
        if material.kind is SupportingMaterialKind.IMAGE and root is not None:
            image_path = (root / material.artifact_path).resolve()
            if image_path.is_relative_to(root) and image_path.is_file():
                encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
                images.append(
                    ImageInput(
                        data_url=f"data:{material.media_type};base64,{encoded}",
                        detail="high",
                    )
                )
                item["image_input_index"] = len(images) - 1
        payload.append(item)
    return payload, images


def _timed_material_ids_for_window(
    materials: Sequence[SupportingMaterial],
    *,
    start_us: int,
    end_us: int,
) -> list[str]:
    """Attach only explicitly timed material; global material stays in the appendix."""

    return [
        material.id
        for material in materials
        if material.start_us is not None
        and material.end_us is not None
        and material.start_us < end_us
        and start_us < material.end_us
    ]


def _window_prompt_payload(
    window: EvidenceWindow,
    evidence_by_id: Mapping[str, EvidenceSpan],
) -> dict[str, object]:
    return {
        "window": window.model_dump(mode="json"),
        "evidence": [
            evidence_by_id[identifier].model_dump(mode="json")
            for identifier in window.evidence_ids
            if identifier in evidence_by_id
        ],
    }


def _invocation_summary(result: GenerationResult) -> InvocationSummary:
    return InvocationSummary(
        provider=result.provider,
        model=result.model,
        role=result.role,
        latency_seconds=result.latency_seconds,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


def _evidence_text(evidence: EvidenceSpan) -> str:
    return (evidence.normalized_text or evidence.raw_text or "").strip()


def _short_title(text: str, *, fallback: str) -> str:
    compact = " ".join(text.split())
    if not compact:
        return fallback
    return compact if len(compact) <= 32 else compact[:31].rstrip() + "…"


def _fact_kind(modality: EvidenceModality) -> str:
    if modality in {EvidenceModality.ASR, EvidenceModality.PLATFORM_CAPTION}:
        return "speech"
    if modality is EvidenceModality.OCR:
        return "screen_text"
    return modality.value


def _modality_label(modality: EvidenceModality) -> str:
    return {
        EvidenceModality.ASR: "语音",
        EvidenceModality.PLATFORM_CAPTION: "平台字幕",
        EvidenceModality.OCR: "画面文字",
        EvidenceModality.VISUAL: "画面",
        EvidenceModality.METADATA: "元数据",
    }[modality]


_FACT_SYSTEM_PROMPT = """你是证据抽取器。
只提取输入 JSON 中可直接支持的原子事实、步骤、定义、数值和术语。
每条事实必须引用当前 window 内至少一个 evidence_id；不得引用未提供的 ID，不得用常识补全不可读文字。
OCR 与语音是互补来源，不要因为文字不同就把它们互相覆盖。
证据冲突时保留候选并将 needs_review 设为 true。
严格返回指定 JSON schema，不要输出解释。"""

_DRAFT_SYSTEM_PROMPT = """你是中文视频笔记编辑。
只能使用给定 fact cards 与 supporting_materials 组织文稿，
不得添加未提供的事实。supporting_materials 是用户从评论区、网页或本地文件加入的外部补充资料，
绝不能把其中内容伪装成视频原话、画面文字或视频内已经证实的事实；引用它们的章节必须填写对应
material_ids，并在正文中明确标注“外部补充资料”或等价措辞。图片内容只在对应 image_input_index
存在时才可进行视觉解读；只有路径而没有图像输入时不得猜测图片内容。
必须遵守输入 report_spec 中的 audience、editorial_goal、max_sections、max_takeaways
和 include_glossary。
按主题和物理时间组织章节；每一章节都要引用实际 fact_id 与 evidence_id。
保持 start_us/end_us 在素材时长内。
正文使用清晰 Markdown，可包含列表、步骤、表格和代码，但不要重复章节标题。明确标注待核对内容。
输出应适合独立阅读，同时保持可回看性。严格返回指定 JSON schema。"""

_VERIFY_SYSTEM_PROMPT = """逐条判断 fact 是否被它引用的 evidence 直接支持。
不评价文风；重点检查数字、人名、专有名词、归因以及 OCR 猜测。证据不足就 supported=false。
每个输入 fact_id 恰好返回一次，严格返回指定 JSON schema。"""
