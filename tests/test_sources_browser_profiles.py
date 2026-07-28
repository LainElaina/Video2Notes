from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video2notes.sources import BrowserKind, enumerate_browser_profiles


class BrowserProfileTests(unittest.TestCase):
    def test_enumerates_descriptors_without_reading_cookie_databases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / "Local"
            roaming = root / "Roaming"

            chrome_root = local / "Google" / "Chrome" / "User Data"
            (chrome_root / "Default").mkdir(parents=True)
            (chrome_root / "Profile 1").mkdir()
            (chrome_root / "Default" / "Cookies").write_text(
                "SESSDATA=must-not-be-read",
                encoding="utf-8",
            )
            (chrome_root / "Local State").write_text(
                json.dumps(
                    {
                        "profile": {
                            "info_cache": {
                                "Default": {"name": "Main"},
                                "Profile 1": {"name": "Study"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            firefox_root = roaming / "Mozilla" / "Firefox"
            firefox_profile = firefox_root / "Profiles" / "abc.default-release"
            firefox_profile.mkdir(parents=True)
            (firefox_root / "profiles.ini").write_text(
                "\n".join(
                    [
                        "[Profile0]",
                        "Name=Default User",
                        "IsRelative=1",
                        "Path=Profiles/abc.default-release",
                        "Default=1",
                    ]
                ),
                encoding="utf-8",
            )

            profiles = enumerate_browser_profiles(
                local_app_data=local,
                roaming_app_data=roaming,
            )

        by_key = {(item.browser, item.profile_id): item for item in profiles}
        self.assertEqual(
            by_key[(BrowserKind.CHROME, "Default")].display_name,
            "Main",
        )
        self.assertEqual(
            by_key[(BrowserKind.CHROME, "Profile 1")].display_name,
            "Study",
        )
        self.assertTrue(
            by_key[(BrowserKind.FIREFOX, "abc.default-release")].is_default
        )
        serialized = "\n".join(item.model_dump_json() for item in profiles)
        self.assertNotIn("must-not-be-read", serialized)

