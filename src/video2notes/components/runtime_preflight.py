"""Request-scoped runtime requirements and install recommendations."""

from __future__ import annotations

import importlib.util
import shutil
from collections.abc import Mapping
from pathlib import Path

from video2notes.domain import ProcessingScope
from video2notes.notes import OutputFormat
from video2notes.pipeline import PipelineRequest, PipelineRuntime
from video2notes.sources import SourceKind, SourceManifest, SourceRegistry

from .runtime_manager import RuntimePackageBindingError, RuntimePackageManager
from .runtime_models import (
    FeatureAvailabilityState,
    RuntimeBindingSnapshot,
    RuntimeInstallRecommendation,
    RuntimePackageRelease,
    RuntimePreflightResult,
    RuntimeRequirementStatus,
)

TOOL_FFMPEG = "tool.ffmpeg"
TOOL_FFPROBE = "tool.ffprobe"
DOWNLOAD_YTDLP = "download.ytdlp"
ASR_FASTER_WHISPER = "asr.faster_whisper"
OCR_PADDLEOCR = "ocr.paddleocr"
RENDER_CHROMIUM_PDF = "render.chromium_pdf"


async def build_runtime_preflight(
    manager: RuntimePackageManager,
    request: PipelineRequest,
    *,
    source_registry: SourceRegistry,
    fallback_runtime: PipelineRuntime | None = None,
    prefer_cuda: bool = False,
) -> RuntimePreflightResult:
    """Resolve only the capabilities this exact task will execute."""

    fallback = _fallback_capabilities(fallback_runtime)
    requirements: list[RuntimeRequirementStatus] = []
    snapshots: dict[str, RuntimeBindingSnapshot] = {}
    installable_missing: set[str] = set()

    def add(
        requirement_id: str,
        *,
        required: bool,
        configured: bool = True,
    ) -> bool:
        try:
            instance = manager.select(requirement_id)
        except RuntimePackageBindingError:
            available = requirement_id in fallback and configured
            if not available and requirement_id not in fallback:
                installable_missing.add(requirement_id)
            requirements.append(
                RuntimeRequirementStatus(
                    requirement_id=requirement_id,
                    capability_id=requirement_id,
                    required=required,
                    state=(
                        FeatureAvailabilityState.READY
                        if available
                        else (
                            FeatureAvailabilityState.BLOCKED
                            if required
                            else FeatureAvailabilityState.DEGRADED
                        )
                    ),
                    detail=(
                        "The injected or current process runtime provides this capability."
                        if available
                        else "No compatible runtime package currently provides this capability."
                    ),
                )
            )
            return available
        if not configured:
            requirements.append(
                RuntimeRequirementStatus(
                    requirement_id=requirement_id,
                    capability_id=requirement_id,
                    required=required,
                    state=(
                        FeatureAvailabilityState.BLOCKED
                        if required
                        else FeatureAvailabilityState.DEGRADED
                    ),
                    selected_instance_id=instance.instance_id,
                    selected_source=instance.source,
                    detail=(
                        "The runtime is installed, but the local model weights "
                        "or adapter settings are not activated."
                    ),
                )
            )
            return False
        if instance.manifest_sha256 is None:
            requirements.append(
                RuntimeRequirementStatus(
                    requirement_id=requirement_id,
                    capability_id=requirement_id,
                    required=required,
                    state=(
                        FeatureAvailabilityState.BLOCKED
                        if required
                        else FeatureAvailabilityState.DEGRADED
                    ),
                    detail="The selected runtime package has no stable manifest identity.",
                )
            )
            return False
        snapshots[requirement_id] = RuntimeBindingSnapshot(
            requirement_id=requirement_id,
            capability_id=requirement_id,
            instance_id=instance.instance_id,
            source=instance.source,
            manifest_sha256=instance.manifest_sha256,
        )
        requirements.append(
            RuntimeRequirementStatus(
                requirement_id=requirement_id,
                capability_id=requirement_id,
                required=required,
                state=FeatureAvailabilityState.READY,
                selected_instance_id=instance.instance_id,
                selected_source=instance.source,
                detail="A compatible runtime package is ready.",
            )
        )
        return True

    ffmpeg_ready = add(TOOL_FFMPEG, required=True)
    ffprobe_ready = add(TOOL_FFPROBE, required=True)
    download_ready = True
    if request.source.kind is SourceKind.URL:
        download_ready = add(DOWNLOAD_YTDLP, required=True)

    asr_configured = fallback_runtime is not None and fallback_runtime.asr_backend is not None
    asr_ready = (
        _capability_available(manager, ASR_FASTER_WHISPER, fallback)
        and asr_configured
    )
    captions_available = False
    if not asr_ready and request.source.kind is SourceKind.URL and download_ready:
        captions_available = await _source_has_captions(request, source_registry)
    add(
        ASR_FASTER_WHISPER,
        required=not captions_available,
        configured=asr_configured,
    )

    if request.processing_scope is ProcessingScope.AUDIO_VISUAL:
        add(
            OCR_PADDLEOCR,
            required=False,
            configured=(
                fallback_runtime is not None and fallback_runtime.ocr_backend is not None
            ),
        )

    output_formats = request.effective_report_spec().resolve().output_formats
    if OutputFormat.PDF in output_formats:
        add(RENDER_CHROMIUM_PDF, required=True)

    del ffmpeg_ready, ffprobe_ready
    missing_required = tuple(
        item.requirement_id
        for item in requirements
        if item.required and item.state is not FeatureAvailabilityState.READY
    )
    missing_optional = tuple(
        item.requirement_id
        for item in requirements
        if not item.required and item.state is not FeatureAvailabilityState.READY
    )
    if missing_required:
        state = FeatureAvailabilityState.BLOCKED
    elif missing_optional:
        state = FeatureAvailabilityState.DEGRADED
    else:
        state = FeatureAvailabilityState.READY

    actions = _recommend_installations(
        manager,
        tuple(sorted(installable_missing)),
        prefer_cuda=prefer_cuda,
    )
    return RuntimePreflightResult(
        state=state,
        requirements=tuple(requirements),
        missing_required=missing_required,
        missing_optional=missing_optional,
        selected_instances={
            requirement_id: snapshot.instance_id
            for requirement_id, snapshot in snapshots.items()
        },
        binding_snapshot=snapshots,
        recommended_actions=actions,
        estimated_download_bytes=sum(item.download_size_bytes for item in actions),
        estimated_installed_bytes=sum(item.installed_size_bytes for item in actions),
    )


