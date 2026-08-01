from __future__ import annotations

import unittest

import video2notes.fusion.timeline as fusion_timeline
from video2notes.domain import (
    BoundingBox,
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
    box: tuple[float, float, float, float] | None = None,
    provenance: dict[str, object] | None = None,
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
        bounding_boxes=(
            [
                BoundingBox(
                    x=box[0],
                    y=box[1],
                    width=box[2],
                    height=box[3],
                )
            ]
            if box is not None
            else []
        ),
        provenance=provenance or {},
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

    def test_ocr_conflict_requires_distinct_frames_same_track_and_same_region(self) -> None:
        result = build_evidence_timeline(
            [
                span(
                    "same-slot-a",
                    EvidenceModality.OCR,
                    0,
                    2_000_000,
                    "720p",
                    box=(100, 200, 180, 40),
                    provenance={
                        "visual_state_id": "state-a",
                        "ocr_track_id": "track-resolution",
                        "frame_width": 1080,
                        "frame_height": 1920,
                    },
                ),
                span(
                    "same-slot-b",
                    EvidenceModality.OCR,
                    1_000_000,
                    3_000_000,
                    "1080p",
                    box=(103, 201, 185, 41),
                    provenance={
                        "visual_state_id": "state-b",
                        "ocr_track_id": "track-resolution",
                        "frame_width": 1080,
                        "frame_height": 1920,
                    },
                ),
            ]
        )

        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(result.conflicts[0].kind, ConflictKind.OCR_DISAGREEMENT)

    def test_ocr_coexisting_or_unrelated_regions_do_not_create_conflict_flood(self) -> None:
        evidence = [
            span(
                "same-frame-left",
                EvidenceModality.OCR,
                0,
                2_000_000,
                "设置",
                box=(100, 200, 180, 40),
                provenance={"visual_state_id": "state-a"},
            ),
            span(
                "same-frame-right",
                EvidenceModality.OCR,
                0,
                2_000_000,
                "开始直播",
                box=(105, 202, 175, 39),
                provenance={"visual_state_id": "state-a"},
            ),
        ]
        for index in range(40):
            evidence.append(
                span(
                    f"line-{index}",
                    EvidenceModality.OCR,
                    0,
                    2_000_000,
                    f"界面项目 {index}",
                    box=(10 + (index % 5) * 200, 400 + (index // 5) * 80, 120, 30),
                    provenance={"visual_state_id": "state-a"},
                )
            )

        result = build_evidence_timeline(evidence)

        self.assertEqual(result.links, [])
        self.assertEqual(result.conflicts, [])

    def test_ocr_different_track_or_region_is_not_a_semantic_conflict(self) -> None:
        result = build_evidence_timeline(
            [
                span(
                    "baseline",
                    EvidenceModality.OCR,
                    0,
                    3_000_000,
                    "直播设置",
                    box=(100, 200, 180, 40),
                    provenance={
                        "visual_state_id": "state-a",
                        "ocr_track_id": "track-a",
                    },
                ),
                span(
                    "other-region",
                    EvidenceModality.OCR,
                    1_000_000,
                    2_500_000,
                    "完全不同",
                    box=(700, 1200, 180, 40),
                    provenance={
                        "visual_state_id": "state-b",
                        "ocr_track_id": "track-a",
                    },
                ),
                span(
                    "other-track",
                    EvidenceModality.OCR,
                    1_000_000,
                    2_500_000,
                    "开始录制",
                    box=(102, 201, 180, 40),
                    provenance={
                        "visual_state_id": "state-c",
                        "ocr_track_id": "track-b",
                    },
                ),
            ]
        )

        self.assertEqual(result.conflicts, [])

    def test_ocr_observation_region_sweep_handles_two_thousand_states(self) -> None:
        observation_count = 2_000

        def tracked_span(identifier: str, track_id: str, x: float) -> EvidenceSpan:
            observations = [
                {
                    "start_us": index * 1_000,
                    "end_us": (index + 1) * 1_000,
                    "bounding_boxes": [
                        {
                            "x": x,
                            "y": 10,
                            "width": 10,
                            "height": 10,
                            "coordinate_space": "pixels",
                        }
                    ],
                    "provenance": {"visual_state_id": f"{identifier}-state-{index}"},
                }
                for index in range(observation_count)
            ]
            return span(
                identifier,
                EvidenceModality.OCR,
                0,
                observation_count * 1_000,
                identifier,
                box=(x, 10, 10, 10),
                provenance={
                    "ocr_track_id": track_id,
                    "observation_count": observation_count,
                    "observations": observations,
                },
            )

        left = tracked_span("left-line", "track-left", 10)
        right = tracked_span("right-line", "track-right", 100)
        temporal_pairs = fusion_timeline._overlapping_observation_regions(
            fusion_timeline._ocr_observation_regions(left),
            fusion_timeline._ocr_observation_regions(right),
        )

        self.assertEqual(sum(1 for _ in temporal_pairs), observation_count)
        result = build_evidence_timeline([left, right])
        self.assertEqual(result.links, [])
        self.assertEqual(result.conflicts, [])

    def test_ocr_region_bucket_enumeration_is_bounded_for_oversized_box(self) -> None:
        evidence = span(
            "oversized",
            EvidenceModality.OCR,
            0,
            1_000_000,
            "malformed detector box",
            box=(0, 0, 1_000_000_000_000, 1_000_000_000_000),
        )

        keys = fusion_timeline._ocr_region_keys(evidence)

        self.assertLessEqual(len(keys), 256)

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
