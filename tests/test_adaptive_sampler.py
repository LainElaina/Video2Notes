from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from video2notes.vision.adaptive_sampler import (
    AdaptiveScanConfig,
    StableStateDetector,
    compare_frames,
    synthetic_observations,
)


SIZE = (640, 360)
FPS = 4.0


def slide(*, bullet_count: int = 0, dark: bool = False) -> Image.Image:
    background = (28, 31, 38) if dark else (247, 245, 238)
    foreground = (235, 237, 240) if dark else (24, 30, 36)
    image = Image.new("RGB", SIZE, background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 45, 510, 63), fill=foreground)
    draw.rectangle((70, 76, 360, 88), fill=foreground)
    for index in range(bullet_count):
        top = 135 + index * 54
        draw.rectangle((82, top, 96, top + 14), fill=foreground)
        draw.rectangle((116, top, 470 - index * 25, top + 14), fill=foreground)
        draw.rectangle((116, top + 22, 390 - index * 18, top + 31), fill=foreground)
    return image


class FrameMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AdaptiveScanConfig(coarse_fps=FPS, fine_fps=12.0)

    def test_small_text_region_is_visible_to_text_metric(self) -> None:
        before = slide(bullet_count=1)
        after = slide(bullet_count=2)
        metrics = compare_frames(before, after, self.config)
        self.assertGreater(metrics.text_score, metrics.scene_score)
        self.assertGreater(metrics.text_score, 0.01)

    def test_identical_frames_have_zero_difference(self) -> None:
        image = slide(bullet_count=2)
        metrics = compare_frames(image, image.copy(), self.config)
        self.assertEqual(metrics.state_score, 0.0)


class StableStateDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AdaptiveScanConfig(
            coarse_fps=FPS,
            fine_fps=12.0,
            min_persistence_ms=500,
            settle_ms=500,
            cooldown_ms=250,
            state_change_threshold=0.020,
            text_change_threshold=0.014,
            stable_step_threshold=0.010,
            hard_cut_threshold=0.10,
        )
        self.detector = StableStateDetector(self.config)

    def test_persistent_incremental_bullet_creates_event(self) -> None:
        first = slide(bullet_count=1)
        second = slide(bullet_count=2)
        frames = [first.copy() for _ in range(6)]
        frames.extend(second.copy() for _ in range(8))
        events = self.detector.detect(synthetic_observations(frames, fps=FPS))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].reason, "text_or_ui_change")
        self.assertGreaterEqual(events[1].transition_ms, 1_250)
        self.assertGreater(events[1].keyframe_ms, events[1].transition_ms)

    def test_single_frame_overlay_is_rejected_as_transient(self) -> None:
        base = slide(bullet_count=1)
        overlay = base.copy()
        draw = ImageDraw.Draw(overlay)
        draw.rectangle((500, 280, 620, 335), fill=(210, 20, 20))
        frames = [base.copy() for _ in range(6)]
        frames.append(overlay)
        frames.extend(base.copy() for _ in range(8))
        events = self.detector.detect(synthetic_observations(frames, fps=FPS))
        self.assertEqual([event.reason for event in events], ["initial"])

    def test_hard_cut_creates_one_new_state(self) -> None:
        light = slide(bullet_count=2)
        dark = slide(bullet_count=2, dark=True)
        frames = [light.copy() for _ in range(6)]
        frames.extend(dark.copy() for _ in range(8))
        events = self.detector.detect(synthetic_observations(frames, fps=FPS))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].reason, "hard_cut")


if __name__ == "__main__":
    unittest.main()

