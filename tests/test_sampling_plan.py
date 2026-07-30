from __future__ import annotations

import unittest
from typing import Literal

from pydantic import ValidationError

from video2notes.domain import MediaTimestamp, Rational
from video2notes.pipeline import PipelineRequest
from video2notes.sources import SourceInput
from video2notes.vision import (
    SamplingMode,
    SamplingOverride,
    SamplingPlan,
    SamplingSpec,
    TimeRange,
    compile_sampling_plan,
    merge_change_events,
)
from video2notes.vision.adaptive_sampler import ChangeEvent


def fixed(interval_us: int) -> SamplingSpec:
    return SamplingSpec(
        mode=SamplingMode.FIXED_INTERVAL,
        interval_us=interval_us,
    )


def event(
    *,
    pts: int,
    time_us: int,
    sampling_mode: Literal["adaptive", "fixed_interval"],
    refined: bool = False,
) -> ChangeEvent:
    timestamp = MediaTimestamp(
        pts=pts,
        time_base=Rational(numerator=1, denominator=1_000),
        source_time_us=time_us,
        time_us=time_us,
    )
    return ChangeEvent(
        transition=timestamp,
        keyframe=timestamp,
        previous_keyframe=None,
        reason=("fixed_interval" if sampling_mode == "fixed_interval" else "initial"),
        state_score=0.5 if sampling_mode == "adaptive" else 0.0,
        scene_score=0.0,
        text_score=0.0,
        step_score=0.0,
        refined=refined,
        sampling_mode=sampling_mode,
    )


class SamplingPlanTests(unittest.TestCase):
    def test_default_plan_is_one_adaptive_segment_and_pipeline_compatible(self) -> None:
        plan = SamplingPlan()
        segments = plan.compile(2_000_000)

        self.assertEqual(len(segments), 1)
        segment = segments[0]
        self.assertEqual((segment.start_us, segment.end_us), (0, 2_000_000))
        self.assertEqual(segment.sampling.mode, SamplingMode.ADAPTIVE)
        self.assertEqual(segment.source, "default")

        request = PipelineRequest(source=SourceInput.local("fixture.mp4"))
        self.assertEqual(request.sampling_plan, SamplingPlan())

    def test_fixed_interval_presets_use_integer_microseconds(self) -> None:
        expected = {
            100_000: 10,
            500_000: 2,
            1_000_000: 1,
        }
        for interval_us, sample_count in expected.items():
            with self.subTest(interval_us=interval_us):
                segments = SamplingPlan(default=fixed(interval_us)).compile(1_000_000)
                self.assertEqual(len(segments), 1)
                self.assertEqual(segments[0].estimated_sample_count, sample_count)
                self.assertEqual(segments[0].sampling.interval_us, interval_us)

    def test_fixed_interval_requires_at_least_one_hundred_milliseconds(self) -> None:
        with self.assertRaises(ValidationError):
            fixed(99_999)
        with self.assertRaises(ValidationError):
            SamplingSpec(mode=SamplingMode.FIXED_INTERVAL)
        with self.assertRaises(ValidationError):
            SamplingSpec(mode=SamplingMode.ADAPTIVE, interval_us=500_000)
        with self.assertRaises(ValidationError):
            SamplingSpec.model_validate(
                {"mode": "fixed_interval", "interval_us": 500_000.0}
            )

    def test_overlapping_overrides_are_rejected_but_adjacent_are_valid(self) -> None:
        with self.assertRaisesRegex(ValidationError, "cannot overlap"):
            SamplingPlan(
                overrides=[
                    SamplingOverride(
                        range=TimeRange(start_us=100_000, end_us=500_000),
                        sampling=fixed(100_000),
                    ),
                    SamplingOverride(
                        range=TimeRange(start_us=400_000, end_us=700_000),
                        sampling=SamplingSpec(mode=SamplingMode.SKIP),
                    ),
                ]
            )

        plan = SamplingPlan(
            overrides=[
                SamplingOverride(
                    range=TimeRange(start_us=100_000, end_us=500_000),
                    sampling=fixed(100_000),
                ),
                SamplingOverride(
                    range=TimeRange(start_us=500_000, end_us=700_000),
                    sampling=SamplingSpec(mode=SamplingMode.SKIP),
                ),
            ]
        )
        self.assertEqual(len(plan.compile(1_000_000)), 4)

    def test_compile_splits_fixed_adaptive_and_skip_without_gaps(self) -> None:
        plan = SamplingPlan(
            default=SamplingSpec(mode=SamplingMode.ADAPTIVE),
            overrides=[
                SamplingOverride(
                    range=TimeRange(start_us=1_000_000, end_us=2_000_000),
                    sampling=fixed(500_000),
                ),
                SamplingOverride(
                    range=TimeRange(start_us=3_000_000, end_us=4_000_000),
                    sampling=SamplingSpec(mode=SamplingMode.SKIP),
                ),
            ],
        )

        segments = compile_sampling_plan(plan, duration_us=5_000_000)

        self.assertEqual(
            [
                (
                    item.start_us,
                    item.end_us,
                    item.sampling.mode,
                    item.sampling.interval_us,
                )
                for item in segments
            ],
            [
                (0, 1_000_000, SamplingMode.ADAPTIVE, None),
                (1_000_000, 2_000_000, SamplingMode.FIXED_INTERVAL, 500_000),
                (2_000_000, 3_000_000, SamplingMode.ADAPTIVE, None),
                (3_000_000, 4_000_000, SamplingMode.SKIP, None),
                (4_000_000, 5_000_000, SamplingMode.ADAPTIVE, None),
            ],
        )

    def test_compile_rejects_out_of_bounds_and_excessive_fixed_samples(self) -> None:
        out_of_bounds = SamplingPlan(
            overrides=[
                SamplingOverride(
                    range=TimeRange(start_us=900_000, end_us=1_100_000),
                    sampling=SamplingSpec(mode=SamplingMode.SKIP),
                )
            ]
        )
        with self.assertRaisesRegex(ValueError, "after media duration"):
            out_of_bounds.compile(1_000_000)

        with self.assertRaisesRegex(ValueError, "5001 frames"):
            SamplingPlan(default=fixed(100_000)).compile(500_000_001)

    def test_merge_deduplicates_by_real_pts_and_prefers_adaptive_event(self) -> None:
        later = event(pts=300, time_us=300_000, sampling_mode="fixed_interval")
        fixed_duplicate = event(pts=100, time_us=100_000, sampling_mode="fixed_interval")
        adaptive_duplicate = event(
            pts=100,
            time_us=100_000,
            sampling_mode="adaptive",
            refined=True,
        )

        merged = merge_change_events([later, fixed_duplicate, adaptive_duplicate])

        self.assertEqual([item.keyframe.pts for item in merged], [100, 300])
        self.assertEqual(merged[0].sampling_mode, "adaptive")
        self.assertIsNone(merged[0].previous_keyframe)
        self.assertEqual(merged[1].previous_keyframe, merged[0].keyframe)


if __name__ == "__main__":
    unittest.main()
