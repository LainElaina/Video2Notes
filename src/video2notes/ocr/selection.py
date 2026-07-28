"""Explainable frame selection for scrolling and incrementally revealed text."""

from __future__ import annotations

import math
from collections import Counter

from pydantic import Field

from .backend import tokenize_ocr_text
from .models import (
    FrameCoverageScore,
    OcrModel,
    OcrResult,
    ScrollFrameSelection,
)


class ScrollSelectionConfig(OcrModel):
    target_coverage: float = Field(default=1.0, gt=0, le=1)
    clarity_weight: float = Field(default=0.12, ge=0, le=1)
    confidence_weight: float = Field(default=0.08, ge=0, le=1)
    redundancy_weight: float = Field(default=0.20, ge=0)
    maximum_frames: int | None = Field(default=None, ge=1)


def select_scroll_keyframes(
    results: list[OcrResult],
    *,
    config: ScrollSelectionConfig | None = None,
) -> ScrollFrameSelection:
    """Approximate minimum set cover, using quality only as a controlled tie-breaker."""

    settings = config or ScrollSelectionConfig()
    candidates = [_candidate(result) for result in results]
    candidates = [candidate for candidate in candidates if candidate.tokens]
    all_tokens = set().union(*(candidate.tokens for candidate in candidates))
    if not all_tokens:
        return ScrollFrameSelection(
            all_unique_tokens=[],
            selected_frames=[],
            covered_tokens=[],
            uncovered_tokens=[],
            coverage_ratio=1.0,
            candidate_frame_count=len(results),
        )

    required_count = math.ceil(len(all_tokens) * settings.target_coverage)
    token_occurrences = Counter(token for candidate in candidates for token in candidate.tokens)
    covered: set[str] = set()
    selected_ids: set[str] = set()
    selected: list[FrameCoverageScore] = []

    while len(covered) < required_count:
        if settings.maximum_frames is not None and len(selected) >= settings.maximum_frames:
            break
        ranked: list[tuple[int, float, float, float, float, str, _FrameCandidate]] = []
        for candidate in candidates:
            if candidate.result.id in selected_ids:
                continue
            newly_covered = candidate.tokens - covered
            if not newly_covered:
                continue
            redundant = candidate.tokens & covered
            redundancy_ratio = len(redundant) / len(candidate.tokens)
            redundancy_penalty = settings.redundancy_weight * redundancy_ratio
            quality_bonus = (
                settings.clarity_weight * candidate.clarity
                + settings.confidence_weight * candidate.confidence
            )
            rarity_bonus = sum(1.0 / token_occurrences[token] for token in newly_covered)
            # Coverage count remains dominant. Rarity anticipates tokens with few
            # alternative frames, then clarity/confidence settle close calls.
            score = len(newly_covered) + 0.05 * rarity_bonus + quality_bonus - redundancy_penalty
            ranked.append(
                (
                    len(newly_covered),
                    rarity_bonus,
                    score,
                    candidate.clarity,
                    candidate.confidence,
                    candidate.result.id,
                    candidate,
                )
            )
        if not ranked:
            break
        ranked.sort(
            key=lambda item: (
                -item[0],
                -item[1],
                -item[2],
                -item[3],
                -item[4],
                item[5],
            )
        )
        gain_count, rarity_bonus, score, _, _, _, winner = ranked[0]
        newly_covered = winner.tokens - covered
        redundant = winner.tokens & covered
        redundancy_ratio = len(redundant) / len(winner.tokens)
        redundancy_penalty = settings.redundancy_weight * redundancy_ratio
        selected.append(
            FrameCoverageScore(
                selection_order=len(selected) + 1,
                result_id=winner.result.id,
                visual_state_id=winner.result.visual_state_id,
                keyframe_artifact=winner.result.keyframe_artifact,
                candidate_tokens=sorted(winner.tokens),
                newly_covered_tokens=sorted(newly_covered),
                redundant_tokens=sorted(redundant),
                clarity_score=winner.clarity,
                mean_confidence=winner.confidence,
                weighted_coverage_gain=float(gain_count),
                rarity_bonus=rarity_bonus,
                redundancy_penalty=redundancy_penalty,
                selection_score=score,
            )
        )
        selected_ids.add(winner.result.id)
        covered.update(newly_covered)

    uncovered = all_tokens - covered
    return ScrollFrameSelection(
        all_unique_tokens=sorted(all_tokens),
        selected_frames=selected,
        covered_tokens=sorted(covered),
        uncovered_tokens=sorted(uncovered),
        coverage_ratio=len(covered) / len(all_tokens),
        candidate_frame_count=len(results),
    )


class _FrameCandidate:
    def __init__(
        self,
        result: OcrResult,
        tokens: set[str],
        clarity: float,
        confidence: float,
    ) -> None:
        self.result = result
        self.tokens = tokens
        self.clarity = clarity
        self.confidence = confidence


def _candidate(result: OcrResult) -> _FrameCandidate:
    lines = result.accepted_lines
    tokens = {token for line in lines for token in tokenize_ocr_text(line.normalized_text)}
    if result.frame_readability is not None:
        clarity = result.frame_readability
    elif lines:
        clarity = sum(line.crop_readability for line in lines) / len(lines)
    else:
        clarity = 0.0
    confidence = sum(line.confidence for line in lines) / len(lines) if lines else 0.0
    return _FrameCandidate(result, tokens, clarity, confidence)
