"""First-class local-file source adapter."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from video2notes.artifacts import sha256_file
from video2notes.domain import MediaManifest
from video2notes.media import probe_media
from video2notes.sources.errors import CancellationToken, QualityChangedError
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

ProgressSink = Callable[[ProgressEvent], None]
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class LocalFileAdapter:
    platform = Platform.LOCAL

    def __init__(
        self,
        *,
        probe_fn: Callable[[str | Path], MediaManifest] = probe_media,
    ):
        self._probe_fn = probe_fn

    def matches(self, source: SourceInput) -> bool:
        return source.kind is SourceKind.LOCAL

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
        if not self.matches(source):
            raise ValueError("LocalFileAdapter requires a local SourceInput")
        if auth.kind is not AuthKind.NONE:
            raise ValueError("local files do not accept platform authentication")
        cancel.raise_if_cancelled()
        _emit(
            progress,
            ProgressEvent(
                kind=ProgressKind.PROBE,
                platform=self.platform,
                phase="ffprobe",
                status="started",
            ),
        )
        source_path = Path(source.value).expanduser().resolve(strict=True)
        if not source_path.is_file():
            raise FileNotFoundError(f"local source is not a regular file: {source_path}")
        media = self._probe_fn(source_path)
        cancel.raise_if_cancelled()
        source_hash = media.source_sha256
        formats = _formats_from_media(media, source_path)
        selected_ids = [item.format_id for item in formats]
        quality_fingerprint = _quality_fingerprint(source_hash, formats)
        manifest = SourceManifest(
            platform=self.platform,
            source=SourceInput.local(source_path),
            source_id=source_hash,
            extractor_key="local",
            title=source_path.stem,
            duration_seconds=media.duration_us / 1_000_000,
            formats=formats,
            selected_format_ids=selected_ids,
            selected_format_expression="+".join(selected_ids),
            selected_quality_fingerprint=quality_fingerprint,
            acquisition_policy_fingerprint=policy.fingerprint(),
            auth_kind=AuthKind.NONE,
        )
        _emit(
            progress,
            ProgressEvent(
                kind=ProgressKind.PROBE,
                platform=self.platform,
                phase="ffprobe",
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
        if manifest.platform is not Platform.LOCAL:
            raise ValueError("manifest does not belong to LocalFileAdapter")
        if auth.kind is not AuthKind.NONE:
            raise ValueError("local files do not accept platform authentication")
        cancel.raise_if_cancelled()

        source_path = Path(manifest.source.value).expanduser().resolve(strict=True)
        current_hash = sha256_file(source_path)
        if current_hash != manifest.source_id:
            raise QualityChangedError("local source changed after it was probed")

        destination = destination.expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        safe_stem = _SAFE_NAME.sub("-", source_path.stem).strip("-.") or "local-media"
        target = destination / f"{safe_stem}-{current_hash[:12]}{source_path.suffix.lower()}"
        if target.resolve() == source_path:
            raise ValueError("local import destination must not be the original file")

        if target.is_file() and sha256_file(target) == current_hash:
            operation = "cached"
        elif policy.prefer_hardlink and _try_hardlink(source_path, target):
            operation = "hardlink"
        else:
            operation = "copy"
            _copy_resumable(source_path, target, progress=progress, cancel=cancel)

        cancel.raise_if_cancelled()
        selected = manifest.selected_formats
        video = next((item for item in selected if item.has_video), None)
        audio = next((item for item in selected if item.has_audio), None)
        _emit(
            progress,
            ProgressEvent(
                kind=ProgressKind.COMPLETED,
                platform=self.platform,
                phase=operation,
                status="finished",
                downloaded_bytes=target.stat().st_size,
                total_bytes=target.stat().st_size,
            ),
        )
        return AcquisitionResult(
            platform=self.platform,
            source_id=manifest.source_id,
            media_path=str(target),
            actual_format_ids=manifest.selected_format_ids,
            actual_width=video.width if video else None,
            actual_height=video.height if video else None,
            actual_fps=video.fps if video else None,
            actual_vcodec=video.vcodec if video else None,
            actual_acodec=(
                audio.acodec
                if audio is not None
                else video.acodec
                if video is not None
                else None
            ),
            source_sha256=current_hash,
            quality_fingerprint=manifest.selected_quality_fingerprint or current_hash,
        )


def _formats_from_media(media: MediaManifest, source_path: Path) -> list[FormatInfo]:
    video = media.video_stream
    audio = media.audio_stream
    frame_rate = None
    if video is not None:
        rate = video.avg_frame_rate or video.real_frame_rate
        if rate is not None:
            frame_rate = float(rate.fraction)
    return [
        FormatInfo(
            format_id="local-file",
            ext=source_path.suffix.lstrip(".").lower() or None,
            protocol="file",
            width=video.width if video else None,
            height=video.height if video else None,
            fps=frame_rate,
            vcodec=video.codec_name if video else None,
            acodec=audio.codec_name if audio else None,
            filesize=media.file_size,
        )
    ]


def _quality_fingerprint(source_hash: str, formats: list[FormatInfo]) -> str:
    return stable_fingerprint(
        {
            "source_sha256": source_hash,
            "formats": [item.model_dump(mode="json") for item in formats],
        }
    )


def _try_hardlink(source: Path, target: Path) -> bool:
    temporary = target.with_suffix(f"{target.suffix}.link-part")
    try:
        temporary.unlink(missing_ok=True)
        os.link(source, temporary)
        os.replace(temporary, target)
        return True
    except OSError:
        temporary.unlink(missing_ok=True)
        return False


def _copy_resumable(
    source: Path,
    target: Path,
    *,
    progress: ProgressSink | None,
    cancel: CancellationToken,
    chunk_size: int = 4 * 1024 * 1024,
) -> None:
    partial = target.with_suffix(f"{target.suffix}.part")
    total = source.stat().st_size
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > total:
        partial.unlink()
        offset = 0
    with source.open("rb") as reader, partial.open("ab" if offset else "wb") as writer:
        reader.seek(offset)
        copied = offset
        while True:
            cancel.raise_if_cancelled()
            chunk = reader.read(chunk_size)
            if not chunk:
                break
            writer.write(chunk)
            copied += len(chunk)
            _emit(
                progress,
                ProgressEvent(
                    kind=ProgressKind.DOWNLOAD,
                    platform=Platform.LOCAL,
                    phase="copy",
                    status="downloading",
                    downloaded_bytes=copied,
                    total_bytes=total,
                ),
            )
        writer.flush()
        os.fsync(writer.fileno())
    shutil.copystat(source, partial)
    os.replace(partial, target)


def _emit(sink: ProgressSink | None, event: ProgressEvent) -> None:
    if sink is not None:
        sink(event)
