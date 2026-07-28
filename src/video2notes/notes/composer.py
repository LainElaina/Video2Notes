"""Evidence-first fact extraction, drafting, and deterministic fallback."""

from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from video2notes.domain import EvidenceModality, EvidenceSpan
from video2notes.fusion import EvidenceWindow, FusionResult
from video2notes.llm import (
    GenerationError,
    GenerationRequest,
    GenerationResult,
    StructuredGenerationBackend,
)

from .models import (
    FactCard,
    NoteDocument,
    NoteMetadata,
    NoteScreenshot,
    NoteSection,
)


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
    ) -> NoteCompositionResult:
        if fusion.run_id != metadata.run_id:
            raise ValueError("note metadata and fusion result must belong to one run")
        screenshots = screenshots_by_window or {}
        if self.fact_backend is None or self.draft_backend is None:
            note = build_deterministic_note(
                metadata,
                fusion,
                screenshots_by_window=screenshots,
            )
            return NoteCompositionResult(
                note=note,
                warnings=["No note LLM is configured; generated an extractive evidence note."],
                used_deterministic_fallback=True,
            )

        invocations: list[InvocationSummary] = []
        warnings: list[str] = []
        try:
            facts = self._extract_facts(fusion, invocations)
            draft = self._draft(metadata, fusion, facts, invocations)
            note = _note_from_draft(
                metadata,
                fusion,
                facts,
                draft,
                screenshots_by_window=screenshots,
            )
            if self.verifier_backend is not None and facts:
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
    ) -> list[FactCard]:
        if self.fact_backend is None:
            raise RuntimeError("fact backend is not configured")
        evidence_by_id = {item.id: item for item in fusion.evidence}
        facts: list[FactCard] = []
        for window in fusion.windows:
            if not window.evidence_ids:
                continue
            payload = _window_prompt_payload(window, evidence_by_id)
            result = self.fact_backend.generate(
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
    ) -> NoteDraftEnvelope:
        if self.draft_backend is None:
            raise RuntimeError("draft backend is not configured")
        result = self.draft_backend.generate(
            GenerationRequest(
                role="notes.drafter",
                system_prompt=_DRAFT_SYSTEM_PROMPT,
                user_prompt=json.dumps(
                    {
                        "title": metadata.title,
                        "duration_us": metadata.duration_us,
                        "facts": [item.model_dump(mode="json") for item in facts],
                        "windows": [item.model_dump(mode="json") for item in fusion.windows],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                schema_name="video_note_document",
                json_schema=NoteDraftEnvelope.model_json_schema(),
                temperature=0.2,
                max_output_tokens=16_384,
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
) -> NoteDocument:
    """Generate a useful extractive Markdown source without any LLM dependency."""

    if fusion.run_id != metadata.run_id:
        raise ValueError("note metadata and fusion result must belong to one run")
    screenshots = screenshots_by_window or {}
    evidence_by_id = {item.id: item for item in fusion.evidence}
    facts: list[FactCard] = []
    sections: list[NoteSection] = []
    takeaway_candidates: list[str] = []

    for section_index, window in enumerate(fusion.windows):
        window_evidence = [
            evidence_by_id[item] for item in window.evidence_ids if item in evidence_by_id
        ]
        textual = [item for item in window_evidence if _evidence_text(item)]
        if not textual and not screenshots.get(window.id):
            continue

        unique_texts: set[str] = set()
        fact_ids: list[str] = []
        body_lines: list[str] = []
        for evidence in textual:
            text = _evidence_text(evidence)
            normalized = "".join(text.casefold().split())
            if not normalized or normalized in unique_texts:
                continue
            unique_texts.add(normalized)
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
            if len(takeaway_candidates) < 8:
                takeaway_candidates.append(text)
            label = _modality_label(evidence.modality)
            body_lines.append(f"- **{label}** {text}")
            if len(fact_ids) >= 16:
                break

        title_source = textual[0] if textual else None
        title_text = _evidence_text(title_source) if title_source is not None else "关键画面"
        title = _short_title(title_text, fallback=f"片段 {section_index + 1}")
        summary = title_text
        sections.append(
            NoteSection(
                id=f"section-{section_index + 1:03d}",
                title=title,
                start_us=window.start_us,
                end_us=window.end_us,
                summary=summary,
                body_markdown="\n".join(body_lines) or "本节仅包含视觉证据，请查看关键截图。",
                evidence_ids=[item.id for item in window_evidence],
                fact_ids=fact_ids,
                screenshots=list(screenshots.get(window.id, [])),
            )
        )

    abstract = (
        "；".join(takeaway_candidates[:3])
        if takeaway_candidates
        else "视频中没有可安全提取的文字证据。"
    )
    return NoteDocument(
        metadata=metadata,
        abstract=abstract,
        key_takeaways=takeaway_candidates[:5],
        sections=sections,
        facts=facts,
        evidence=fusion.evidence,
    )


def _note_from_draft(
    metadata: NoteMetadata,
    fusion: FusionResult,
    facts: list[FactCard],
    draft: NoteDraftEnvelope,
    *,
    screenshots_by_window: Mapping[str, list[NoteScreenshot]],
) -> NoteDocument:
    evidence_ids = {item.id for item in fusion.evidence}
    fact_ids = {item.id for item in facts}
    all_screenshots = [
        screenshot
        for window_screenshots in screenshots_by_window.values()
        for screenshot in window_screenshots
    ]
    sections: list[NoteSection] = []
    for index, candidate in enumerate(draft.sections):
        unknown_evidence = set(candidate.evidence_ids) - evidence_ids
        unknown_facts = set(candidate.fact_ids) - fact_ids
        if unknown_evidence or unknown_facts:
            raise ValueError("draft cited unknown fact or evidence IDs")
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
                screenshots=section_screenshots,
            )
        )
    return NoteDocument(
        metadata=metadata,
        abstract=draft.abstract,
        key_takeaways=draft.key_takeaways,
        sections=sections,
        facts=facts,
        evidence=fusion.evidence,
        glossary=draft.glossary,
    )


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

_DRAFT_SYSTEM_PROMPT = """你是中文视频笔记编辑。只能使用给定 fact cards 组织文稿，不得添加外部事实。
按主题和物理时间组织章节；每一章节都要引用实际 fact_id 与 evidence_id。
保持 start_us/end_us 在素材时长内。
正文使用清晰 Markdown，可包含列表、步骤、表格和代码，但不要重复章节标题。明确标注待核对内容。
输出应适合独立阅读，同时保持可回看性。严格返回指定 JSON schema。"""

_VERIFY_SYSTEM_PROMPT = """逐条判断 fact 是否被它引用的 evidence 直接支持。
不评价文风；重点检查数字、人名、专有名词、归因以及 OCR 猜测。证据不足就 supported=false。
每个输入 fact_id 恰好返回一次，严格返回指定 JSON schema。"""
