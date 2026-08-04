"""Build one job-scoped pipeline runtime from a validated package snapshot."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from video2notes.audio import FasterWhisperConfig
from video2notes.components.runtime_manager import RuntimePackageManager
from video2notes.components.runtime_models import (
    RuntimeBindingSnapshot,
    RuntimeCapabilitySpec,
    RuntimeTransport,
)
from video2notes.components.runtime_preflight import (
    ASR_FASTER_WHISPER,
    DOWNLOAD_YTDLP,
    OCR_PADDLEOCR,
    RENDER_CHROMIUM_PDF,
    TOOL_FFMPEG,
    TOOL_FFPROBE,
    runtime_snapshot_identities,
)
from video2notes.ocr import PaddleOcrConfig
from video2notes.pipeline import PipelineRuntime
from video2notes.workers import (
    RuntimeWorkerAsrBackend,
    RuntimeWorkerClient,
    RuntimeWorkerOcrBackend,
)


class RuntimePackageExecutionError(RuntimeError):
    """A preflight-approved runtime cannot be represented by an execution adapter."""


def apply_runtime_package_snapshot(
    runtime: PipelineRuntime,
    manager: RuntimePackageManager,
    snapshot: Mapping[str, RuntimeBindingSnapshot],
) -> tuple[PipelineRuntime, tuple[RuntimeWorkerClient, ...]]:
    """Mutate a newly built runtime; callers own and must close returned clients."""

    clients: list[RuntimeWorkerClient] = []
    try:
        for requirement_id, binding in snapshot.items():
            instance = manager.get_instance(binding.instance_id)
            manifest = manager.manifest_for_instance(binding.instance_id)
            capability = _capability(manifest.capabilities, binding.capability_id)
            if requirement_id == ASR_FASTER_WHISPER:
                if capability.transport is RuntimeTransport.WORKER:
                    client = _client(manager, binding, capability.capability_id)
                    clients.append(client)
                    runtime.asr_backend = _asr_backend(client, runtime.asr_backend)
                    runtime.secondary_asr_backend = _optional_asr_backend(
                        client,
                        runtime.secondary_asr_backend,
                    )
                    runtime.asr_backends_by_quality = {
                        mode: _asr_backend(client, backend)
                        for mode, backend in runtime.asr_backends_by_quality.items()
                    }
                    runtime.secondary_asr_backends_by_quality = {
                        mode: _asr_backend(client, backend)
                        for mode, backend in runtime.secondary_asr_backends_by_quality.items()
                    }
                elif capability.transport is not RuntimeTransport.IN_PROCESS:
                    raise RuntimePackageExecutionError(
                        "ASR requires an in-process or worker runtime"
                    )
            elif requirement_id == OCR_PADDLEOCR:
                if capability.transport is RuntimeTransport.WORKER:
                    client = _client(manager, binding, capability.capability_id)
                    clients.append(client)
                    runtime.ocr_backend = _ocr_backend(client, runtime.ocr_backend)
                    runtime.ocr_backends_by_quality = {
                        mode: _ocr_backend(client, backend)
                        for mode, backend in runtime.ocr_backends_by_quality.items()
                    }
                elif capability.transport is not RuntimeTransport.IN_PROCESS:
                    raise RuntimePackageExecutionError(
                        "OCR requires an in-process or worker runtime"
                    )
            elif requirement_id in {TOOL_FFMPEG, TOOL_FFPROBE, RENDER_CHROMIUM_PDF}:
                if capability.transport is RuntimeTransport.EXECUTABLE:
                    executable = _entrypoint(instance.root, capability)
                    if requirement_id == TOOL_FFMPEG:
                        runtime.ffmpeg_path = str(executable)
                    elif requirement_id == TOOL_FFPROBE:
                        runtime.ffprobe_path = str(executable)
                    else:
                        runtime.pdf_browser_executable = executable
                elif capability.transport is not RuntimeTransport.IN_PROCESS:
                    raise RuntimePackageExecutionError(
                        f"{requirement_id} requires an executable or bundled runtime"
                    )
            elif (
                requirement_id == DOWNLOAD_YTDLP
                and capability.transport is not RuntimeTransport.IN_PROCESS
            ):
                raise RuntimePackageExecutionError(
                    "yt-dlp worker packages are not supported by the current source adapter"
                )
    except Exception:
        close_runtime_workers(tuple(clients))
        raise

    identities = runtime_snapshot_identities(snapshot)
    runtime.runtime_bindings = identities
    runtime.runtime_package_instance_ids = tuple(
        sorted({item.instance_id for item in snapshot.values()})
    )
    return runtime, tuple(clients)


def close_runtime_workers(clients: tuple[RuntimeWorkerClient, ...]) -> None:
    for client in reversed(clients):
        client.close()


def _client(
    manager: RuntimePackageManager,
    binding: RuntimeBindingSnapshot,
    capability_id: str,
) -> RuntimeWorkerClient:
    instance = manager.get_instance(binding.instance_id)
    manifest = manager.manifest_for_instance(binding.instance_id)
    return RuntimeWorkerClient(
        instance.root,
        manifest,
        source=instance.source.value,
        instance_id=instance.instance_id,
        capability_id=capability_id,
    )


TConfig = TypeVar("TConfig", bound=BaseModel)


def _config(backend: object | None, model: type[TConfig], label: str) -> TConfig:
    raw = getattr(backend, "config", None) or getattr(backend, "_config", None)
    try:
        return model.model_validate(raw)
    except ValueError:
        raise RuntimePackageExecutionError(
            f"{label} model weights and adapter settings are not configured"
        ) from None


def _asr_backend(
    client: RuntimeWorkerClient,
    backend: object | None,
) -> RuntimeWorkerAsrBackend:
    return RuntimeWorkerAsrBackend(
        client,
        _config(backend, FasterWhisperConfig, "ASR"),
    )


def _optional_asr_backend(
    client: RuntimeWorkerClient,
    backend: object | None,
) -> RuntimeWorkerAsrBackend | None:
    return None if backend is None else _asr_backend(client, backend)


def _ocr_backend(
    client: RuntimeWorkerClient,
    backend: object | None,
) -> RuntimeWorkerOcrBackend:
    return RuntimeWorkerOcrBackend(
        client,
        _config(backend, PaddleOcrConfig, "OCR"),
    )


def _capability(
    capabilities: tuple[RuntimeCapabilitySpec, ...],
    capability_id: str,
) -> RuntimeCapabilitySpec:
    selected = next(
        (item for item in capabilities if item.capability_id == capability_id),
        None,
    )
    if selected is None:
        raise RuntimePackageExecutionError("runtime package capability disappeared")
    return selected


def _entrypoint(root: str, capability: RuntimeCapabilitySpec) -> Path:
    if capability.entrypoint is None:
        raise RuntimePackageExecutionError("runtime executable entrypoint is missing")
    package_root = Path(root).expanduser().resolve()
    path = (
        package_root / Path(capability.entrypoint.replace("/", os.sep))
    ).resolve()
    if not path.is_relative_to(package_root) or not path.is_file():
        raise RuntimePackageExecutionError("runtime executable entrypoint is unavailable")
    return path
