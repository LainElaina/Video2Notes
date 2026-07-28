from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video2notes.media import parse_probe_payload


class MediaProbeTests(unittest.TestCase):
    def test_audio_video_offsets_share_one_timeline_origin(self) -> None:
        payload = {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "time_base": "1/90000",
                    "start_pts": "45000",
                    "start_time": "0.500000",
                    "duration_ts": "180000",
                    "duration": "2.000000",
                    "avg_frame_rate": "30000/1001",
                    "r_frame_rate": "30/1",
                    "width": 1920,
                    "height": 1080,
                },
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "time_base": "1/48000",
                    "start_pts": "26400",
                    "start_time": "0.550000",
                    "duration_ts": "96000",
                    "duration": "2.000000",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "start_time": "0.500000",
                "duration": "2.550000",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "fixture.mp4"
            source.write_bytes(b"fixture")
            manifest = parse_probe_payload(
                payload,
                source_path=source,
                source_sha256="a" * 64,
                file_size=7,
            )

        self.assertEqual(manifest.timeline_origin_us, 500_000)
        self.assertEqual(manifest.video_stream.start_time_us, 500_000)
        self.assertEqual(manifest.audio_stream.start_time_us, 550_000)
        self.assertEqual(
            manifest.audio_stream.start_time_us - manifest.timeline_origin_us,
            50_000,
        )
        self.assertEqual(manifest.duration_us, 2_550_000)
