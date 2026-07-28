"""Window-local policy for selectively invoking a second ASR backend."""

from __future__ import annotations

from collections.abc import Sequence
from difflib import SequenceMatcher
from enum import StrEnum
from itertools import product
from typing import Self

from pydantic import Field, model_validator

from video2notes.domain import EvidenceModality, EvidenceSpan

from .models import AudioModel


class SecondaryASRReason(StrEnum):
    MISSING_PRIMARY = "missing_primary"
    LOW_PRIMARY_CONFIDENCE = "low_primary_confidence"
    CAPTION_CONFLICT = "caption_conflict"
    LANGUAGE_UNCERTAIN = "language_uncertain"
    LANGUAGE_CONFLICT = "language_conflict"


class SecondaryASRPolicy(AudioModel):
    low_confidence_threshold: float = Field(default=0.72, ge=0, le=1)
    caption_similarity_threshold: float = Field(default=0.70, ge=0, le=1)
    language_confidence_threshold: float = Field(default=0.65, ge=0, le=1)


class SecondaryASRDecision(AudioModel):
    window_start_us: int = Field(ge=0)
    window_end_us: int = Field(ge=0)
    requires_secondary: bool
    reasons: list[SecondaryASRReason]
    primary_evidence_ids: list[str]
    caption_evidence_ids: list[str]
    lowest_primary_confidence: float | None = Field(default=None, ge=0, le=1)
    lowest_caption_similarity: float | None = Field(default=None, ge=0, le=1)
    lowest_language_confidence: float | None = Field(default=None, ge=0, le=1)
    language_conflict_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.window_end_us <= self.window_start_us:
            raise ValueError("secondary-ASR window must have positive duration")
        if self.requires_secondary != bool(self.reasons):
            raise ValueError("secondary-ASR decision and reason list disagree")
        return self


def evaluate_secondary_asr_window(
    *,
    window_start_us: int,
    window_end_us: int,
    primary_asr: list[EvidenceSpan],
    platform_captions: list[EvidenceSpan],
    language_hints: Sequence[str] = (),
    policy: SecondaryASRPolicy | None = None,
) -> SecondaryASRDecision:
    """Evaluate confidence and only co-temporal caption disagreements."""

    selected_policy = policy or SecondaryASRPolicy()
    if window_end_us <= window_start_us:
        raise ValueError("secondary-ASR window must have positive duration")
    for item in primary_asr:
        if item.modality is not EvidenceModality.ASR:
            raise ValueError("primary_asr can only contain ASR evidence")
    for item in platform_captions:
        if item.modality is not EvidenceModality.PLATFORM_CAPTION:
            raise ValueError("platform_captions can only contain platform-caption evidence")

    primary = [
        item for item in primary_asr if _overlap_in_window(item, window_start_us, window_end_us) > 0
    ]
    captions = [
        item
        for item in platform_captions
        if _overlap_in_window(item, window_start_us, window_end_us) > 0
    ]
    reasons: list[SecondaryASRReason] = []
    confidences = [item.confidence for item in primary if item.confidence is not None]
    lowest_confidence = min(confidences, default=None)
    if not primary and captions:
        reasons.append(SecondaryASRReason.MISSING_PRIMARY)
    elif len(confidences) != len(primary) or (
        lowest_confidence is not None
        and lowest_confidence < selected_policy.low_confidence_threshold
    ):
        reasons.append(SecondaryASRReason.LOW_PRIMARY_CONFIDENCE)

    similarities: list[float] = []
    for asr_span, caption_span in product(primary, captions):
        if (
            _pair_overlap_in_window(
                asr_span,
                caption_span,
                window_start_us,
                window_end_us,
            )
            <= 0
        ):
            continue
        asr_text = _normalized_text(asr_span)
        caption_text = _normalized_text(caption_span)
        if asr_text and caption_text:
            similarities.append(SequenceMatcher(None, asr_text, caption_text).ratio())
    lowest_similarity = min(similarities, default=None)
    if (
        lowest_similarity is not None
        and lowest_similarity < selected_policy.caption_similarity_threshold
    ):
        reasons.append(SecondaryASRReason.CAPTION_CONFLICT)

    normalized_hints = {_normalize_language(item) for item in language_hints if item.strip()}
    language_confidences = [
        confidence for item in primary if (confidence := _language_confidence(item)) is not None
    ]
    lowest_language_confidence = min(language_confidences, default=None)
    language_is_uncertain = (
        lowest_language_confidence is not None
        and lowest_language_confidence < selected_policy.language_confidence_threshold
    )
    if len(normalized_hints) > 1 and (
        len(language_confidences) != len(primary) or any(item.language is None for item in primary)
    ):
        language_is_uncertain = bool(primary)
    if language_is_uncertain:
        reasons.append(SecondaryASRReason.LANGUAGE_UNCERTAIN)

    language_conflicts = 0
    for asr_span in primary:
        asr_language = _normalize_optional_language(asr_span.language)
        if asr_language is not None and normalized_hints and asr_language not in normalized_hints:
            language_conflicts += 1
        for caption_span in captions:
            if (
                _pair_overlap_in_window(
                    asr_span,
                    caption_span,
                    window_start_us,
                    window_end_us,
                )
                <= 0
            ):
                continue
            caption_language = _normalize_optional_language(caption_span.language)
            if (
                asr_language is not None
                and caption_language is not None
                and asr_language != caption_language
            ):
                language_conflicts += 1
    if language_conflicts:
        reasons.append(SecondaryASRReason.LANGUAGE_CONFLICT)

    return SecondaryASRDecision(
        window_start_us=window_start_us,
        window_end_us=window_end_us,
        requires_secondary=bool(reasons),
        reasons=reasons,
        primary_evidence_ids=[item.id for item in primary],
        caption_evidence_ids=[item.id for item in captions],
        lowest_primary_confidence=lowest_confidence,
        lowest_caption_similarity=lowest_similarity,
        lowest_language_confidence=lowest_language_confidence,
        language_conflict_count=language_conflicts,
    )


