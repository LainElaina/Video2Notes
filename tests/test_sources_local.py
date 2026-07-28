from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from video2notes.domain import MediaManifest, MediaStream, Rational
from video2notes.sources import (
    AcquisitionPolicy,
    LocalFileAdapter,
    ProgressKind,
    QualityChangedError,
    SourceInput,
)


class LocalFileAdapterTests(unittest.TestCase):
    def test_probe_and_copy_import_do_not_modify_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "lesson.mp4"
            original = b"deterministic-local-video-fixture"
            source.write_bytes(original)
            source_hash = hashlib.sha256(original).hexdigest()

            def fake_probe(path: str | Path) -> MediaManifest:
                self.assertEqual(Path(path), source.resolve())
                return MediaManifest(
                    source_path=str(source.resolve()),
                    source_sha256=source_hash,
                    container_format="mov,mp4",
                    file_size=len(original),
                    duration_us=7_000_000,
                    timeline_origin_us=0,
                    streams=[
                        MediaStream(
                            index=0,
                            codec_type="video",
                            codec_name="h264",
                            time_base=Rational(numerator=1, denominator=15_360),
                            start_pts=0,
                            start_time_us=0,
                            duration_ts=107_520,
                            duration_us=7_000_000,
                            avg_frame_rate=Rational(numerator=30, denominator=1),
                            width=640,
                            height=360,
                        )
                    ],
                )

            adapter = LocalFileAdapter(probe_fn=fake_probe)
            events = []
            manifest = asyncio.run(
                adapter.probe(SourceInput.local(source), progress=events.append)
            )
            destination = root / "run" / "source"
            result = asyncio.run(
                adapter.acquire(
                    manifest,
                    AcquisitionPolicy(prefer_hardlink=False),
                    destination,
                    progress=events.append,
                )
            )

            imported = Path(result.media_path)
            self.assertNotEqual(imported, source)
            self.assertEqual(imported.read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(result.source_sha256, source_hash)
            self.assertEqual(manifest.duration_seconds, 7)
            self.assertEqual(manifest.selected_format_ids, ["local-file"])
            self.assertTrue(any(item.kind is ProgressKind.DOWNLOAD for item in events))
            self.assertEqual(events[-1].kind, ProgressKind.COMPLETED)

    def test_detects_source_change_after_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "lesson.mp4"
            source.write_bytes(b"before")
            source_hash = hashlib.sha256(b"before").hexdigest()

            adapter = LocalFileAdapter(
                probe_fn=lambda path: MediaManifest(
                    source_path=str(Path(path).resolve()),
                    source_sha256=source_hash,
                    file_size=6,
                    duration_us=1,
                    timeline_origin_us=0,
                    streams=[
                        MediaStream(
                            index=0,
                            codec_type="video",
                            codec_name="h264",
                            time_base=Rational(numerator=1, denominator=1),
                        )
                    ],
                )
            )
            manifest = asyncio.run(adapter.probe(SourceInput.local(source)))
            source.write_bytes(b"after")

            with self.assertRaises(QualityChangedError):
                asyncio.run(
                    adapter.acquire(
                        manifest,
                        AcquisitionPolicy(prefer_hardlink=False),
                        root / "destination",
                    )
                )

