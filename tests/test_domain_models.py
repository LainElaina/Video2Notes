from __future__ import annotations

import unittest

from pydantic import ValidationError

from video2notes.domain import (
    ArtifactKind,
    ArtifactRef,
    EvidenceModality,
    EvidenceSpan,
    MediaTimestamp,
    Rational,
    VisualState,
)


class TimebaseTests(unittest.TestCase):
    def test_pts_rescale_uses_exact_rational_math(self) -> None:
        time_base = Rational(numerator=1, denominator=90_000)
        timestamp = MediaTimestamp.from_pts(
            90_009,
            time_base,
            timeline_origin_us=500_000,
        )
        self.assertEqual(timestamp.source_time_us, 1_000_100)
        self.assertEqual(timestamp.time_us, 500_100)

    def test_large_pts_does_not_require_float(self) -> None:
        time_base = Rational(numerator=1, denominator=48_000)
        self.assertEqual(
            time_base.timestamp_us(48_000 * 60 * 60 * 24),
            86_400_000_000,
        )


class EvidenceModelTests(unittest.TestCase):
    def test_evidence_rejects_reversed_interval(self) -> None:
        with self.assertRaises(ValidationError):
            EvidenceSpan(
                id="e1",
                run_id="run",
                modality=EvidenceModality.ASR,
                start_us=2_000_000,
                end_us=1_000_000,
            )

    def test_visual_state_rejects_keyframe_before_transition(self) -> None:
        with self.assertRaises(ValidationError):
            VisualState(
                id="v1",
                run_id="run",
                start_us=0,
                end_us=2_000_000,
                transition_us=1_000_000,
                stable_keyframe_us=900_000,
                change_reason="text_or_ui_change",
            )

    def test_artifact_ref_rejects_escape_path(self) -> None:
        with self.assertRaises(ValidationError):
            ArtifactRef(
                kind=ArtifactKind.MEDIA,
                relative_path="../outside.mp4",
                sha256="0" * 64,
                size_bytes=1,
            )
