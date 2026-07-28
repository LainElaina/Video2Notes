from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from video2notes.sources import (
    AcquisitionPolicy,
    AuthSpec,
    BrowserKind,
    Platform,
    ProgressKind,
    QualityMode,
    SourceInput,
    SourceProbeError,
    YouTubeAdapter,
    progress_event_from_ytdlp,
)


def sample_info() -> dict[str, Any]:
    return {
        "id": "video123",
        "title": "Fixture video",
        "uploader": "Fixture author",
        "duration": 12.5,
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "extractor_key": "Youtube",
        "formats": [
            {
                "format_id": "v2160",
                "ext": "webm",
                "width": 3840,
                "height": 2160,
                "fps": 60,
                "vcodec": "av01",
                "acodec": "none",
                "vbr": 12000,
            },
            {
                "format_id": "v1080",
                "ext": "mp4",
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "vcodec": "avc1.640028",
                "acodec": "none",
                "vbr": 5000,
            },
            {
                "format_id": "a160",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "abr": 160,
                "asr": 48000,
            },
        ],
        "subtitles": {"en": [{"ext": "vtt"}]},
        "automatic_captions": {"zh-Hans": [{"ext": "vtt"}, {"ext": "json3"}]},
    }


class FakeYDL:
    instances: list[FakeYDL] = []

    def __init__(self, params: dict[str, Any]):
        self.params = params
        self.instances.append(self)

    def __enter__(self) -> FakeYDL:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
        del url, download
        return sample_info()

    def process_ie_result(
        self,
        info: dict[str, Any],
        download: bool = True,
    ) -> dict[str, Any]:
        self.assert_download(download)
        for hook in self.params.get("progress_hooks", []):
            hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 512,
                    "total_bytes": 1024,
                    "speed": 256,
                    "eta": 2,
                    "elapsed": 1,
                    "fragment_index": 1,
                    "fragment_count": 2,
                }
            )
        root = Path(self.params["paths"]["home"])
        media = root / "Youtube-video123.mkv"
        media.write_bytes(b"downloaded-media")
        subtitle = root / "Youtube-video123.en.vtt"
        subtitle.write_text("WEBVTT", encoding="utf-8")
        info = dict(info)
        info["filepath"] = str(media)
        for hook in self.params.get("postprocessor_hooks", []):
            hook(
                {
                    "status": "finished",
                    "postprocessor": "Merger",
                    "info_dict": info,
                }
            )
        for hook in self.params.get("progress_hooks", []):
            hook(
                {
                    "status": "finished",
                    "filename": str(media),
                    "downloaded_bytes": len(b"downloaded-media"),
                    "total_bytes": len(b"downloaded-media"),
                }
            )
        return info

    def prepare_filename(self, info: dict[str, Any]) -> str:
        return str(info.get("filepath") or "")

    @staticmethod
    def assert_download(download: bool) -> None:
        if not download:
            raise AssertionError("process_ie_result must download")


class FailingYDL(FakeYDL):
    def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
        del url, download
        self.params["logger"].warning(
            "Cookie: SESSDATA=warning-secret; auth_token=warning-token"
        )
        raise RuntimeError("SESSDATA=exception-secret auth_token=exception-token")


class YtDlpAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeYDL.instances.clear()
        self.source = SourceInput.url(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )

    def test_controlled_auth_and_download_options(self) -> None:
        adapter = YouTubeAdapter(ydl_factory=FakeYDL)
        browser_auth = AuthSpec.browser_profile(
            BrowserKind.CHROME,
            "Profile 1",
        )
        probe_options = adapter.build_probe_options(browser_auth)
        self.assertEqual(
            probe_options["cookiesfrombrowser"],
            ("chrome", "Profile 1", None, None),
        )
        self.assertEqual(probe_options["js_runtimes"], {"node": {}})
        self.assertNotIn("exec_cmd", probe_options)
        self.assertNotIn("external_downloader", probe_options)

        with tempfile.TemporaryDirectory() as temporary:
            cookie_file = Path(temporary) / "cookies.txt"
            cookie_file.write_text(
                "# Netscape HTTP Cookie File\n",
                encoding="utf-8",
            )
            manifest = asyncio.run(
                adapter.probe(
                    self.source,
                    AuthSpec.cookies_txt(cookie_file),
                    AcquisitionPolicy(mode=QualityMode.FAST),
                )
            )
            options = adapter.build_acquire_options(
                manifest,
                AcquisitionPolicy(mode=QualityMode.FAST),
                Path(temporary) / "source",
                AuthSpec.cookies_txt(cookie_file),
            )

        self.assertEqual(options["format"], "v1080+a160")
        self.assertEqual(options["cookiefile"], str(cookie_file.resolve()))
        self.assertTrue(options["continuedl"])
        self.assertFalse(options["nopart"])
        self.assertEqual(options["retries"], 3)
        self.assertEqual(options["fragment_retries"], 10)
        self.assertEqual(options["outtmpl"]["default"], "%(extractor_key)s-%(id)s.%(ext)s")

    def test_probe_and_acquire_emit_normalized_progress(self) -> None:
        adapter = YouTubeAdapter(ydl_factory=FakeYDL)
        policy = AcquisitionPolicy(mode=QualityMode.ACCURATE)
        events = []
        manifest = asyncio.run(
            adapter.probe(self.source, policy=policy, progress=events.append)
        )
        self.assertEqual(manifest.selected_format_expression, "v2160+a160")
        self.assertEqual(manifest.subtitles, {"en": ["vtt"]})
        self.assertEqual(
            manifest.automatic_captions,
            {"zh-Hans": ["json3", "vtt"]},
        )

        with tempfile.TemporaryDirectory() as temporary:
            result = asyncio.run(
                adapter.acquire(
                    manifest,
                    policy,
                    Path(temporary),
                    progress=events.append,
                )
            )
            self.assertTrue(Path(result.media_path).is_file())
            self.assertEqual(len(result.subtitle_paths), 1)
            self.assertEqual(result.actual_format_ids, ["v2160", "a160"])

        download_events = [item for item in events if item.kind is ProgressKind.DOWNLOAD]
        self.assertGreaterEqual(len(download_events), 2)
        self.assertEqual(download_events[0].downloaded_bytes, 512)
        self.assertTrue(any(item.kind is ProgressKind.POSTPROCESS for item in events))
        self.assertEqual(events[-1].kind, ProgressKind.COMPLETED)

    def test_logs_and_errors_are_redacted(self) -> None:
        adapter = YouTubeAdapter(ydl_factory=FailingYDL)
        events = []
        with self.assertRaises(SourceProbeError) as captured:
            asyncio.run(adapter.probe(self.source, progress=events.append))

        combined = str(captured.exception) + "\n" + "\n".join(
            item.message or "" for item in events
        )
        self.assertNotIn("warning-secret", combined)
        self.assertNotIn("warning-token", combined)
        self.assertNotIn("exception-secret", combined)
        self.assertNotIn("exception-token", combined)
        self.assertIn("<redacted>", combined)

    def test_progress_payload_mapping(self) -> None:
        event = progress_event_from_ytdlp(
            {
                "status": "downloading",
                "downloaded_bytes": "25",
                "total_bytes_estimate": 100,
                "speed": 12.5,
                "eta": 6,
                "fragment_index": 2,
                "fragment_count": 8,
            },
            platform=Platform.X,
        )
        self.assertEqual(event.downloaded_bytes, 25)
        self.assertEqual(event.total_bytes_estimate, 100)
        self.assertEqual(event.speed_bps, 12.5)
        self.assertEqual(event.fragment_index, 2)

