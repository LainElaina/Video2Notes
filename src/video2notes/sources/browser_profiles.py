"""Read browser profile descriptors without touching cookie databases."""

from __future__ import annotations

import configparser
import contextlib
import json
import os
from pathlib import Path
from typing import Any

from video2notes.sources.models import BrowserKind, BrowserProfile


def enumerate_browser_profiles(
    *,
    local_app_data: str | Path | None = None,
    roaming_app_data: str | Path | None = None,
) -> list[BrowserProfile]:
    local_root = Path(
        local_app_data
        if local_app_data is not None
        else os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    roaming_root = Path(
        roaming_app_data
        if roaming_app_data is not None
        else os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    )

    profiles: list[BrowserProfile] = []
    profiles.extend(
        _enumerate_chromium(
            BrowserKind.CHROME,
            local_root / "Google" / "Chrome" / "User Data",
        )
    )
    profiles.extend(
        _enumerate_chromium(
            BrowserKind.EDGE,
            local_root / "Microsoft" / "Edge" / "User Data",
        )
    )
    profiles.extend(_enumerate_firefox(roaming_root / "Mozilla" / "Firefox"))
    return sorted(
        profiles,
        key=lambda item: (
            item.browser.value,
            not item.is_default,
            item.display_name.casefold(),
            item.profile_id.casefold(),
        ),
    )


def _enumerate_chromium(browser: BrowserKind, user_data: Path) -> list[BrowserProfile]:
    if not user_data.is_dir():
        return []

    info_cache: dict[str, Any] = {}
    local_state = user_data / "Local State"
    if local_state.is_file():
        try:
            payload = json.loads(local_state.read_text(encoding="utf-8"))
            raw_cache = payload.get("profile", {}).get("info_cache", {})
            if isinstance(raw_cache, dict):
                info_cache = raw_cache
        except (OSError, UnicodeError, json.JSONDecodeError):
            info_cache = {}

    profile_ids = {
        name
        for name in info_cache
        if name == "Default" or name.startswith("Profile ")
    }
    with contextlib.suppress(OSError):
        profile_ids.update(
            item.name
            for item in user_data.iterdir()
            if item.is_dir() and (item.name == "Default" or item.name.startswith("Profile "))
        )

    results: list[BrowserProfile] = []
    for profile_id in sorted(profile_ids):
        profile_path = user_data / profile_id
        if not profile_path.is_dir():
            continue
        metadata = info_cache.get(profile_id)
        display_name = profile_id
        if isinstance(metadata, dict):
            name = metadata.get("name")
            if isinstance(name, str) and name.strip():
                display_name = name.strip()
        results.append(
            BrowserProfile(
                browser=browser,
                profile_id=profile_id,
                display_name=display_name,
                path=str(profile_path.resolve()),
                is_default=profile_id == "Default",
            )
        )
    return results


def _enumerate_firefox(firefox_root: Path) -> list[BrowserProfile]:
    profiles_ini = firefox_root / "profiles.ini"
    if not profiles_ini.is_file():
        return []

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(profiles_ini, encoding="utf-8")
    except (OSError, configparser.Error, UnicodeError):
        return []

    results: list[BrowserProfile] = []
    for section in parser.sections():
        if not section.casefold().startswith("profile"):
            continue
        raw_path = parser.get(section, "Path", fallback="").strip()
        if not raw_path:
            continue
        is_relative = parser.getboolean(section, "IsRelative", fallback=True)
        profile_path = (firefox_root / raw_path) if is_relative else Path(raw_path)
        if not profile_path.is_dir():
            continue
        profile_id = profile_path.name
        display_name = parser.get(section, "Name", fallback=profile_id).strip() or profile_id
        results.append(
            BrowserProfile(
                browser=BrowserKind.FIREFOX,
                profile_id=profile_id,
                display_name=display_name,
                path=str(profile_path.resolve()),
                is_default=parser.getboolean(section, "Default", fallback=False),
            )
        )
    return results
