"""Pinned archive download and extraction for isolated runtime packages."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .runtime_models import (
    RUNTIME_PACKAGE_MANIFEST,
    RuntimePackageManifest,
    RuntimePackageRelease,
    normalize_runtime_relative_path,
)

RuntimeDownloadProgress = Callable[[int, int | None], None]


class RuntimePackageDownloadError(RuntimeError):
    """A runtime release archive could not be downloaded safely."""


class RuntimePackageDownloadCancelled(RuntimePackageDownloadError):
    """The caller cancelled a resumable archive download."""


class RuntimePackageIntegrityError(RuntimePackageDownloadError):
    """A package archive or extracted payload failed its pinned integrity contract."""


class RuntimePackageArchiveError(RuntimePackageDownloadError):
    """A package archive is unsafe or structurally invalid."""


@dataclass(frozen=True, slots=True)
class RuntimeDownloadResult:
    archive_path: Path
    downloaded_bytes: int
    resumed: bool
    reused: bool


@dataclass(frozen=True, slots=True)
class RuntimePackageValidation:
    manifest: RuntimePackageManifest
    manifest_sha256: str
    payload_size_bytes: int


class RuntimePackageDownloader(Protocol):
    def download(
        self,
        release: RuntimePackageRelease,
        destination: Path,
        *,
        cancel_event: threading.Event,
        progress: RuntimeDownloadProgress,
    ) -> RuntimeDownloadResult: ...


class UrlRuntimePackageDownloader:
    """Download a trusted HTTPS/HTTP/file release with resumable partial files."""

    def __init__(self, *, chunk_size: int = 1024 * 1024, timeout_seconds: float = 60.0):
        if chunk_size < 64 * 1024:
            raise ValueError("runtime archive download chunks must be at least 64 KiB")
        self.chunk_size = chunk_size
        self.timeout_seconds = timeout_seconds

    def download(
        self,
        release: RuntimePackageRelease,
        destination: Path,
        *,
        cancel_event: threading.Event,
        progress: RuntimeDownloadProgress,
    ) -> RuntimeDownloadResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            if _archive_matches(destination, release):
                progress(release.archive_size_bytes, release.archive_size_bytes)
                return RuntimeDownloadResult(
                    archive_path=destination,
                    downloaded_bytes=release.archive_size_bytes,
                    resumed=False,
                    reused=True,
                )
            destination.unlink()

        partial = destination.with_name(f"{destination.name}.part")
        if partial.is_file() and _archive_matches(partial, release):
            os.replace(partial, destination)
            progress(release.archive_size_bytes, release.archive_size_bytes)
            return RuntimeDownloadResult(
                archive_path=destination,
                downloaded_bytes=release.archive_size_bytes,
                resumed=True,
                reused=True,
            )
        if partial.is_file() and partial.stat().st_size > release.archive_size_bytes:
            partial.unlink()
        if release.archive_url is None:
            raise RuntimePackageDownloadError(
                "offline-only runtime archive is not present in the managed download cache"
            )
        parsed = urllib.parse.urlparse(release.archive_url)
        try:
            if parsed.scheme == "file":
                resumed = self._copy_local(
                    release,
                    parsed,
                    partial,
                    cancel_event=cancel_event,
                    progress=progress,
                )
            elif parsed.scheme in {"https", "http"}:
                resumed = self._download_http(
                    release,
                    partial,
                    cancel_event=cancel_event,
                    progress=progress,
                )
            else:
                raise RuntimePackageDownloadError(
                    "runtime archive URLs must use HTTPS, HTTP, or file"
                )
        except RuntimePackageDownloadError:
            raise
        except (OSError, urllib.error.URLError) as error:
            raise RuntimePackageDownloadError(
                f"runtime archive transfer failed: {type(error).__name__}"
            ) from None

        if cancel_event.is_set():
            raise RuntimePackageDownloadCancelled("runtime archive download was cancelled")
        if not _archive_matches(partial, release):
            partial.unlink(missing_ok=True)
            raise RuntimePackageIntegrityError(
                "runtime archive size or SHA-256 does not match the trusted release"
            )
        os.replace(partial, destination)
        return RuntimeDownloadResult(
            archive_path=destination,
            downloaded_bytes=release.archive_size_bytes,
            resumed=resumed,
            reused=False,
        )

    def _copy_local(
        self,
        release: RuntimePackageRelease,
        parsed: urllib.parse.ParseResult,
        partial: Path,
        *,
        cancel_event: threading.Event,
        progress: RuntimeDownloadProgress,
    ) -> bool:
        raw_path = urllib.request.url2pathname(parsed.path)
        if parsed.netloc:
            raw_path = f"//{parsed.netloc}{raw_path}"
        source = Path(raw_path).expanduser().resolve()
        if not source.is_file():
            raise RuntimePackageDownloadError("local runtime archive does not exist")
        source_size = source.stat().st_size
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset > source_size:
            partial.unlink(missing_ok=True)
            offset = 0
        resumed = offset > 0
        with source.open("rb") as input_stream, partial.open("ab" if resumed else "wb") as output:
            input_stream.seek(offset)
            transferred = offset
            progress(transferred, release.archive_size_bytes)
            while True:
                if cancel_event.is_set():
                    raise RuntimePackageDownloadCancelled(
                        "runtime archive download was cancelled"
                    )
                chunk = input_stream.read(self.chunk_size)
                if not chunk:
                    break
                output.write(chunk)
                transferred += len(chunk)
                progress(transferred, release.archive_size_bytes)
        return resumed

    def _download_http(
        self,
        release: RuntimePackageRelease,
        partial: Path,
        *,
        cancel_event: threading.Event,
        progress: RuntimeDownloadProgress,
    ) -> bool:
        offset = partial.stat().st_size if partial.is_file() else 0
        headers = {"Accept-Encoding": "identity", "User-Agent": "Video2Notes/runtime-packager"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        if release.archive_url is None:  # pragma: no cover - guarded by download()
            raise RuntimePackageDownloadError("runtime archive has no source URL")
        request = urllib.request.Request(release.archive_url, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            status = getattr(response, "status", response.getcode())
            resumed = offset > 0 and status == 206
            if offset and not resumed:
                offset = 0
            mode = "ab" if resumed else "wb"
            transferred = offset
            progress(transferred, release.archive_size_bytes)
            with partial.open(mode) as output:
                while True:
                    if cancel_event.is_set():
                        raise RuntimePackageDownloadCancelled(
                            "runtime archive download was cancelled"
                        )
                    chunk = response.read(self.chunk_size)
                    if not chunk:
                        break
                    output.write(chunk)
                    transferred += len(chunk)
                    progress(transferred, release.archive_size_bytes)
        return resumed


def safe_extract_runtime_archive(
    archive: Path,
    destination: Path,
    *,
    max_members: int = 20_000,
    max_uncompressed_bytes: int = 16 * 1024**3,
    max_compression_ratio: int = 2_000,
) -> tuple[Path, ...]:
    """Extract regular files only, after validating every member up front."""

    if destination.exists():
        if _is_link(destination) or not destination.is_dir() or any(destination.iterdir()):
            raise RuntimePackageArchiveError("runtime extraction destination must be empty")
    else:
        destination.mkdir(parents=True)
    destination = destination.resolve()

    try:
        package = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimePackageArchiveError(
            f"runtime archive is not a valid zip: {type(error).__name__}"
        ) from None

    with package:
        members = package.infolist()
        if not members or len(members) > max_members:
            raise RuntimePackageArchiveError("runtime archive member count is invalid")

        prepared: list[tuple[zipfile.ZipInfo, str, bool]] = []
        seen: set[str] = set()
        total_size = 0
        has_manifest = False
        for member in members:
            raw_name = member.filename.replace("\\", "/")
            is_directory = member.is_dir() or raw_name.endswith("/")
            candidate = raw_name.rstrip("/") if is_directory else raw_name
            try:
                relative = normalize_runtime_relative_path(candidate)
            except ValueError as error:
                raise RuntimePackageArchiveError(str(error)) from None
            folded = relative.casefold()
            if folded in seen:
                raise RuntimePackageArchiveError(
                    "runtime archive contains duplicate case-insensitive paths"
                )
            seen.add(folded)
            has_manifest = has_manifest or relative == RUNTIME_PACKAGE_MANIFEST

            mode = (member.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            allowed_type = stat.S_IFDIR if is_directory else stat.S_IFREG
            if file_type not in {0, allowed_type} or stat.S_ISLNK(mode):
                raise RuntimePackageArchiveError(
                    "runtime archive may contain only regular files and directories"
                )
            if member.flag_bits & 0x1:
                raise RuntimePackageArchiveError("encrypted runtime archives are unsupported")
            total_size += member.file_size
            if total_size > max_uncompressed_bytes:
                raise RuntimePackageArchiveError("runtime archive expands beyond the size limit")
            if (
                member.file_size > 1024 * 1024
                and member.compress_size == 0
                and not is_directory
            ):
                raise RuntimePackageArchiveError("runtime archive has an invalid compression size")
            if (
                member.compress_size > 0
                and member.file_size / member.compress_size > max_compression_ratio
            ):
                raise RuntimePackageArchiveError("runtime archive compression ratio is unsafe")
            prepared.append((member, relative, is_directory))

        if not has_manifest:
            raise RuntimePackageArchiveError("runtime archive has no self-description manifest")

        extracted: list[Path] = []
        written = 0
        for member, relative, is_directory in prepared:
            target = (destination / Path(relative.replace("/", os.sep))).resolve()
            if not target.is_relative_to(destination):
                raise RuntimePackageArchiveError("runtime archive path escaped its destination")
            if is_directory:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if _is_link(target.parent):
                raise RuntimePackageArchiveError("runtime archive parent cannot be a link")
            with package.open(member) as source, target.open("xb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_uncompressed_bytes:
                        raise RuntimePackageArchiveError(
                            "runtime archive exceeded its extraction limit"
                        )
                    output.write(chunk)
            if target.stat().st_size != member.file_size:
                raise RuntimePackageArchiveError("runtime archive member size changed")
            extracted.append(target)
        return tuple(extracted)


def validate_runtime_package_root(
    root: Path,
    *,
    expected_manifest: RuntimePackageManifest | None = None,
    full_hash: bool = True,
) -> RuntimePackageValidation:
    """Validate a package root without importing or executing any package code."""

    resolved = root.expanduser().resolve()
    if not resolved.is_dir() or _is_link(root):
        raise RuntimePackageIntegrityError("runtime package root is not a regular directory")
    manifest_path = resolved / RUNTIME_PACKAGE_MANIFEST
    if not manifest_path.is_file() or _is_link(manifest_path):
        raise RuntimePackageIntegrityError("runtime package manifest is missing")
    try:
        manifest = RuntimePackageManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise RuntimePackageIntegrityError(
            f"runtime package manifest is invalid: {type(error).__name__}"
        ) from None
    if expected_manifest is not None and manifest != expected_manifest:
        raise RuntimePackageIntegrityError(
            "embedded runtime manifest does not match the trusted catalog"
        )

    payload_size = 0
    for item in manifest.payload:
        path = (resolved / Path(item.path.replace("/", os.sep))).resolve()
        if not path.is_relative_to(resolved) or not path.is_file() or _is_link(path):
            raise RuntimePackageIntegrityError(f"runtime payload is missing: {item.path}")
        actual_size = path.stat().st_size
        if actual_size != item.size_bytes:
            raise RuntimePackageIntegrityError(f"runtime payload size changed: {item.path}")
        if full_hash and _sha256_file(path) != item.sha256:
            raise RuntimePackageIntegrityError(f"runtime payload hash changed: {item.path}")
        payload_size += actual_size
    return RuntimePackageValidation(
        manifest=manifest,
        manifest_sha256=runtime_manifest_sha256(manifest),
        payload_size_bytes=payload_size,
    )


def validate_runtime_release_root(
    root: Path,
    release: RuntimePackageRelease,
) -> RuntimePackageValidation:
    """Fully validate the exact installed tree pinned by a catalog entry."""

    validation = validate_runtime_package_root(
        root,
        expected_manifest=release.manifest,
        full_hash=True,
    )
    resolved = root.expanduser().resolve()
    expected_paths = {item.relative_path.casefold() for item in release.files}
    installed_size = 0
    for item in release.files:
        path = (resolved / Path(item.relative_path.replace("/", os.sep))).resolve()
        if not path.is_relative_to(resolved) or not path.is_file() or _is_link(path):
            raise RuntimePackageIntegrityError(
                f"runtime catalog file is missing: {item.relative_path}"
            )
        if path.stat().st_size != item.size_bytes or _sha256_file(path) != item.sha256:
            raise RuntimePackageIntegrityError(
                f"runtime catalog file identity changed: {item.relative_path}"
            )
        installed_size += item.size_bytes
    actual_paths = {
        path.relative_to(resolved).as_posix().casefold()
        for path in resolved.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise RuntimePackageIntegrityError(
            "runtime package contains files outside its trusted catalog"
        )
    if installed_size != release.installed_size_bytes:
        raise RuntimePackageIntegrityError("runtime installed size changed")
    return validation


def runtime_manifest_sha256(manifest: RuntimePackageManifest) -> str:
    canonical = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _archive_matches(path: Path, release: RuntimePackageRelease) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == release.archive_size_bytes
        and _sha256_file(path) == release.archive_sha256
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False
