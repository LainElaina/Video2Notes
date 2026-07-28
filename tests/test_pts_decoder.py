from __future__ import annotations

import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import av
from PIL import Image

from video2notes.domain import MediaTimestamp, Rational
from video2notes.media import decode_video_frame_at, iter_video_frames
from video2notes.vision.adaptive_sampler import (
    AdaptiveScanConfig,
    AdaptiveVideoScanner,
    ChangeEvent,
    VideoProbe,
)

FRAME_SIZE = (160, 90)
FRAME_PTS = (100, 220, 640, 1_000, 1_750)
FRAME_COLORS = (
    (220, 30, 30),
    (30, 180, 40),
    (35, 75, 220),
    (220, 180, 25),
    (170, 45, 190),
)


def write_vfr_fixture(path: Path) -> None:
    time_base = Fraction(1, 1_000)
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("ffv1", rate=25)
        stream.width, stream.height = FRAME_SIZE
        stream.pix_fmt = "yuv420p"
        stream.time_base = time_base
        stream.codec_context.time_base = time_base

        for pts, color in zip(FRAME_PTS, FRAME_COLORS, strict=True):
            frame = av.VideoFrame.from_image(Image.new("RGB", FRAME_SIZE, color))
            frame.pts = pts
            frame.time_base = time_base
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def source_timestamps(path: Path) -> list[tuple[int, Rational, int]]:
    timestamps: list[tuple[int, Rational, int]] = []
    with av.open(str(path), mode="r") as container:
        stream = container.streams.video[0]
        for frame in container.decode(stream):
            if frame.pts is None or frame.time_base is None:
                raise AssertionError("fixture frame unexpectedly has no PTS")
            time_base = Rational(
                numerator=frame.time_base.numerator,
                denominator=frame.time_base.denominator,
            )
            timestamps.append(
                (
                    frame.pts,
                    time_base,
                    time_base.timestamp_us(frame.pts),
                )
            )
    return timestamps


class PtsDecoderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.video = Path(self.temporary.name) / "vfr.mkv"
        write_vfr_fixture(self.video)
        self.expected = source_timestamps(self.video)
        self.assertGreaterEqual(len(self.expected), 4)
        self.timeline_origin_us = self.expected[0][2]

    def test_decoder_preserves_non_uniform_source_pts(self) -> None:
        decoded = list(
            iter_video_frames(
                self.video,
                timeline_origin_us=self.timeline_origin_us,
                sample_fps=None,
                target_size=(80, 60),
            )
        )

        actual = [
            (
                frame.timestamp.pts,
                frame.timestamp.time_base,
                frame.timestamp.source_time_us,
            )
            for frame in decoded
        ]
        self.assertEqual(actual, self.expected)
        self.assertTrue(all(frame.image.size == (80, 60) for frame in decoded))

        deltas = [
            right.timestamp.time_us - left.timestamp.time_us
            for left, right in zip(decoded, decoded[1:], strict=False)
        ]
        self.assertGreater(len(set(deltas)), 1)

    def test_sampling_emits_real_frames_not_synthetic_ticks(self) -> None:
        decoded = list(
            iter_video_frames(
                self.video,
                timeline_origin_us=self.timeline_origin_us,
                sample_fps=4.0,
            )
        )

        source_pts = {item[0] for item in self.expected}
        self.assertTrue(all(frame.timestamp.pts in source_pts for frame in decoded))
        canonical_times = [frame.timestamp.time_us for frame in decoded]
        self.assertTrue(any(value % 250_000 != 0 for value in canonical_times[1:]))

    def test_exact_frame_lookup_matches_pts_and_time_base(self) -> None:
        pts, time_base, _ = self.expected[2]
        target = MediaTimestamp.from_pts(
            pts,
            time_base,
            timeline_origin_us=self.timeline_origin_us,
        )

        decoded = decode_video_frame_at(
            self.video,
            timestamp=target,
            timeline_origin_us=self.timeline_origin_us,
        )

        self.assertEqual(decoded.timestamp.pts, target.pts)
        self.assertEqual(decoded.timestamp.time_base, target.time_base)
        self.assertEqual(decoded.timestamp.time_us, target.time_us)

    def test_preview_is_decoded_from_the_exact_keyframe_pts(self) -> None:
        pts, time_base, _ = self.expected[3]
        target = MediaTimestamp.from_pts(
            pts,
            time_base,
            timeline_origin_us=self.timeline_origin_us,
        )
        event = ChangeEvent(
            transition=target,
            keyframe=target,
            previous_keyframe=None,
            reason="initial",
            state_score=0.0,
            scene_score=0.0,
            text_score=0.0,
            step_score=0.0,
            refined=True,
        )
        stream_index = 0
        with av.open(str(self.video), mode="r") as container:
            stream_index = container.streams.video[0].index
        probe = VideoProbe(
            duration_us=self.expected[-1][2] - self.timeline_origin_us + 1_000,
            width=FRAME_SIZE[0],
            height=FRAME_SIZE[1],
            frame_rate=None,
            timeline_origin_us=self.timeline_origin_us,
            stream_index=stream_index,
            stream_time_base=time_base,
        )
        scanner = AdaptiveVideoScanner(
            AdaptiveScanConfig(
                coarse_fps=2.0,
                fine_fps=4.0,
                analysis_width=160,
                analysis_height=90,
            )
        )
        preview_dir = Path(self.temporary.name) / "previews"

        [updated] = scanner._write_previews(
            self.video,
            [event],
            preview_dir,
            probe,
        )

        self.assertIsNotNone(updated.preview_path)
        preview_path = Path(updated.preview_path or "")
        self.assertTrue(preview_path.is_file())
        with Image.open(preview_path) as preview:
            actual = preview.convert("RGB").getpixel((80, 45))
        expected = FRAME_COLORS[3]
        self.assertLess(
            sum(
                abs(left - right)
                for left, right in zip(actual, expected, strict=True)
            ),
            30,
        )


if __name__ == "__main__":
    unittest.main()
