from __future__ import annotations

import unittest

from video2notes.domain import (
    EvidenceModality,
    EvidenceSpan,
    Rational,
    VisualState,
)
from video2notes.fusion import (
    ConflictKind,
    LinkRelation,
    build_evidence_timeline,
)


def span(
    identifier: str,
    modality: EvidenceModality,
    start_us: int,
    end_us: int,
    text: str,
    *,
    confidence: float = 0.9,
) -> EvidenceSpan:
    return EvidenceSpan(
        id=identifier,
        run_id="run-1",
        modality=modality,
        start_us=start_us,
        end_us=end_us,
        raw_text=text,
        normalized_text=text,
        confidence=confidence,
    )


def visual_state(identifier: str, start_us: int, transition_us: int, end_us: int) -> VisualState:
    return VisualState(
        id=identifier,
        run_id="run-1",
        start_us=start_us,
        end_us=end_us,
        transition_us=transition_us,
        stable_keyframe_us=transition_us,
        transition_pts=transition_us,
        keyframe_pts=transition_us,
        stream_time_base=Rational(numerator=1, denominator=1_000_000),
        change_reason="text_or_ui_change",
    )


class FusionTimelineTests(unittest.TestCase):
    def test_visual_transition_and_speech_pause_create_semantic_boundaries(self) -> None:
        result = build_evidence_timeline(
            [
                span("asr-1", EvidenceModality.ASR, 0, 2_000_000, "第一段"),
                span("asr-2", EvidenceModality.ASR, 4_000_000, 6_000_000, "第二段"),
                span("ocr-1", EvidenceModality.OCR, 3_000_000, 7_000_000, "第二页"),
            ],
            [visual_state("visual-1", 0, 3_000_000, 7_000_000)],
        )
        self.assertEqual(
            [(item.start_us, item.end_us) for item in result.windows],
            [(0, 3_000_000), (3_000_000, 4_000_000), (4_000_000, 7_000_000)],
        )
        self.assertTrue(
            any("visual_transition" in item.boundary_reasons for item in result.windows)
        )
        self.assertTrue(any("speech_pause" in item.boundary_reasons for item in result.windows))

    def test_non_overlapping_modalities_are_not_linked_by_nearest_time(self) -> None:
        result = build_evidence_timeline(
            [
                span("asr", EvidenceModality.ASR, 0, 1_000_000, "语音"),
                span("ocr", EvidenceModality.OCR, 2_000_000, 3_000_000, "屏幕"),
            ],
            speech_gap_us=10_000_000,
        )
        self.assertEqual(result.links, [])

    def test_overlapping_ocr_and_asr_are_complements_not_voting_candidates(self) -> None:
        result = build_evidence_timeline(
            [
                span("asr", EvidenceModality.ASR, 0, 2_000_000, "点击保存"),
                span("ocr", EvidenceModality.OCR, 500_000, 2_000_000, "Save"),
            ]
        )
        self.assertEqual(len(result.links), 1)
        self.assertEqual(
            result.links[0].relation,
            LinkRelation.CO_TEMPORAL_COMPLEMENT,
        )
        self.assertEqual(result.conflicts, [])

    def test_platform_caption_and_asr_disagreement_is_traceable(self) -> None:
        result = build_evidence_timeline(
            [
                span(
                    "caption",
                    EvidenceModality.PLATFORM_CAPTION,
                    0,
                    2_000_000,
                    "精确时间戳",
                ),
                span(
                    "asr",
                    EvidenceModality.ASR,
                    100_000,
                    1_900_000,
                    "完全不同的话",
                ),
            ]
        )
        self.assertEqual(len(result.conflicts), 1)
        conflict = result.conflicts[0]
        self.assertEqual(conflict.kind, ConflictKind.TRANSCRIPT_DISAGREEMENT)
        self.assertTrue(conflict.requires_secondary)
        self.assertEqual(conflict.resolution, "unresolved")

    def test_maximum_window_is_a_safety_cap_not_a_sampling_grid(self) -> None:
        result = build_evidence_timeline(
            [
                span(
                    "long",
                    EvidenceModality.ASR,
                    0,
                    250_000_000,
                    "连续长语音",
                ),
                span(
                    "natural-start",
                    EvidenceModality.OCR,
                    83_000_000,
                    90_000_000,
                    "自然候选",
                ),
            ],
            maximum_window_us=90_000_000,
        )
        boundaries = [result.windows[0].start_us] + [item.end_us for item in result.windows]
        self.assertIn(83_000_000, boundaries)
        self.assertNotIn(90_000_000, boundaries)
        self.assertTrue(
            all(window.end_us - window.start_us <= 90_000_000 for window in result.windows)
        )

    def test_cross_run_inputs_are_rejected(self) -> None:
        left = span("left", EvidenceModality.ASR, 0, 1, "a")
        right = span("right", EvidenceModality.OCR, 0, 1, "b")
        right.run_id = "run-2"
        with self.assertRaisesRegex(ValueError, "one run"):
            build_evidence_timeline([left, right])
