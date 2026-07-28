"""Controlled yt-dlp adapters for Bilibili, YouTube, and X.

Only structured application settings are translated into yt-dlp options.  The
caller cannot provide arbitrary output templates, postprocessors, commands, or
external downloaders.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeAlias

import yt_dlp  # type: ignore[import-untyped]
from yt_dlp.version import __version__ as YT_DLP_VERSION  # type: ignore[import-untyped]

from video2notes.artifacts import sha256_file
from video2notes.sources.errors import (
    AcquisitionCancelled,
    CancellationToken,
    QualityChangedError,
    SourceAcquisitionError,
    SourceProbeError,
    UnsupportedSourceError,
)
from video2notes.sources.models import (
    AcquisitionPolicy,
    AcquisitionResult,
    AuthKind,
    AuthSpec,
    FormatInfo,
    Platform,
    ProgressEvent,
    ProgressKind,
    SourceInput,
    SourceKind,
    SourceManifest,
    stable_fingerprint,
)
from video2notes.sources.registry import platform_for_url

ProgressSink: TypeAlias = Callable[[ProgressEvent], None]
YdlFactory: TypeAlias = Callable[[dict[str, Any]], Any]
EXPECTED_YT_DLP_VERSION = "2026.07.04"

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b("
    r"SESSDATA|bili_jct|DedeUserID(?:__ckMd5)?|"
    r"SAPISID|APISID|HSID|SSID|SID|LOGIN_INFO|"
    r"auth_token|ct0|twid|guest_token|csrf(?:_token)?|access_token|refresh_token"
    r")=([^;\s&#]+)"
)
_HEADER_SECRET = re.compile(
    r"(?i)\b(cookie|authorization|proxy-authorization)\s*[:=]\s*([^\r\n]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|access_token|auth|authorization|sig|signature|key)="
    r")([^&#\s]+)"
)

_SUBTITLE_EXTENSIONS = {
    ".ass",
    ".json3",
    ".lrc",
    ".srt",
    ".ssa",
    ".ttml",
    ".vtt",
}
_NON_MEDIA_SUFFIXES = _SUBTITLE_EXTENSIONS | {".part", ".ytdl", ".json"}


def redact_sensitive(value: object) -> str:
    text = str(value)
    text = _HEADER_SECRET.sub(lambda match: f"{match.group(1)}: <redacted>", text)
    text = _BEARER_SECRET.sub("Bearer <redacted>", text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return _QUERY_SECRET.sub(lambda match: f"{match.group(1)}<redacted>", text)


def progress_event_from_ytdlp(
    payload: Mapping[str, Any],
    *,
    platform: Platform,
) -> ProgressEvent:
    return ProgressEvent(
        kind=ProgressKind.DOWNLOAD,
        platform=platform,
        phase="download",
        status=str(payload.get("status") or "unknown"),
        downloaded_bytes=_nonnegative_int(payload.get("downloaded_bytes")),
        total_bytes=_nonnegative_int(payload.get("total_bytes")),
        total_bytes_estimate=_nonnegative_int(payload.get("total_bytes_estimate")),
        speed_bps=_nonnegative_float(payload.get("speed")),
        eta_seconds=_nonnegative_float(payload.get("eta")),
        elapsed_seconds=_nonnegative_float(payload.get("elapsed")),
        fragment_index=_nonnegative_int(payload.get("fragment_index")),
        fragment_count=_nonnegative_int(payload.get("fragment_count")),
    )


class YtDlpAdapter:
    """Base adapter; use one of the platform-specific subclasses."""

    platform: Platform

    def __init__(
        self,
        platform: Platform,
        *,
        ydl_factory: YdlFactory | None = None,
    ):
        if platform is Platform.LOCAL:
            raise ValueError("YtDlpAdapter cannot be used for local files")
        self.platform = platform
        self._ydl_factory = ydl_factory or yt_dlp.YoutubeDL

    def matches(self, source: SourceInput) -> bool:
        if source.kind is not SourceKind.URL:
            return False
        try:
            return platform_for_url(source.value) is self.platform
        except UnsupportedSourceError:
            return False

    async def probe(
        self,
        source: SourceInput,
        auth: AuthSpec | None = None,
        policy: AcquisitionPolicy | None = None,
        *,
        progress: ProgressSink | None = None,
        cancel: CancellationToken | None = None,
    ) -> SourceManifest:
        return await asyncio.to_thread(
            self._probe_sync,
            source,
            auth or AuthSpec(),
            policy or AcquisitionPolicy(),
            progress,
            cancel or CancellationToken(),
        )

    def _probe_sync(
        self,
        source: SourceInput,
        auth: AuthSpec,
        policy: AcquisitionPolicy,
        progress: ProgressSink | None,
        cancel: CancellationToken,
    ) -> SourceManifest:
        self._validate_source(source)
        cancel.raise_if_cancelled()
        warnings: list[str] = []
        _emit(
            progress,
            ProgressEvent(
                kind=ProgressKind.PROBE,
                platform=self.platform,
                phase="extract_metadata",
                status="started",
            ),
        )
        try:
            options = self.build_probe_options(
                auth,
                progress=progress,
                cancel=cancel,
                warnings=warnings,
            )
            with self._ydl_factory(options) as ydl:
                info = ydl.extract_info(source.value, download=False)
            cancel.raise_if_cancelled()
            manifest = self._manifest_from_info(
                source,
                info,
                auth=auth,
                policy=policy,
                warnings=warnings,
            )
        except AcquisitionCancelled:
            raise
        except Exception as exc:
            raise SourceProbeError(redact_sensitive(exc)) from None
        _emit(
            progress,
            ProgressEvent(
                kind=ProgressKind.PROBE,
                platform=self.platform,
                phase="extract_metadata",
                status="finished",
            ),
        )
        return manifest

    async def acquire(
        self,
        manifest: SourceManifest,
        policy: AcquisitionPolicy,
        destination: str | Path,
        auth: AuthSpec | None = None,
        *,
        progress: ProgressSink | None = None,
        cancel: CancellationToken | None = None,
    ) -> AcquisitionResult:
        return await asyncio.to_thread(
            self._acquire_sync,
            manifest,
            policy,
            Path(destination),
            auth or AuthSpec(),
            progress,
            cancel or CancellationToken(),
        )

    def _acquire_sync(
        self,
        manifest: SourceManifest,
        policy: AcquisitionPolicy,
        destination: Path,
        auth: AuthSpec,
        progress: ProgressSink | None,
        cancel: CancellationToken,
    ) -> AcquisitionResult:
        if manifest.platform is not self.platform:
            raise ValueError(f"manifest does not belong to the {self.platform} adapter")
        self._validate_source(manifest.source)
        cancel.raise_if_cancelled()
        destination = destination.expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        hook_paths: list[str] = []
        try:
            options = self.build_acquire_options(
                manifest,
                policy,
                destination,
                auth,
                progress=progress,
                cancel=cancel,
                warnings=warnings,
                hook_paths=hook_paths,
            )
            with self._ydl_factory(options) as ydl:
                current_info = ydl.extract_info(manifest.source.value, download=False)
                current = self._manifest_from_info(
                    manifest.source,
                    current_info,
                    auth=auth,
                    policy=policy,
                    warnings=warnings,
                )
                quality_changed = (
                    current.source_id != manifest.source_id
                    or current.selected_quality_fingerprint
                    != manifest.selected_quality_fingerprint
                    or current.acquisition_policy_fingerprint
                    != manifest.acquisition_policy_fingerprint
                )
                selected_manifest = manifest
                if quality_changed:
                    if not policy.allow_quality_change:
                        raise QualityChangedError(
                            "available media quality changed after probe; "
                            "probe again before download"
                        )
                    selected_manifest = current
                    warnings.append(
                        "available quality changed after probe; current selection was used"
                    )
                    ydl.params["format"] = current.selected_format_expression
                cancel.raise_if_cancelled()
                if hasattr(ydl, "process_ie_result"):
                    downloaded_info = ydl.process_ie_result(current_info, download=True)
                else:
                    downloaded_info = ydl.extract_info(manifest.source.value, download=True)
                cancel.raise_if_cancelled()
                media_path = _find_media_path(
                    downloaded_info,
                    ydl=ydl,
                    destination=destination,
                    hook_paths=hook_paths,
                )
        except (AcquisitionCancelled, QualityChangedError):
            raise
        except Exception as exc:
            raise SourceAcquisitionError(redact_sensitive(exc)) from None

        selected = selected_manifest.selected_formats
        video = next((item for item in selected if item.has_video), None)
        audio = next((item for item in selected if item.has_audio and item is not video), None)
        if audio is None and video is not None and video.has_audio:
            audio = video
        subtitles = _find_subtitles(destination, media_path)
        partial_present = any(
            item.is_file() and item.name.endswith((".part", ".ytdl"))
            for item in destination.rglob("*")
        )
        media_hash = sha256_file(media_path)
        _emit(
            progress,
            ProgressEvent(
                kind=ProgressKind.COMPLETED,
                platform=self.platform,
                phase="acquire",
                status="finished",
                downloaded_bytes=media_path.stat().st_size,
                total_bytes=media_path.stat().st_size,
            ),
        )
        return AcquisitionResult(
            platform=self.platform,
            source_id=selected_manifest.source_id,
            media_path=str(media_path),
            subtitle_paths=[str(item) for item in subtitles],
            actual_format_ids=selected_manifest.selected_format_ids,
            actual_width=video.width if video else None,
            actual_height=video.height if video else None,
            actual_fps=video.fps if video else None,
            actual_vcodec=video.vcodec if video else None,
            actual_acodec=audio.acodec if audio else None,
            source_sha256=media_hash,
            quality_fingerprint=(
                selected_manifest.selected_quality_fingerprint
                or stable_fingerprint(selected_manifest.selected_format_ids)
            ),
            quality_changed=quality_changed,
            resumable_partial_present=partial_present,
            warnings=[redact_sensitive(item) for item in warnings],
        )

    def build_probe_options(
        self,
        auth: AuthSpec,
        *,
        progress: ProgressSink | None = None,
        cancel: CancellationToken | None = None,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        cancel = cancel or CancellationToken()
        warnings = warnings if warnings is not None else []
        options: dict[str, Any] = {
            "noplaylist": True,
            "skip_download": True,
            "simulate": True,
            "quiet": True,
            "no_warnings": False,
            "extract_flat": False,
            "js_runtimes": {"node": {}},
            "logger": _SafeYtDlpLogger(self.platform, progress, warnings),
        }
        options.update(self._auth_options(auth))
        options.update(self._platform_options())
        cancel.raise_if_cancelled()
        return options

    def build_acquire_options(
        self,
        manifest: SourceManifest,
        policy: AcquisitionPolicy,
        destination: str | Path,
        auth: AuthSpec,
        *,
        progress: ProgressSink | None = None,
        cancel: CancellationToken | None = None,
        warnings: list[str] | None = None,
        hook_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        if not manifest.selected_format_expression:
            raise ValueError("manifest does not contain an exact format selection")
        cancel = cancel or CancellationToken()
        warnings = warnings if warnings is not None else []
        hook_paths = hook_paths if hook_paths is not None else []
        root = Path(destination).expanduser().resolve()
        temporary = root / ".temp"
        temporary.mkdir(parents=True, exist_ok=True)

        def download_hook(payload: dict[str, Any]) -> None:
            cancel.raise_if_cancelled()
            filename = payload.get("filename")
            if isinstance(filename, str):
                hook_paths.append(filename)
            _emit(
                progress,
                progress_event_from_ytdlp(payload, platform=self.platform),
            )

        def postprocessor_hook(payload: dict[str, Any]) -> None:
            cancel.raise_if_cancelled()
            info = payload.get("info_dict")
            if isinstance(info, dict):
                for key in ("filepath", "_filename"):
                    candidate = info.get(key)
                    if isinstance(candidate, str):
                        hook_paths.append(candidate)
            _emit(
                progress,
                ProgressEvent(
                    kind=ProgressKind.POSTPROCESS,
                    platform=self.platform,
                    phase="postprocess",
                    status=str(payload.get("status") or "unknown"),
                    postprocessor=redact_sensitive(payload.get("postprocessor") or "") or None,
                ),
            )

        options: dict[str, Any] = {
            "noplaylist": True,
            "format": manifest.selected_format_expression,
            "paths": {"home": str(root), "temp": str(temporary)},
            "outtmpl": {
                "default": "%(extractor_key)s-%(id)s.%(ext)s",
                "subtitle": "%(extractor_key)s-%(id)s.%(language)s.%(ext)s",
            },
            "continuedl": True,
            "nopart": False,
            "overwrites": False,
            "retries": 3,
            "fragment_retries": 10,
            "file_access_retries": 3,
            "concurrent_fragment_downloads": 4,
            "merge_output_format": policy.preferred_container,
            "windowsfilenames": True,
            "writeinfojson": False,
            "writedescription": False,
            "writethumbnail": False,
            "writesubtitles": policy.include_subtitles,
            "writeautomaticsub": (
                policy.include_subtitles and policy.include_automatic_captions
            ),
            "subtitleslangs": list(policy.subtitle_languages),
            "subtitlesformat": "vtt/best",
            "quiet": True,
            "no_warnings": False,
            "js_runtimes": {"node": {}},
            "progress_hooks": [download_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "logger": _SafeYtDlpLogger(self.platform, progress, warnings),
        }
        options.update(self._auth_options(auth))
        options.update(self._platform_options())
        return options

    def _auth_options(self, auth: AuthSpec) -> dict[str, Any]:
        if auth.kind is AuthKind.NONE:
            return {}
        if auth.kind is AuthKind.BROWSER_PROFILE:
            if auth.browser is None or auth.profile is None:
                raise ValueError("browser profile auth is incomplete")
            return {
                "cookiesfrombrowser": (
                    auth.browser.value,
                    auth.profile,
                    auth.keyring,
                    auth.container,
                )
            }
        if auth.kind is AuthKind.COOKIE_FILE:
            if auth.cookie_file is None:
                raise ValueError("cookie-file auth is incomplete")
            cookie_path = Path(auth.cookie_file).expanduser().resolve(strict=True)
            if not cookie_path.is_file():
                raise FileNotFoundError("cookie file is not a regular file")
            return {"cookiefile": str(cookie_path)}
        raise ValueError(f"unsupported authentication kind: {auth.kind}")

    def _platform_options(self) -> dict[str, Any]:
        if self.platform is Platform.BILIBILI:
            return {"http_headers": {"Referer": "https://www.bilibili.com/"}}
        return {}

    def _validate_source(self, source: SourceInput) -> None:
        if not self.matches(source):
            raise UnsupportedSourceError(f"URL does not belong to {self.platform.value}")

    def _manifest_from_info(
        self,
        source: SourceInput,
        raw_info: Any,
        *,
        auth: AuthSpec,
        policy: AcquisitionPolicy,
        warnings: list[str],
    ) -> SourceManifest:
        if not isinstance(raw_info, dict):
            raise SourceProbeError("yt-dlp did not return media metadata")
        if raw_info.get("_type") in {"playlist", "multi_video"}:
            raise SourceProbeError("source contains multiple media items; select one video URL")
        source_id = str(raw_info.get("id") or "").strip()
        if not source_id:
            raise SourceProbeError("yt-dlp metadata is missing a source id")
        raw_formats = raw_info.get("formats")
        if isinstance(raw_formats, list) and raw_formats:
            formats = [
                _format_from_ytdlp(item)
                for item in raw_formats
                if isinstance(item, dict) and item.get("format_id") is not None
            ]
        else:
            formats = [_format_from_ytdlp(raw_info)]
        formats = [item for item in formats if not item.has_drm]
        selected, expression, warning = select_exact_formats(formats, policy)
        if warning:
            warnings.append(warning)
        fingerprint = format_selection_fingerprint(source_id, selected)
        return SourceManifest(
            platform=self.platform,
            source=source,
            source_id=source_id,
            canonical_url=_optional_text(raw_info.get("webpage_url")) or source.value,
            extractor_key=_optional_text(
                raw_info.get("extractor_key") or raw_info.get("extractor")
            ),
            title=_optional_text(raw_info.get("title")),
            author=_optional_text(raw_info.get("uploader") or raw_info.get("channel")),
            description=_optional_text(raw_info.get("description")),
            duration_seconds=_nonnegative_float(raw_info.get("duration")),
            thumbnail_url=_optional_text(raw_info.get("thumbnail")),
            is_live=bool(raw_info.get("is_live")),
            availability=_optional_text(raw_info.get("availability")),
            age_limit=_nonnegative_int(raw_info.get("age_limit")),
            has_drm=bool(raw_info.get("_has_drm") or raw_info.get("has_drm")),
            formats=formats,
            subtitles=_subtitle_summary(raw_info.get("subtitles")),
            automatic_captions=_subtitle_summary(raw_info.get("automatic_captions")),
            selected_format_ids=[item.format_id for item in selected],
            selected_format_expression=expression,
            selected_quality_fingerprint=fingerprint,
            acquisition_policy_fingerprint=policy.fingerprint(),
            auth_kind=auth.kind,
            quality_warning=warning,
            yt_dlp_version=YT_DLP_VERSION,
        )


class BilibiliAdapter(YtDlpAdapter):
    def __init__(self, *, ydl_factory: YdlFactory | None = None):
        super().__init__(Platform.BILIBILI, ydl_factory=ydl_factory)


class YouTubeAdapter(YtDlpAdapter):
    def __init__(self, *, ydl_factory: YdlFactory | None = None):
        super().__init__(Platform.YOUTUBE, ydl_factory=ydl_factory)


class XAdapter(YtDlpAdapter):
    def __init__(self, *, ydl_factory: YdlFactory | None = None):
        super().__init__(Platform.X, ydl_factory=ydl_factory)


def select_exact_formats(
    formats: list[FormatInfo],
    policy: AcquisitionPolicy,
) -> tuple[list[FormatInfo], str, str | None]:
    videos = [item for item in formats if item.has_video and not item.has_drm]
    if not videos:
        raise SourceProbeError("no downloadable video format is available")

    warning: str | None = None
    maximum_height = policy.effective_max_height
    capped = [
        item
        for item in videos
        if maximum_height is None or (item.height is not None and item.height <= maximum_height)
    ]
    if capped:
        videos = capped
    elif maximum_height is not None:
        warning = f"no format is at or below the requested {maximum_height}p cap"

    video = max(
        videos,
        key=lambda item: _video_sort_key(
            item,
            prefer_playback_compatible=policy.effective_playback_preference,
        ),
    )
    selected = [video]
    if not video.has_audio:
        audio_candidates = [
            item for item in formats if item.has_audio and not item.has_video and not item.has_drm
        ]
        if audio_candidates:
            selected.append(max(audio_candidates, key=_audio_sort_key))
        else:
            warning = _join_warning(warning, "no separate audio format is available")

    if video.height is not None and video.height <= 480:
        warning = _join_warning(
            warning,
            f"highest selected video quality is only {video.height}p; "
            "small-text OCR may be limited",
        )
    expression = "+".join(item.format_id for item in selected)
    return selected, expression, warning


def format_selection_fingerprint(source_id: str, selected: list[FormatInfo]) -> str:
    return stable_fingerprint(
        {
            "source_id": source_id,
            "formats": [
                item.model_dump(mode="json", exclude_none=False)
                for item in sorted(selected, key=lambda value: value.format_id)
            ],
        }
    )


class _SafeYtDlpLogger:
    def __init__(
        self,
        platform: Platform,
        progress: ProgressSink | None,
        warnings: list[str],
    ):
        self._platform = platform
        self._progress = progress
        self._warnings = warnings

    def debug(self, message: object) -> None:
        del message

    def info(self, message: object) -> None:
        del message

    def warning(self, message: object) -> None:
        safe = redact_sensitive(message)
        self._warnings.append(safe)
        _emit(
            self._progress,
            ProgressEvent(
                kind=ProgressKind.WARNING,
                platform=self._platform,
                phase="yt_dlp",
                status="warning",
                message=safe,
            ),
        )

    def error(self, message: object) -> None:
        self.warning(message)


def _format_from_ytdlp(raw: Mapping[str, Any]) -> FormatInfo:
    return FormatInfo(
        format_id=str(raw.get("format_id") or "unknown"),
        ext=_optional_text(raw.get("ext")),
        protocol=_optional_text(raw.get("protocol")),
        width=_nonnegative_int(raw.get("width")),
        height=_nonnegative_int(raw.get("height")),
        fps=_nonnegative_float(raw.get("fps")),
        vcodec=_optional_text(raw.get("vcodec")),
        acodec=_optional_text(raw.get("acodec")),
        video_bitrate_kbps=_nonnegative_float(raw.get("vbr")),
        audio_bitrate_kbps=_nonnegative_float(raw.get("abr")),
        total_bitrate_kbps=_nonnegative_float(raw.get("tbr")),
        audio_sample_rate=_nonnegative_int(raw.get("asr")),
        filesize=_nonnegative_int(raw.get("filesize")),
        filesize_approx=_nonnegative_int(raw.get("filesize_approx")),
        language=_optional_text(raw.get("language")),
        format_note=_optional_text(raw.get("format_note")),
        dynamic_range=_optional_text(raw.get("dynamic_range")),
        has_drm=bool(raw.get("has_drm")),
    )


def _video_sort_key(
    item: FormatInfo,
    *,
    prefer_playback_compatible: bool,
) -> tuple[float, ...]:
    compatible = float(
        (item.ext or "").lower() in {"mp4", "m4v"}
        and (item.vcodec or "").lower().startswith(("avc", "h264"))
    )
    return (
        float(item.height or 0),
        float(item.width or 0),
        float(item.fps or 0),
        compatible if prefer_playback_compatible else 0.0,
        float(item.video_bitrate_kbps or item.total_bitrate_kbps or 0),
        float(item.filesize or item.filesize_approx or 0),
    )


def _audio_sort_key(item: FormatInfo) -> tuple[float, ...]:
    return (
        float(item.audio_bitrate_kbps or 0),
        float(item.audio_sample_rate or 0),
        float(item.total_bitrate_kbps or 0),
        float(item.filesize or item.filesize_approx or 0),
    )


def _find_media_path(
    info: Any,
    *,
    ydl: Any,
    destination: Path,
    hook_paths: list[str],
) -> Path:
    candidates = list(hook_paths)
    if isinstance(info, dict):
        for key in ("filepath", "_filename"):
            value = info.get(key)
            if isinstance(value, str):
                candidates.append(value)
        downloads = info.get("requested_downloads")
        if isinstance(downloads, list):
            for item in downloads:
                if isinstance(item, dict):
                    value = item.get("filepath")
                    if isinstance(value, str):
                        candidates.append(value)
        try:
            prepared = ydl.prepare_filename(info)
        except Exception:
            prepared = None
        if isinstance(prepared, str):
            candidates.append(prepared)

    for candidate in reversed(candidates):
        resolved = Path(candidate).expanduser().resolve()
        if (
            resolved.is_relative_to(destination)
            and resolved.is_file()
            and resolved.suffix.lower() not in _NON_MEDIA_SUFFIXES
        ):
            return resolved

    discovered = [
        item.resolve()
        for item in destination.iterdir()
        if item.is_file() and item.suffix.lower() not in _NON_MEDIA_SUFFIXES
    ]
    if not discovered:
        raise FileNotFoundError("yt-dlp completed but no media file was found")
    return max(discovered, key=lambda item: item.stat().st_mtime_ns)


def _find_subtitles(destination: Path, media_path: Path) -> list[Path]:
    return sorted(
        (
            item.resolve()
            for item in destination.iterdir()
            if item.is_file()
            and item.resolve() != media_path
            and item.suffix.lower() in _SUBTITLE_EXTENSIONS
        ),
        key=lambda item: item.name.casefold(),
    )


def _subtitle_summary(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[str]] = {}
    for language, tracks in raw.items():
        if not isinstance(language, str) or not isinstance(tracks, list):
            continue
        extensions = sorted(
            {
                extension
                for item in tracks
                if isinstance(item, dict)
                and (extension := _optional_text(item.get("ext"))) is not None
            }
        )
        result[language] = extensions
    return result


def _join_warning(current: str | None, addition: str) -> str:
    return addition if not current else f"{current}; {addition}"


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _nonnegative_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _emit(sink: ProgressSink | None, event: ProgressEvent) -> None:
    if sink is not None:
        sink(event)