def build_secondary_asr_decisions(
    *,
    primary_asr: list[EvidenceSpan],
    platform_captions: list[EvidenceSpan],
    language_hints: Sequence[str] = (),
    policy: SecondaryASRPolicy | None = None,
    merge_gap_us: int = 250_000,
) -> list[SecondaryASRDecision]:
    """Evaluate natural evidence components instead of fixed-duration windows."""

    if merge_gap_us < 0:
        raise ValueError("merge_gap_us cannot be negative")
    spans = sorted(
        [*primary_asr, *platform_captions],
        key=lambda item: (item.start_us, item.end_us, item.id),
    )
    if not spans:
        return []
    components: list[tuple[int, int]] = []
    current_start = spans[0].start_us
    current_end = max(spans[0].end_us, current_start + 1)
    for item in spans[1:]:
        item_end = max(item.end_us, item.start_us + 1)
        if item.start_us <= current_end + merge_gap_us:
            current_end = max(current_end, item_end)
            continue
        components.append((current_start, current_end))
        current_start = item.start_us
        current_end = item_end
    components.append((current_start, current_end))
    return [
        evaluate_secondary_asr_window(
            window_start_us=start,
            window_end_us=end,
            primary_asr=primary_asr,
            platform_captions=platform_captions,
            language_hints=language_hints,
            policy=policy,
        )
        for start, end in components
    ]


def _overlap_in_window(item: EvidenceSpan, start_us: int, end_us: int) -> int:
    return max(0, min(item.end_us, end_us) - max(item.start_us, start_us))


def _pair_overlap_in_window(
    left: EvidenceSpan,
    right: EvidenceSpan,
    start_us: int,
    end_us: int,
) -> int:
    return max(
        0,
        min(left.end_us, right.end_us, end_us) - max(left.start_us, right.start_us, start_us),
    )


def _normalized_text(item: EvidenceSpan) -> str:
    text = item.normalized_text or item.raw_text or ""
    return "".join(text.casefold().split())


def _normalize_language(language: str) -> str:
    return language.strip().replace("_", "-").casefold().split("-", 1)[0]


def _normalize_optional_language(language: str | None) -> str | None:
    if language is None or not language.strip():
        return None
    return _normalize_language(language)


def _language_confidence(item: EvidenceSpan) -> float | None:
    value = item.provenance.get("language_probability")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return min(1.0, max(0.0, float(value)))