def _capability_available(
    manager: RuntimePackageManager,
    capability_id: str,
    fallback: set[str],
) -> bool:
    try:
        manager.select(capability_id)
    except RuntimePackageBindingError:
        return capability_id in fallback
    return True


async def _source_has_captions(
    request: PipelineRequest,
    source_registry: SourceRegistry,
) -> bool:
    try:
        adapter = source_registry.resolve(request.source)
        raw = await adapter.probe(
            request.source,
            request.auth,
            request.acquisition,
        )
        manifest = SourceManifest.model_validate(raw)
    except Exception:
        return False
    return any(manifest.subtitles.values()) or any(manifest.automatic_captions.values())


def _fallback_capabilities(runtime: PipelineRuntime | None) -> set[str]:
    if runtime is None:
        return set()
    available: set[str] = set()
    if runtime.asr_backend is not None:
        available.add(ASR_FASTER_WHISPER)
    if runtime.ocr_backend is not None:
        available.add(OCR_PADDLEOCR)
    if _executable_available(runtime.ffmpeg_path):
        available.add(TOOL_FFMPEG)
    if _executable_available(runtime.ffprobe_path):
        available.add(TOOL_FFPROBE)
    if importlib.util.find_spec("yt_dlp") is not None:
        available.add(DOWNLOAD_YTDLP)
    if runtime.pdf_browser_executable is not None and _executable_available(
        runtime.pdf_browser_executable
    ):
        available.add(RENDER_CHROMIUM_PDF)
    return available


def _executable_available(value: str | Path) -> bool:
    raw = str(value)
    selected = Path(raw).expanduser()
    if selected.is_file():
        return True
    return shutil.which(raw) is not None


def _recommend_installations(
    manager: RuntimePackageManager,
    requirement_ids: tuple[str, ...],
    *,
    prefer_cuda: bool,
) -> tuple[RuntimeInstallRecommendation, ...]:
    remaining = set(requirement_ids)
    if not remaining:
        return ()
    releases = _latest_releases(manager.inventory().available_releases)
    selected: list[RuntimeInstallRecommendation] = []
    while remaining:
        ranked: list[tuple[int, int, int, RuntimePackageRelease, set[str]]] = []
        for release in releases:
            capabilities = set(release.manifest.capability_ids)
            coverage = remaining & capabilities
            if not coverage:
                continue
            supports_cuda = any(
                "cuda" in capability.supported_devices
                for capability in release.capabilities
                if capability.capability_id in coverage
            )
            ranked.append(
                (
                    len(coverage),
                    int(prefer_cuda and supports_cuda),
                    -release.archive_size_bytes,
                    release,
                    coverage,
                )
            )
        if not ranked:
            break
        _, _, _, release, coverage = max(
            ranked,
            key=lambda item: (item[0], item[1], item[2], item[3].package_id),
        )
        devices = tuple(
            sorted(
                {
                    device
                    for capability in release.capabilities
                    for device in capability.supported_devices
                }
            )
        )
        selected.append(
            RuntimeInstallRecommendation(
                package_id=release.package_id,
                version=release.version,
                display_name=release.display_name,
                requirement_ids=tuple(sorted(coverage)),
                archive_file_name=release.archive.file_name,
                source_url=release.archive_url,
                download_size_bytes=release.archive_size_bytes,
                installed_size_bytes=release.installed_size_bytes,
                install_root=str(
                    manager.managed_root / release.package_id / release.version
                ),
                supported_devices=devices,
            )
        )
        remaining -= coverage
        releases = tuple(item for item in releases if item != release)
    return tuple(selected)


def _latest_releases(
    releases: tuple[RuntimePackageRelease, ...],
) -> tuple[RuntimePackageRelease, ...]:
    latest: dict[str, RuntimePackageRelease] = {}
    for release in releases:
        latest[release.package_id] = release
    return tuple(latest.values())


def runtime_snapshot_identities(
    snapshot: Mapping[str, RuntimeBindingSnapshot],
) -> dict[str, dict[str, str]]:
    return {
        requirement_id: {
            "instance_id": item.instance_id,
            "manifest_sha256": item.manifest_sha256,
            "capability_id": item.capability_id,
            "source": item.source.value,
        }
        for requirement_id, item in snapshot.items()
    }
