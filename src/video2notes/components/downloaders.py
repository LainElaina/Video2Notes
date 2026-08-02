"""Optional, injectable download adapters used by the component manager."""

from __future__ import annotations

import importlib
import re
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

    def __init__(self, snapshot_download: _SnapshotDownload | None = None) -> None:
        self._snapshot_download = snapshot_download

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
        snapshot_download = self._snapshot_download or _load_snapshot_download()
        arguments: dict[str, object] = {
            "repo_id": manifest.source,
            "local_dir": str(destination),
            # Catalog assets are public and pinned.  Never read or forward a
            # user's global Hugging Face credential while preparing them.
            "token": False,
            # New huggingface_hub releases use a small local-dir metadata cache;
            # older releases use this explicit cache. Both remain below app data.
            "cache_dir": str(destination / ".hf-cache"),
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


_PADDLE_SOURCE = re.compile(
    r"^paddleocr://"
    r"(?P<det>[A-Za-z0-9._-]+)@(?P<det_revision>[0-9a-f]{40})\+"
    r"(?P<rec>[A-Za-z0-9._-]+)@(?P<rec_revision>[0-9a-f]{40})$"
)


class PaddleHuggingFaceDownloader:
    """Download the catalog-pinned Paddle detector and recognizer snapshots."""

    def __init__(self, snapshot_download: _SnapshotDownload | None = None) -> None:
        self._snapshot_download = snapshot_download

    def download(
        self,
        manifest: ComponentManifest,
        destination: Path,
        *,
        resume: bool,
    ) -> DownloadResult:
        del resume  # local_dir snapshot downloads resume their partial files.
        if manifest.source_kind is not DownloadSource.PADDLE_COMPATIBLE:
            raise ComponentDownloadError("manifest is not a Paddle-compatible bundle")
        match = _PADDLE_SOURCE.fullmatch(manifest.source)
        if match is None:
            raise ComponentDownloadError("Paddle component source is invalid or unpinned")
        snapshot_download = self._snapshot_download or _load_snapshot_download()
        for role, model_key, revision_key in (
            ("detection", "det", "det_revision"),
            ("recognition", "rec", "rec_revision"),
        ):
            model_name = match.group(model_key)
            revision = match.group(revision_key)
            local_dir = destination / role
            arguments: dict[str, object] = {
                "repo_id": f"PaddlePaddle/{model_name}",
                "revision": revision,
                "local_dir": str(local_dir),
                "cache_dir": str(local_dir / ".hf-cache"),
                "token": False,
            }
            try:
                snapshot_download(**arguments)
            except Exception as error:
                raise ComponentDownloadError(
                    f"Paddle model snapshot failed: {type(error).__name__}"
                ) from None
        return DownloadResult(
            source_revision=manifest.source.removeprefix("paddleocr://")
        )


def _load_snapshot_download() -> _SnapshotDownload:
    try:
        module = importlib.import_module("huggingface_hub")
    except ImportError as error:
        raise ComponentDownloadError(
            "huggingface_hub is unavailable in this portable runtime"
        ) from error
    raw_download = getattr(module, "snapshot_download", None)
    if not callable(raw_download):
        raise ComponentDownloadError("huggingface_hub has no snapshot_download function")
    return cast(_SnapshotDownload, raw_download)


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
