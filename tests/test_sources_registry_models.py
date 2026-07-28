from __future__ import annotations

import unittest

from pydantic import ValidationError

from video2notes.sources import (
    AcquisitionPolicy,
    AuthSpec,
    FormatInfo,
    Platform,
    QualityMode,
    SourceInput,
    SourceRegistry,
    UnsupportedSourceError,
    format_selection_fingerprint,
    select_exact_formats,
)


class SourceRegistryTests(unittest.TestCase):
    def test_routes_supported_platform_hosts(self) -> None:
        registry = SourceRegistry.default()
        cases = {
            "https://www.bilibili.com/video/BV1xx411c7mD": Platform.BILIBILI,
            "https://b23.tv/example": Platform.BILIBILI,
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ": Platform.YOUTUBE,
            "https://youtu.be/dQw4w9WgXcQ": Platform.YOUTUBE,
            "https://x.com/example/status/123": Platform.X,
            "https://mobile.twitter.com/example/status/123": Platform.X,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(registry.resolve(SourceInput.url(url)).platform, expected)

    def test_rejects_domain_deception_and_embedded_credentials(self) -> None:
        registry = SourceRegistry.default()
        deceptive = [
            "https://youtube.com.attacker.invalid/watch?v=dQw4w9WgXcQ",
            "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
            "https://x.com.attacker.invalid/example/status/123",
            "https://bilibili.com@attacker.invalid/video/BV1xx411c7mD",
            "https://attacker.invalid/?next=https://www.youtube.com/watch",
            "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
        ]
        for url in deceptive:
            with self.subTest(url=url), self.assertRaises(UnsupportedSourceError):
                registry.resolve(SourceInput.url(url))


class SourceModelTests(unittest.TestCase):
    def test_auth_model_refuses_raw_cookie_field(self) -> None:
        with self.assertRaises(ValidationError):
            AuthSpec.model_validate(
                {
                    "kind": "browser_profile",
                    "browser": "chrome",
                    "profile": "Default",
                    "cookie": "SESSDATA=do-not-store-this",
                }
            )

    def test_fast_and_accurate_choose_exact_different_formats(self) -> None:
        formats = [
            FormatInfo(
                format_id="v4k",
                ext="webm",
                width=3840,
                height=2160,
                fps=60,
                vcodec="av01",
                acodec="none",
                video_bitrate_kbps=12000,
            ),
            FormatInfo(
                format_id="v1080",
                ext="mp4",
                width=1920,
                height=1080,
                fps=30,
                vcodec="avc1.640028",
                acodec="none",
                video_bitrate_kbps=5000,
            ),
            FormatInfo(
                format_id="a160",
                ext="m4a",
                vcodec="none",
                acodec="mp4a.40.2",
                audio_bitrate_kbps=160,
            ),
        ]
        accurate, accurate_expression, _ = select_exact_formats(
            formats,
            AcquisitionPolicy(mode=QualityMode.ACCURATE),
        )
        fast, fast_expression, _ = select_exact_formats(
            formats,
            AcquisitionPolicy(mode=QualityMode.FAST),
        )

        self.assertEqual(accurate_expression, "v4k+a160")
        self.assertEqual(fast_expression, "v1080+a160")
        self.assertNotEqual(
            format_selection_fingerprint("video", accurate),
            format_selection_fingerprint("video", fast),
        )

