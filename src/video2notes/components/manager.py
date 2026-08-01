"""Safe app-data component inventory and recoverable local-model preparation."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import sys
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from video2notes.system.hardware import HardwareTier

from .catalog import DEFAULT_COMPONENT_CATALOG, ComponentCatalog
from .downloaders import (
    ComponentDownloader,
    HuggingFaceSnapshotDownloader,
    PaddleHuggingFaceDownloader,
)
from .models import (
    ComponentAction,
    ComponentActionKind,
    ComponentCompletionMarker,
    ComponentInventory,
    ComponentInventoryItem,
    ComponentKind,
    ComponentManifest,
    ComponentState,
    DownloadSource,
    LocalAdapterSettings,
    PrepareBatchResult,
    PrepareResult,
    PrepareStatus,
    TierRecommendation,
)

BinaryLocator = Callable[[str], str | None]

_COMPLETE_MARKER = ".video2notes-component.json"
_PREPARE_STATE = ".video2notes-prepare-state.json"


class ComponentManagerError(RuntimeError):
    """Base class for safe component-management failures."""


class ComponentPathError(ComponentManagerError):
    """A managed path escaped app data or was replaced by a link."""


class ComponentNotReadyError(ComponentManagerError):
    """Adapter settings were requested before their model payload was complete."""


@dataclass(frozen=True, slots=True)
class ModuleProbeResult:
    available: bool
    version: str | None = None
    path: str | None = None


ModuleProbe = Callable[[str, str], ModuleProbeResult]


def _default_module_probe(module_name: str, distribution_name: str) -> ModuleProbeResult:
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError):
        spec = None
    if spec is None:
        return ModuleProbeResult(available=False)
    try:
        version = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        version = None
    return ModuleProbeResult(
        available=True,
        version=version,
        path=str(spec.origin) if spec.origin else None,
    )


class ComponentManager:
    """Manage only catalog-declared assets below one explicit app-data root."""

    def __init__(
        self,
        app_data_root: str | Path,
        *,
        runtime_root: str | Path | None = None,
        ffmpeg_path: str | Path | None = None,
        ffprobe_path: str | Path | None = None,
        binary_locator: BinaryLocator = shutil.which,
        module_probe: ModuleProbe = _default_module_probe,
        catalog: ComponentCatalog = DEFAULT_COMPONENT_CATALOG,
        downloaders: Mapping[DownloadSource, ComponentDownloader] | None = None,
    ) -> None:
        self.app_data_root = Path(app_data_root).expanduser().resolve()
        self.managed_root = (self.app_data_root / "components").resolve()
        self.runtime_root = (
            Path(runtime_root).expanduser().resolve()
            if runtime_root is not None
            else Path(sys.executable).resolve().parent
        )
        self._explicit_binaries = {
            "ffmpeg": Path(ffmpeg_path).expanduser().resolve() if ffmpeg_path else None,
            "ffprobe": Path(ffprobe_path).expanduser().resolve() if ffprobe_path else None,
        }
        self._binary_locator = binary_locator
        self._module_probe = module_probe
        self.catalog = catalog
        self._downloaders: dict[DownloadSource, ComponentDownloader] = {
            DownloadSource.HUGGINGFACE_SNAPSHOT: HuggingFaceSnapshotDownloader(),
            DownloadSource.PADDLE_COMPATIBLE: PaddleHuggingFaceDownloader(),
        }
        if downloaders is not None:
            self._downloaders.update(downloaders)
        self._lock = threading.RLock()
        self.managed_root.mkdir(parents=True, exist_ok=True)
        for child in (".staging", ".recovery"):
            self._managed_path(child).mkdir(parents=True, exist_ok=True)

    def recommendation(self, tier: HardwareTier) -> TierRecommendation:
        return self.catalog.recommendations[tier]

    def inventory(self, tier: HardwareTier) -> ComponentInventory:
        recommendation = self.recommendation(tier)
        runtime_items = self._runtime_inventory()
        model_items = (
            self._model_inventory(self.catalog.manifests[recommendation.asr_component_id]),
            self._model_inventory(self.catalog.manifests[recommendation.ocr_component_id]),
        )
        items = (*runtime_items, *model_items)
        by_id = {item.id: item for item in items}
        core_ready = all(
            by_id[item_id].ready
            for item_id in (
                "runtime-root",
                "python-runtime",
                "ffmpeg",
                "ffprobe",
                "yt-dlp",
                "psutil",
            )
        )
        asr_ready = all(
            by_id[item_id].ready
            for item_id in (
                "faster-whisper",
                "ctranslate2",
                "huggingface-hub",
                recommendation.asr_component_id,
            )
        )
        ocr_ready = all(
            by_id[item_id].ready
            for item_id in (
                "paddleocr",
                "paddlepaddle",
                recommendation.ocr_component_id,
            )
        )
        actions_by_id: dict[str, ComponentAction] = {}
        for item in items:
            for action in item.actions:
                actions_by_id[action.id] = action
        ready = core_ready and asr_ready and ocr_ready
        return ComponentInventory(
            ready=ready,
            degraded=not ready,
            capabilities={
                "core": core_ready,
                "download": core_ready,
                "asr": asr_ready,
                "ocr": ocr_ready,
            },
            items=items,
            actions=tuple(actions_by_id.values()),
        )

    def prepare(self, component_id: str) -> PrepareResult:
        manifest = self.catalog.manifests.get(component_id)
        if manifest is None:
            raise ComponentManagerError(f"unknown managed component: {component_id}")
        with self._lock:
            target = self._target_path(manifest)
            ready, _ = self._ready_at(target, manifest)
            if ready:
                return PrepareResult(
                    component_id=component_id,
                    status=PrepareStatus.REUSED,
                    path=str(target),
                )

            downloader = self._downloaders.get(manifest.source_kind)
            if downloader is None:
                return PrepareResult(
                    component_id=component_id,
                    status=PrepareStatus.FAILED,
                    detail=(
                        f"No app-managed {manifest.source_kind.value} downloader is available."
                    ),
                )

            staging = self._staging_path(manifest)
            if staging.exists() and (_is_link(staging) or not staging.is_dir()):
                raise ComponentPathError("managed staging path must be a regular directory")
            resumed = staging.exists() and any(staging.iterdir())
            staging.mkdir(parents=True, exist_ok=True)

            staged_ready, _ = self._ready_at(staging, manifest)
            source_revision: str | None = None
            if not staged_ready:
                self._write_prepare_state(staging, manifest, status="downloading")
                try:
                    download = downloader.download(
                        manifest,
                        staging,
                        resume=resumed,
                    )
                    source_revision = download.source_revision
                    self._validate_payload(staging, manifest)
                    marker = self._completion_marker(
                        staging,
                        manifest,
                        source_revision=source_revision,
                    )
                    self._atomic_write_json(
                        staging / _COMPLETE_MARKER,
                        marker.model_dump(mode="json"),
                    )
                    self._write_prepare_state(staging, manifest, status="complete")
                except Exception as error:
                    self._write_prepare_state(
                        staging,
                        manifest,
                        status="failed",
                        error_type=type(error).__name__,
                    )
                    return PrepareResult(
                        component_id=component_id,
                        status=PrepareStatus.FAILED,
                        resumed=resumed,
                        detail=f"Component preparation failed: {type(error).__name__}",
                    )

            staged_ready, staged_detail = self._ready_at(staging, manifest)
            if not staged_ready:
                return PrepareResult(
                    component_id=component_id,
                    status=PrepareStatus.FAILED,
                    resumed=resumed,
                    detail=staged_detail or "Staged component is incomplete.",
                )
            try:
                self._publish(staging, target, manifest)
            except (OSError, ComponentManagerError) as error:
                if staging.is_dir():
                    self._write_prepare_state(
                        staging,
                        manifest,
                        status="publish_failed",
                        error_type=type(error).__name__,
                    )
                return PrepareResult(
                    component_id=component_id,
                    status=PrepareStatus.FAILED,
                    resumed=resumed,
                    detail=f"Component publish failed: {type(error).__name__}",
                )
            final_ready, final_detail = self._ready_at(target, manifest)
            if not final_ready:
                return PrepareResult(
                    component_id=component_id,
                    status=PrepareStatus.FAILED,
                    path=str(target),
                    resumed=resumed,
                    detail=final_detail or "Published component is incomplete.",
                )
            return PrepareResult(
                component_id=component_id,
                status=PrepareStatus.PREPARED,
                path=str(target),
                resumed=resumed,
            )

    def prepare_recommended(self, tier: HardwareTier) -> PrepareBatchResult:
        recommendation = self.recommendation(tier)
        results = tuple(
            self.prepare(component_id)
            for component_id in (
                recommendation.asr_component_id,
                recommendation.ocr_component_id,
            )
        )
        return PrepareBatchResult(
            ready=self.inventory(tier).ready,
            results=results,
        )

    def local_adapter_settings(self, tier: HardwareTier) -> LocalAdapterSettings:
        recommendation = self.recommendation(tier)
        asr_manifest = self.catalog.manifests[recommendation.asr_component_id]
        ocr_manifest = self.catalog.manifests[recommendation.ocr_component_id]
        asr_path = self._target_path(asr_manifest)
        ocr_path = self._target_path(ocr_manifest)
        asr_ready, _ = self._ready_at(asr_path, asr_manifest)
        ocr_ready, _ = self._ready_at(ocr_path, ocr_manifest)
        if not asr_ready or not ocr_ready:
            raise ComponentNotReadyError("recommended local ASR/OCR models are not ready")
        return LocalAdapterSettings(
            asr={
                "engine": "faster_whisper",
                "model_path": str(asr_path),
                "device": recommendation.asr_device,
                "compute_type": recommendation.asr_compute_type,
            },
            ocr={
                "engine": "paddleocr",
                "detection_model_dir": str(ocr_path / "detection"),
                "recognition_model_dir": str(ocr_path / "recognition"),
                "device": recommendation.ocr_device,
                "language": "ch",
                "api_family": "auto",
            },
        )

    def _runtime_inventory(self) -> tuple[ComponentInventoryItem, ...]:
        runtime_root_ready = self.runtime_root.is_dir()
        python_path = Path(sys.executable).resolve()
        items: list[ComponentInventoryItem] = [
            self._simple_runtime_item(
                component_id="runtime-root",
                display_name="Portable runtime resources",
                ready=runtime_root_ready,
                path=str(self.runtime_root),
            ),
            self._simple_runtime_item(
                component_id="python-runtime",
                display_name="Embedded Python runtime",
                ready=python_path.is_file(),
                path=str(python_path),
                version=sys.version.split()[0],
            ),
        ]
        for tool_name in ("ffmpeg", "ffprobe"):
            path = self._find_binary(tool_name)
            items.append(
                self._simple_runtime_item(
                    component_id=tool_name,
                    display_name=tool_name,
                    ready=path is not None,
                    path=str(path) if path is not None else None,
                    kind=ComponentKind.TOOL,
                )
            )
        for component_id, display_name, module_name, distribution_name in (
            ("yt-dlp", "yt-dlp", "yt_dlp", "yt-dlp"),
            ("psutil", "psutil resource monitor", "psutil", "psutil"),
            ("faster-whisper", "faster-whisper runtime", "faster_whisper", "faster-whisper"),
            ("ctranslate2", "CTranslate2 runtime", "ctranslate2", "ctranslate2"),
            (
                "huggingface-hub",
                "Hugging Face managed download runtime",
                "huggingface_hub",
                "huggingface-hub",
            ),
            ("paddleocr", "PaddleOCR runtime", "paddleocr", "paddleocr"),
            ("paddlepaddle", "PaddlePaddle runtime", "paddle", "paddlepaddle"),
        ):
            probe = self._module_probe(module_name, distribution_name)
            items.append(
                self._simple_runtime_item(
                    component_id=component_id,
                    display_name=display_name,
                    ready=probe.available,
                    version=probe.version,
                    path=probe.path,
                )
            )
        return tuple(items)

    def _simple_runtime_item(
        self,
        *,
        component_id: str,
        display_name: str,
        ready: bool,
        path: str | None,
        version: str | None = None,
        kind: ComponentKind = ComponentKind.RUNTIME,
    ) -> ComponentInventoryItem:
        actions = (
            ()
            if ready
            else (
                ComponentAction(
                    id=f"repair-{component_id}",
                    kind=ComponentActionKind.REPAIR_RUNTIME,
                    component_id=component_id,
                    label=f"Repair packaged {display_name}",
                    automatic=False,
                ),
            )
        )
        return ComponentInventoryItem(
            id=component_id,
            display_name=display_name,
            kind=kind,
            state=ComponentState.READY if ready else ComponentState.MISSING,
            ready=ready,
            degraded=not ready,
            version=version,
            path=path,
            detail=None if ready else "Required portable runtime component is missing.",
            actions=actions,
        )

    def _model_inventory(self, manifest: ComponentManifest) -> ComponentInventoryItem:
        target = self._target_path(manifest)
        ready, detail = self._ready_at(target, manifest)
        staging = self._staging_path(manifest)
        interrupted = not ready and staging.is_dir() and any(staging.iterdir())
        if ready:
            state = ComponentState.READY
            actions: tuple[ComponentAction, ...] = ()
        else:
            state = (
                ComponentState.INCOMPLETE
                if interrupted or target.exists()
                else ComponentState.MISSING
            )
            action_kind = ComponentActionKind.RESUME if interrupted else ComponentActionKind.PREPARE
            automatic = manifest.source_kind in self._downloaders
            actions = (
                ComponentAction(
                    id=f"{action_kind.value}-{manifest.id}",
                    kind=action_kind,
                    component_id=manifest.id,
                    label=(
                        f"Resume {manifest.display_name}"
                        if interrupted
                        else f"Prepare {manifest.display_name}"
                    ),
                    automatic=automatic,
                ),
            )
            if not automatic:
                detail = (
                    detail
                    or f"A compatible {manifest.source_kind.value} downloader is required."
                )
        return ComponentInventoryItem(
            id=manifest.id,
            display_name=manifest.display_name,
            kind=ComponentKind.LOCAL_MODEL,
            state=state,
            ready=ready,
            degraded=not ready,
            version=manifest.version,
            path=str(target),
            detail=detail,
            actions=actions,
        )

    def _find_binary(self, name: str) -> Path | None:
        explicit = self._explicit_binaries[name]
        candidates: list[Path] = []
        if explicit is not None:
            candidates.append(explicit)
        executable_name = f"{name}.exe" if os.name == "nt" else name
        candidates.extend(
            (
                self.runtime_root / "tools" / executable_name,
                self.runtime_root / executable_name,
            )
        )
        located = self._binary_locator(name)
        if located:
            candidates.append(Path(located).expanduser())
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.is_file():
                return resolved
        return None

    def _target_path(self, manifest: ComponentManifest) -> Path:
        return self._managed_path(manifest.target_subdirectory)

    def _staging_path(self, manifest: ComponentManifest) -> Path:
        return self._managed_path(f".staging/{manifest.id}-{manifest.version}")

    def _managed_path(self, relative: str) -> Path:
        candidate = self.managed_root / Path(relative.replace("/", os.sep))
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.managed_root):
            raise ComponentPathError("component path escapes the app-data managed root")
        current = self.managed_root
        for part in candidate.relative_to(self.managed_root).parts:
            current /= part
            if current.exists() and _is_link(current):
                raise ComponentPathError("managed component paths cannot contain links")
        return candidate

    def _manifest_fingerprint(self, manifest: ComponentManifest) -> str:
        encoded = json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _ready_at(
        self,
        root: Path,
        manifest: ComponentManifest,
    ) -> tuple[bool, str | None]:
        if not root.exists():
            return False, "Managed model payload is not downloaded."
        if _is_link(root) or not root.is_dir():
            return False, "Managed model path is not a regular directory."
        marker_path = root / _COMPLETE_MARKER
        if not marker_path.is_file():
            return False, "Completion marker is missing; preparation can be resumed."
        try:
            marker = ComponentCompletionMarker.model_validate_json(
                marker_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return False, "Completion marker is invalid; preparation can be resumed."
        if (
            marker.component_id != manifest.id
            or marker.component_version != manifest.version
            or marker.manifest_fingerprint != self._manifest_fingerprint(manifest)
        ):
            return False, "Completion marker does not match the current component manifest."
        try:
            self._validate_payload(root, manifest)
        except ComponentManagerError as error:
            return False, str(error)
        payload_files = self._payload_files(root)
        payload_size = sum(item.stat().st_size for item in payload_files)
        if (
            len(payload_files) != marker.payload_file_count
            or payload_size != marker.payload_size_bytes
        ):
            return False, "Managed model payload changed after preparation."
        for relative, expected_digest in marker.required_file_sha256.items():
            candidate = root / Path(relative.replace("/", os.sep))
            if not candidate.is_file() or _sha256_file(candidate) != expected_digest:
                return False, f"Required model file changed after preparation: {relative}"
        return True, None

    def _validate_payload(self, root: Path, manifest: ComponentManifest) -> None:
        resolved_root = root.resolve()
        if not resolved_root.is_relative_to(self.managed_root):
            raise ComponentPathError("model payload is outside the app-data managed root")
        for entry in root.rglob("*"):
            if _is_link(entry):
                raise ComponentPathError("managed model payload cannot contain symbolic links")
            if not entry.resolve().is_relative_to(resolved_root):
                raise ComponentPathError("managed model payload entry escapes its component root")
        for relative in manifest.required_files:
            candidate = (root / Path(relative.replace("/", os.sep))).resolve()
            if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
                raise ComponentManagerError(f"required model file is missing: {relative}")
            if candidate.stat().st_size < 1:
                raise ComponentManagerError(f"required model file is empty: {relative}")
        for relative in manifest.required_nonempty_directories:
            candidate = (root / Path(relative.replace("/", os.sep))).resolve()
            if not candidate.is_relative_to(resolved_root) or not candidate.is_dir():
                raise ComponentManagerError(f"required model directory is missing: {relative}")
            if not self._payload_files(candidate):
                raise ComponentManagerError(f"required model directory is empty: {relative}")

    @staticmethod
    def _payload_files(root: Path) -> list[Path]:
        return [
            item
            for item in root.rglob("*")
            if item.is_file()
            and item.name not in {_COMPLETE_MARKER, _PREPARE_STATE}
            and ".hf-cache" not in item.relative_to(root).parts
        ]

    def _completion_marker(
        self,
        root: Path,
        manifest: ComponentManifest,
        *,
        source_revision: str | None,
    ) -> ComponentCompletionMarker:
        payload_files = self._payload_files(root)
        return ComponentCompletionMarker(
            component_id=manifest.id,
            component_version=manifest.version,
            manifest_fingerprint=self._manifest_fingerprint(manifest),
            completed_at_utc=datetime.now(UTC).isoformat(),
            source_revision=source_revision,
            payload_file_count=len(payload_files),
            payload_size_bytes=sum(item.stat().st_size for item in payload_files),
            required_file_sha256={
                relative: _sha256_file(
                    root / Path(relative.replace("/", os.sep))
                )
                for relative in manifest.required_files
            },
        )

    def _publish(
        self,
        staging: Path,
        target: Path,
        manifest: ComponentManifest,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _is_link(target):
                raise ComponentPathError("refusing to replace a linked managed model path")
            ready, _ = self._ready_at(target, manifest)
            if ready:
                return
            recovery = self._managed_path(
                ".recovery/"
                f"{manifest.id}-{manifest.version}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-"
                f"{uuid.uuid4().hex}"
            )
            recovery.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, recovery)
        os.replace(staging, target)

    def _write_prepare_state(
        self,
        root: Path,
        manifest: ComponentManifest,
        *,
        status: str,
        error_type: str | None = None,
    ) -> None:
        if _is_link(root) or not root.resolve().is_relative_to(self.managed_root):
            raise ComponentPathError("prepare state path is not a regular managed directory")
        self._atomic_write_json(
            root / _PREPARE_STATE,
            {
                "schema_version": 1,
                "component_id": manifest.id,
                "component_version": manifest.version,
                "status": status,
                "updated_at_utc": datetime.now(UTC).isoformat(),
                "error_type": error_type,
            },
        )

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
