from __future__ import annotations

import unittest

from video2notes.audio import (
    SecondaryASRPolicy,
    SecondaryASRReason,
    build_secondary_asr_decisions,
    evaluate_secondary_asr_window,
)
from video2notes.domain import EvidenceModality, EvidenceSpan


def evidence(
    *,
    identifier: str,
    modality: EvidenceModality,
    start_us: int,
    end_us: int,
    text: str,
    confidence: float | None = None,
) -> EvidenceSpan:
    return EvidenceSpan(
        id=identifier,
        run_id="run",
        modality=modality,
        start_us=start_us,
        end_us=end_us,
        raw_text=text,
        normalized_text=text,
        confidence=confidence,
    )


class SecondaryASRTests(unittest.TestCase):
    def test_low_confidence_primary_triggers_secondary(self) -> None:
        decision = evaluate_secondary_asr_window(
            window_start_us=0,
            window_end_us=2_000_000,
            primary_asr=[
                evidence(
                    identifier="asr-low",
                    modality=EvidenceModality.ASR,
                    start_us=100_000,
                    end_us=1_000_000,
                    text="same words",
                    confidence=0.4,
                )
            ],
            platform_captions=[
                evidence(
                    identifier="caption",
                    modality=EvidenceModality.PLATFORM_CAPTION,
                    start_us=100_000,
                    end_us=1_000_000,
                    text="same words",
                )
            ],
        )

        self.assertTrue(decision.requires_secondary)
        self.assertEqual(decision.reasons, [SecondaryASRReason.LOW_PRIMARY_CONFIDENCE])
        self.assertAlmostEqual(decision.lowest_caption_similarity or 0, 1.0)

    def test_only_overlapping_caption_can_create_conflict(self) -> None:
        primary = evidence(
            identifier="asr",
            modality=EvidenceModality.ASR,
            start_us=0,
            end_us=1_000_000,
            text="high precision transcript",
            confidence=0.95,
        )
        outside = evidence(
            identifier="outside",
            modality=EvidenceModality.PLATFORM_CAPTION,
            start_us=2_000_000,
            end_us=3_000_000,
            text="completely different",
        )
        no_conflict = evaluate_secondary_asr_window(
            window_start_us=0,
            window_end_us=4_000_000,
            primary_asr=[primary],
            platform_captions=[outside],
        )
        self.assertFalse(no_conflict.requires_secondary)
        self.assertIsNone(no_conflict.lowest_caption_similarity)

        overlapping = evidence(
            identifier="overlap",
            modality=EvidenceModality.PLATFORM_CAPTION,
            start_us=500_000,
            end_us=900_000,
            text="totally unrelated caption",
        )
        conflict = evaluate_secondary_asr_window(
            window_start_us=0,
            window_end_us=4_000_000,
            primary_asr=[primary],
            platform_captions=[outside, overlapping],
            policy=SecondaryASRPolicy(caption_similarity_threshold=0.8),
        )
        self.assertTrue(conflict.requires_secondary)
        self.assertIn(SecondaryASRReason.CAPTION_CONFLICT, conflict.reasons)

    def test_missing_primary_in_window_triggers_secondary(self) -> None:
        decision = evaluate_secondary_asr_window(
            window_start_us=1_000_000,
            window_end_us=2_000_000,
            primary_asr=[],
            platform_captions=[
                evidence(
                    identifier="caption-only",
                    modality=EvidenceModality.PLATFORM_CAPTION,
                    start_us=1_100_000,
                    end_us=1_900_000,
                    text="caption but no primary transcript",
                )
            ],
        )
        self.assertEqual(decision.reasons, [SecondaryASRReason.MISSING_PRIMARY])

    def test_empty_silent_window_does_not_trigger_secondary(self) -> None:
        decision = evaluate_secondary_asr_window(
            window_start_us=1_000_000,
            window_end_us=2_000_000,
            primary_asr=[],
            platform_captions=[],
        )
        self.assertFalse(decision.requires_secondary)

    def test_rejects_wrong_modality_lists(self) -> None:
        caption = evidence(
            identifier="caption",
            modality=EvidenceModality.PLATFORM_CAPTION,
            start_us=0,
            end_us=1,
            text="caption",
        )
        with self.assertRaisesRegex(ValueError, "primary_asr"):
            evaluate_secondary_asr_window(
                window_start_us=0,
                window_end_us=1,
                primary_asr=[caption],
                platform_captions=[],
            )

    def test_candidate_windows_follow_evidence_components_not_a_time_grid(self) -> None:
        primary = [
            evidence(
                identifier="a",
                modality=EvidenceModality.ASR,
                start_us=100_000,
                end_us=500_000,
                text="low",
                confidence=0.2,
            ),
            evidence(
                identifier="b",
                modality=EvidenceModality.ASR,
                start_us=4_000_000,
                end_us=4_400_000,
                text="certain",
                confidence=0.99,
            ),
        ]
        captions = [
            evidence(
                identifier="c",
                modality=EvidenceModality.PLATFORM_CAPTION,
                start_us=4_100_000,
                end_us=4_350_000,
                text="different",
                confidence=1.0,
            )
        ]

        decisions = build_secondary_asr_decisions(
            primary_asr=primary,
            platform_captions=captions,
        )

        self.assertEqual(
            [(item.window_start_us, item.window_end_us) for item in decisions],
            [(100_000, 500_000), (4_000_000, 4_400_000)],
        )
        self.assertEqual(
            decisions[0].reasons,
            [SecondaryASRReason.LOW_PRIMARY_CONFIDENCE],
        )
        self.assertIn(
            SecondaryASRReason.CAPTION_CONFLICT,
            decisions[1].reasons,
        )


if __name__ == "__main__":
    unittest.main()
