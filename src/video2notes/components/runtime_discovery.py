"""Discover trusted catalogs and synthesize the current bundled app runtime."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import shutil
import sys
from pathlib import Path

from video2notes import __version__

from .runtime_catalog import (
    DEFAULT_RUNTIME_PACKAGE_CATALOG,
    RuntimePackageCatalog,
    load_runtime_package_catalog,
)
from .runtime_models import (
    RuntimeArchiveSpec,
    RuntimeCapabilitySpec,
    RuntimeLicenseSpec,
    RuntimePackageCandidate,
    RuntimePackageManifest,
    RuntimePackageRelease,
    RuntimePackageSource,
    RuntimePayloadFile,
    RuntimeTransport,
)


def load_packaged_runtime_catalog(runtime_root: str | Path) -> RuntimePackageCatalog:
    """Merge shipped online metadata with portable offline archives, if present."""

    root = Path(runtime_root).expanduser().resolve()
    candidates = (
        root / "runtime-packs",
        root.parent / "runtime-packs",
    )
    catalog = DEFAULT_RUNTIME_PACKAGE_CATALOG
    seen: set[Path] = set()
    for directory in candidates:
        resolved = directory.resolve()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        trusted = resolved / "catalog.json"
        if trusted.is_file():
            catalog = catalog.merge(load_runtime_package_catalog(trusted))
        offline = resolved / "offline-catalog.json"
        if offline.is_file():
            loaded = load_runtime_package_catalog(offline)
            catalog = catalog.merge(_attach_offline_archives(loaded, resolved / "offline"))
    return catalog


def build_current_runtime_candidate(
    runtime_root: str | Path,
    *,
    ffmpeg_path: str | Path | None = None,
    ffprobe_path: str | Path | None = None,
    pdf_browser_path: str | Path | None = None,
) -> RuntimePackageCandidate | None:
    """Describe capabilities already shipped in the current app process.

    This is a compatibility bridge for the existing full portable and the
    editable development environment. It never makes those files removable.
    """

    requested_root = Path(runtime_root).expanduser().resolve()
    root = requested_root if requested_root.is_dir() else Path(sys.executable).resolve().parent
    marker = _runtime_marker(root)
    if marker is None:
        return None
    license_path = _runtime_license(root, marker)
    payload_paths = tuple(dict.fromkeys((marker, license_path)))
    payload = tuple(_payload_file(root, path) for path in payload_paths)

    backend_components = _backend_components(root)
    capabilities: list[RuntimeCapabilitySpec] = []
    if _component_available(backend_components, "yt-dlp", module="yt_dlp"):
        capabilities.append(_in_process_capability("download.ytdlp", "yt-dlp"))
    resolved_ffmpeg = _resolve_binary(ffmpeg_path, "ffmpeg")
    resolved_ffprobe = _resolve_binary(ffprobe_path, "ffprobe")
    if resolved_ffmpeg is not None:
        capabilities.append(_in_process_capability("tool.ffmpeg", "ffmpeg"))
    if resolved_ffprobe is not None:
        capabilities.append(_in_process_capability("tool.ffprobe", "ffprobe"))
    if _component_available(
        backend_components,
        "faster-whisper",
        module="faster_whisper",
    ) and _component_available(backend_components, "ctranslate2", module="ctranslate2"):
        capabilities.append(
            _in_process_capability(
                "asr.faster_whisper",
                "faster-whisper",
                devices=("cpu", "cuda") if _has_nvidia_asr(backend_components) else ("cpu",),
            )
        )
    if _component_available(backend_components, "paddleocr", module="paddleocr") and (
        _component_available(backend_components, "paddlepaddle", module="paddle")
    ):
        capabilities.append(
            _in_process_capability(
                "ocr.paddleocr",
                "paddleocr",
                devices=("cpu", "cuda") if _has_nvidia_ocr(backend_components) else ("cpu",),
            )
        )
    if _resolve_binary(pdf_browser_path, "msedge", "chrome", "chromium") is not None:
        capabilities.append(_in_process_capability("render.chromium_pdf", "chromium"))
    if not capabilities:
        return None

    fingerprint = hashlib.sha256(
        "|".join(f"{item.relative_path}:{item.sha256}" for item in payload).encode("utf-8")
    ).hexdigest()[:12]
    manifest = RuntimePackageManifest(
        package_id="video2notes-current-runtime",
        version=f"{__version__}-{fingerprint}",
        display_name="Current Video2Notes runtime",
        target_triple=_target_triple(),
        runtime_protocol_version=1,
        capabilities=tuple(capabilities),
        licenses=(
            RuntimeLicenseSpec(
                name="Current runtime licensing marker",
                relative_path=license_path.relative_to(root).as_posix(),
            ),
        ),
        upstream_sources=("https://github.com/yt-dlp/yt-dlp",),
        payload_size_bytes=sum(item.size_bytes for item in payload),
        user_model_weights_included=False,
        files=payload,
    )
    return RuntimePackageCandidate(
        source=RuntimePackageSource.BUNDLED,
        root=str(root),
        manifest=manifest,
    )


def _attach_offline_archives(
    catalog: RuntimePackageCatalog,
    archive_root: Path,
) -> RuntimePackageCatalog:
    releases: list[RuntimePackageRelease] = []
    for release in catalog.releases:
        archive_path = (archive_root / release.archive.file_name).resolve()
        if not archive_path.is_relative_to(archive_root.resolve()) or not archive_path.is_file():
            continue
        archive = RuntimeArchiveSpec(
            file_name=release.archive.file_name,
            source_url=archive_path.as_uri(),
            size_bytes=release.archive.size_bytes,
            sha256=release.archive.sha256,
            offline_only=True,
        )
        releases.append(release.model_copy(update={"archive": archive}))
    return RuntimePackageCatalog(
        catalog_id=catalog.catalog_id,
        target_triple=catalog.target_triple,
        runtime_protocol_version=catalog.runtime_protocol_version,
        release_profile=catalog.release_profile,
        packages=tuple(releases),
    )


def _runtime_marker(root: Path) -> Path | None:
    candidates = [
        root / "video2notes.exe",
        Path(sys.executable).resolve(),
    ]
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve().is_relative_to(root):
            return candidate.resolve()
    return None


def _runtime_license(root: Path, fallback: Path) -> Path:
    candidates = (
        root / "tools" / "FFMPEG_LICENSE.txt",
        root / "LICENSE.txt",
        root / "LICENSE",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    for candidate in root.glob("Lib/site-packages/*.dist-info/licenses/LICENSE*"):
        if candidate.is_file():
            return candidate.resolve()
    return fallback


def _payload_file(root: Path, path: Path) -> RuntimePayloadFile:
    resolved = path.resolve()
    relative = resolved.relative_to(root).as_posix()
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return RuntimePayloadFile(
        relative_path=relative,
        size_bytes=resolved.stat().st_size,
        sha256=digest.hexdigest(),
    )


def _backend_components(root: Path) -> dict[str, dict[str, object]]:
    path = root / "manifest.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    components = payload.get("components") if isinstance(payload, dict) else None
    if not isinstance(components, list):
        return {}
    return {
        str(item.get("id")): item
        for item in components
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _component_available(
    components: dict[str, dict[str, object]],
    component_id: str,
    *,
    module: str,
) -> bool:
    packaged = components.get(component_id)
    if packaged is not None:
        return packaged.get("included") is True and packaged.get("status") == "bundled"
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _has_nvidia_asr(components: dict[str, dict[str, object]]) -> bool:
    return all(
        _component_available(components, item, module=module)
        for item, module in (
            ("nvidia-cublas-cu12", "nvidia.cublas"),
            ("nvidia-cudnn-cu12", "nvidia.cudnn"),
        )
    )


def _has_nvidia_ocr(components: dict[str, dict[str, object]]) -> bool:
    paddle = components.get("paddlepaddle")
    if paddle is not None and paddle.get("distribution") != "paddlepaddle-gpu":
        return False
    return all(
        _component_available(components, item, module=module)
        for item, module in (
            ("nvidia-cuda-runtime-cu12", "nvidia.cuda_runtime"),
            ("nvidia-cufft-cu12", "nvidia.cufft"),
            ("nvidia-cusparse-cu12", "nvidia.cusparse"),
        )
    )


def _in_process_capability(
    capability_id: str,
    engine_id: str,
    *,
    devices: tuple[str, ...] = ("cpu",),
) -> RuntimeCapabilitySpec:
    return RuntimeCapabilitySpec(
        capability_id=capability_id,
        engine_id=engine_id,
        protocol_version=1,
        transport=RuntimeTransport.IN_PROCESS,
        supported_devices=devices,
    )


def _resolve_binary(
    explicit: str | Path | None,
    *names: str,
) -> Path | None:
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    for name in names:
        located = shutil.which(name)
        if located:
            return Path(located).resolve()
    return None


def _target_triple() -> str:
    architecture = platform.machine().casefold()
    machine = "x86_64" if architecture in {"amd64", "x64", "x86_64"} else architecture
    system = platform.system().casefold()
    suffix = {
        "windows": "pc-windows-msvc",
        "linux": "unknown-linux-gnu",
        "darwin": "apple-darwin",
    }.get(system, f"unknown-{system}")
    return f"{machine}-{suffix}"
