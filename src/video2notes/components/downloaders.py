"""Optional, injectable download adapters used by the component manager."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from .models import ComponentManifest, DownloadResult, DownloadSource


class ComponentDownloadError(RuntimeError):
    """A component source could not be materialized inside its staging directory."""


class ComponentDownloader(Protocol):
    def download(
        self,
        manifest: ComponentManifest,
        destination: Path,
        *,
        resume: bool,
    ) -> DownloadResult: ...


class _SnapshotDownload(Protocol):
    def __call__(self, **kwargs: object) -> str: ...


class HuggingFaceSnapshotDownloader:
    """Lazy huggingface_hub adapter; importing components never requires that package."""

    def download(
        self,
        manifest: ComponentManifest,
        destination: Path,
        *,
        resume: bool,
    ) -> DownloadResult:
        del resume  # snapshot_download resumes its managed local_dir/cache automatically.
        if manifest.source_kind is not DownloadSource.HUGGINGFACE_SNAPSHOT:
            raise ComponentDownloadError("manifest is not a Hugging Face snapshot")
        try:
            module = importlib.import_module("huggingface_hub")
        except ImportError as error:
            raise ComponentDownloadError(
                "huggingface_hub is unavailable in this portable runtime"
            ) from error
        raw_download = getattr(module, "snapshot_download", None)
        if not callable(raw_download):
            raise ComponentDownloadError("huggingface_hub has no snapshot_download function")
        snapshot_download = cast(_SnapshotDownload, raw_download)
        arguments: dict[str, object] = {
            "repo_id": manifest.source,
            "local_dir": str(destination),
            # Keep both payload and cache under app data. The manager rejects
            # any link that resolves outside this staging directory.
            "cache_dir": str(destination / ".hf-cache"),
            "local_dir_use_symlinks": False,
        }
        if manifest.revision is not None:
            arguments["revision"] = manifest.revision
        try:
            snapshot_download(**arguments)
        except Exception as error:
            raise ComponentDownloadError(
                f"Hugging Face snapshot failed: {type(error).__name__}"
            ) from None
        return DownloadResult(source_revision=manifest.revision)


PaddleCompatibilityResolver = Callable[
    [ComponentManifest, Path, bool],
    DownloadResult | None,
]


class PaddleCompatibleDownloader:
    """Adapter around a version-specific Paddle asset resolver supplied by the app build.

    Paddle's model distribution APIs vary between PaddleOCR/PaddleX versions and
    often default to a global user cache. Requiring an explicit resolver keeps
    every downloaded byte inside the manager-provided app-data staging path.
    """

    def __init__(self, resolver: PaddleCompatibilityResolver) -> None:
        self._resolver = resolver

    def download(
        self,
        manifest: ComponentManifest,
        destination: Path,
        *,
        resume: bool,
    ) -> DownloadResult:
        if manifest.source_kind is not DownloadSource.PADDLE_COMPATIBLE:
            raise ComponentDownloadError("manifest is not a Paddle-compatible bundle")
        try:
            result = self._resolver(manifest, destination, resume)
        except Exception as error:
            raise ComponentDownloadError(
                f"Paddle-compatible model preparation failed: {type(error).__name__}"
            ) from None
        return result or DownloadResult(source_revision=manifest.revision)
