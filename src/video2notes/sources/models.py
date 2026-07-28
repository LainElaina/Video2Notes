"""Strict, JSON-safe models shared by all source adapters.

The acquisition layer deliberately stores references to browser profiles or
cookie files, never raw cookie/header values.  This keeps task manifests safe to
inspect and makes accidental secret echoing much harder.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Platform(StrEnum):
    LOCAL = "local"
    BILIBILI = "bilibili"
    YOUTUBE = "youtube"
    X = "x"


class SourceKind(StrEnum):
    LOCAL = "local"
    URL = "url"


class AuthKind(StrEnum):
    NONE = "none"
    BROWSER_PROFILE = "browser_profile"
    COOKIE_FILE = "cookie_file"


class BrowserKind(StrEnum):
    CHROME = "chrome"
    EDGE = "edge"
    FIREFOX = "firefox"


class QualityMode(StrEnum):
    FAST = "fast"
    ACCURATE = "accurate"


class ProgressKind(StrEnum):
    PROBE = "probe"
    DOWNLOAD = "download"
    POSTPROCESS = "postprocess"
    COMPLETED = "completed"
    WARNING = "warning"


class SourceInput(StrictModel):
    kind: SourceKind
    value: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def strip_value(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("source value cannot be empty")
        return stripped

    @classmethod
    def local(cls, path: str | Path) -> Self:
        return cls(kind=SourceKind.LOCAL, value=str(path))

    @classmethod
    def url(cls, url: str) -> Self:
        return cls(kind=SourceKind.URL, value=url)


class AuthSpec(StrictModel):
    """A non-secret reference to authentication material.

    Browser profiles are read by yt-dlp for the duration of a task.  Explicit
    cookie files remain user-owned local files.  There is intentionally no
    ``cookie`` or ``headers`` field in this model.
    """

    kind: AuthKind = AuthKind.NONE
    browser: BrowserKind | None = None
    profile: str | None = None
    keyring: str | None = None
    container: str | None = None
    cookie_file: str | None = None

    @model_validator(mode="after")
    def validate_auth_shape(self) -> Self:
        if self.kind is AuthKind.NONE:
            if any(
                value is not None
                for value in (
                    self.browser,
                    self.profile,
                    self.keyring,
                    self.container,
                    self.cookie_file,
                )
            ):
                raise ValueError("none auth cannot contain browser or cookie-file settings")
        elif self.kind is AuthKind.BROWSER_PROFILE:
            if self.browser is None or not self.profile:
                raise ValueError("browser_profile auth requires browser and profile")
            if self.cookie_file is not None:
                raise ValueError("browser_profile auth cannot also contain a cookie file")
        elif self.kind is AuthKind.COOKIE_FILE:
            if not self.cookie_file:
                raise ValueError("cookie_file auth requires cookie_file")
            if any(
                value is not None
                for value in (self.browser, self.profile, self.keyring, self.container)
            ):
                raise ValueError("cookie_file auth cannot contain browser settings")
        return self

    @classmethod
    def browser_profile(
        cls,
        browser: BrowserKind,
        profile: str,
        *,
        keyring: str | None = None,
        container: str | None = None,
    ) -> Self:
        return cls(
            kind=AuthKind.BROWSER_PROFILE,
            browser=browser,
            profile=profile,
            keyring=keyring,
            container=container,
        )

    @classmethod
    def cookies_txt(cls, path: str | Path) -> Self:
        return cls(kind=AuthKind.COOKIE_FILE, cookie_file=str(path))


class BrowserProfile(StrictModel):
    browser: BrowserKind
    profile_id: str
    display_name: str
    path: str
    is_default: bool = False


class FormatInfo(StrictModel):
    format_id: str = Field(min_length=1)
    ext: str | None = None
    protocol: str | None = None
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    fps: float | None = Field(default=None, ge=0)
    vcodec: str | None = None
    acodec: str | None = None
    video_bitrate_kbps: float | None = Field(default=None, ge=0)
    audio_bitrate_kbps: float | None = Field(default=None, ge=0)
    total_bitrate_kbps: float | None = Field(default=None, ge=0)
    audio_sample_rate: int | None = Field(default=None, ge=0)
    filesize: int | None = Field(default=None, ge=0)
    filesize_approx: int | None = Field(default=None, ge=0)
    language: str | None = None
    format_note: str | None = None
    dynamic_range: str | None = None
    has_drm: bool = False

    @property
    def has_video(self) -> bool:
        return bool(self.vcodec and self.vcodec not in {"none", "images"})

    @property
    def has_audio(self) -> bool:
        return bool(self.acodec and self.acodec != "none")


class AcquisitionPolicy(StrictModel):
    mode: QualityMode = QualityMode.ACCURATE
    max_height: int | None = Field(default=None, gt=0)
    prefer_playback_compatible: bool | None = None
    preferred_container: str = "mkv/mp4"
    include_subtitles: bool = True
    subtitle_languages: list[str] = Field(
        default_factory=lambda: ["zh-Hans", "zh", "zh-CN", "zh-TW", "en", "ja"]
    )
    include_automatic_captions: bool = True
    allow_quality_change: bool = False
    prefer_hardlink: bool = True

    @property
    def effective_max_height(self) -> int | None:
        if self.max_height is not None:
            return self.max_height
        return 1080 if self.mode is QualityMode.FAST else None

    @property
    def effective_playback_preference(self) -> bool:
        if self.prefer_playback_compatible is not None:
            return self.prefer_playback_compatible
        return self.mode is QualityMode.FAST

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        return stable_fingerprint(payload)


class SourceManifest(StrictModel):
    schema_version: int = 1
    platform: Platform
    source: SourceInput
    source_id: str
    canonical_url: str | None = None
    extractor_key: str | None = None
    title: str | None = None
    author: str | None = None
    description: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    thumbnail_url: str | None = None
    is_live: bool = False
    availability: str | None = None
    age_limit: int | None = Field(default=None, ge=0)
    has_drm: bool = False
    formats: list[FormatInfo] = Field(default_factory=list)
    subtitles: dict[str, list[str]] = Field(default_factory=dict)
    automatic_captions: dict[str, list[str]] = Field(default_factory=dict)
    selected_format_ids: list[str] = Field(default_factory=list)
    selected_format_expression: str | None = None
    selected_quality_fingerprint: str | None = None
    acquisition_policy_fingerprint: str | None = None
    auth_kind: AuthKind = AuthKind.NONE
    quality_warning: str | None = None
    yt_dlp_version: str | None = None
    probed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if self.selected_format_ids and not self.selected_format_expression:
            raise ValueError("selected formats require an exact format expression")
        if self.selected_format_expression and not self.selected_quality_fingerprint:
            raise ValueError("selected format expression requires a quality fingerprint")
        return self

    @property
    def selected_formats(self) -> list[FormatInfo]:
        selected = set(self.selected_format_ids)
        return [item for item in self.formats if item.format_id in selected]


class AcquisitionResult(StrictModel):
    schema_version: int = 1
    platform: Platform
    source_id: str
    media_path: str
    subtitle_paths: list[str] = Field(default_factory=list)
    actual_format_ids: list[str] = Field(default_factory=list)
    actual_width: int | None = Field(default=None, ge=0)
    actual_height: int | None = Field(default=None, ge=0)
    actual_fps: float | None = Field(default=None, ge=0)
    actual_vcodec: str | None = None
    actual_acodec: str | None = None
    source_sha256: str
    quality_fingerprint: str
    quality_changed: bool = False
    resumable_partial_present: bool = False
    warnings: list[str] = Field(default_factory=list)

    @field_validator("source_sha256", "quality_fingerprint")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("fingerprint must be a 64-character SHA-256 hex string")
        return normalized


class ProgressEvent(StrictModel):
    kind: ProgressKind
    platform: Platform
    phase: str
    status: str
    downloaded_bytes: int | None = Field(default=None, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    total_bytes_estimate: int | None = Field(default=None, ge=0)
    speed_bps: float | None = Field(default=None, ge=0)
    eta_seconds: float | None = Field(default=None, ge=0)
    elapsed_seconds: float | None = Field(default=None, ge=0)
    fragment_index: int | None = Field(default=None, ge=0)
    fragment_count: int | None = Field(default=None, ge=0)
    postprocessor: str | None = None
    message: str | None = None


def stable_fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

