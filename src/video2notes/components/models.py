"""Serializable contracts for app-managed runtimes and local model assets."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from video2notes.system.hardware import HardwareTier


class ComponentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ComponentKind(StrEnum):
    RUNTIME = "runtime"
    TOOL = "tool"
    LOCAL_MODEL = "local_model"


class ComponentState(StrEnum):
    READY = "ready"
    MISSING = "missing"
    INCOMPLETE = "incomplete"
    DEGRADED = "degraded"


class ComponentActionKind(StrEnum):
    PREPARE = "prepare"
    RESUME = "resume"
    REPAIR_RUNTIME = "repair_runtime"


class DownloadSource(StrEnum):
    HUGGINGFACE_SNAPSHOT = "huggingface_snapshot"
    PADDLE_COMPATIBLE = "paddle_compatible"


class LocalModelRole(StrEnum):
    ASR = "asr"
    OCR = "ocr"


class PrepareStatus(StrEnum):
    PREPARED = "prepared"
    REUSED = "reused"
    FAILED = "failed"


def _safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != normalized
    ):
        raise ValueError("component paths must be normalized safe relative paths")
    return normalized


class ComponentManifest(ComponentModel):
    """Versioned manifest for a model managed exclusively below app data."""

    schema_version: int = Field(default=1, ge=1, le=1)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
    display_name: str = Field(min_length=1, max_length=120)
    role: LocalModelRole
    engine: str = Field(min_length=1, max_length=80)
    source_kind: DownloadSource
    source: str = Field(min_length=1, max_length=300)
    revision: str | None = Field(default=None, min_length=1, max_length=160)
    target_subdirectory: str
    required_files: tuple[str, ...] = ()
    required_nonempty_directories: tuple[str, ...] = ()

    @field_validator(
        "target_subdirectory",
        "required_files",
        "required_nonempty_directories",
        mode="before",
    )
    @classmethod
    def validate_relative_paths(cls, value: object) -> object:
        if isinstance(value, str):
            return _safe_relative_path(value)
        if isinstance(value, (tuple, list)):
            return tuple(_safe_relative_path(str(item)) for item in value)
        return value

    @model_validator(mode="after")
    def require_payload_contract(self) -> Self:
        if not self.target_subdirectory.startswith("models/"):
            raise ValueError("managed model target must be below the models directory")
        if not self.required_files and not self.required_nonempty_directories:
            raise ValueError("component manifest must define required payload artifacts")
        return self


class TierRecommendation(ComponentModel):
    hardware_tier: HardwareTier
    asr_component_id: str
    ocr_component_id: str
    asr_device: str
    asr_compute_type: str
    ocr_device: str
    reason: str


class ComponentAction(ComponentModel):
    id: str
    kind: ComponentActionKind
    component_id: str
    label: str
    automatic: bool


class ComponentInventoryItem(ComponentModel):
    id: str
    display_name: str
    kind: ComponentKind
    state: ComponentState
    ready: bool
    degraded: bool
    required: bool = True
    version: str | None = None
    path: str | None = None
    detail: str | None = None
    actions: tuple[ComponentAction, ...] = ()


class ComponentInventory(ComponentModel):
    ready: bool
    degraded: bool
    capabilities: dict[str, bool]
    items: tuple[ComponentInventoryItem, ...]
    actions: tuple[ComponentAction, ...]


class DownloadResult(ComponentModel):
    source_revision: str | None = None


class ComponentCompletionMarker(ComponentModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    component_id: str
    component_version: str
    manifest_fingerprint: str
    completed_at_utc: str
    source_revision: str | None = None
    payload_file_count: int = Field(ge=1)
    payload_size_bytes: int = Field(ge=1)


class PrepareResult(ComponentModel):
    component_id: str
    status: PrepareStatus
    path: str | None = None
    resumed: bool = False
    detail: str | None = None


class PrepareBatchResult(ComponentModel):
    ready: bool
    results: tuple[PrepareResult, ...]


class LocalAdapterSettings(ComponentModel):
    asr: dict[str, str | int | float | bool]
    ocr: dict[str, str | int | float | bool]
