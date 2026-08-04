"""Serializable contracts for isolated, self-describing runtime packages."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RUNTIME_PACKAGE_MANIFEST = "runtime-package.json"
RUNTIME_PACKAGE_INSTALL_MARKER = ".video2notes-runtime-install.json"

_PACKAGE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_CAPABILITY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_REQUIREMENT_ID_PATTERN = r"^[a-z][a-z0-9_.-]{1,127}$"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RuntimeModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class RuntimePackageSource(StrEnum):
    BUNDLED = "bundled"
    MANAGED = "managed"
    SYSTEM = "system"
    CUSTOM = "custom"


class RuntimeTransport(StrEnum):
    IN_PROCESS = "in_process"
    WORKER = "worker"
    EXECUTABLE = "executable"


class RuntimePackageState(StrEnum):
    READY = "ready"
    MISSING = "missing"
    INVALID = "invalid"
    DEGRADED = "degraded"


class RuntimeOperationKind(StrEnum):
    INSTALL = "install"
    UPGRADE = "upgrade"
    UNINSTALL = "uninstall"
    VERIFY = "verify"


class RuntimeOperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RuntimeOperationPhase(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    VERIFYING_ARCHIVE = "verifying_archive"
    EXTRACTING = "extracting"
    PROBING = "probing"
    PUBLISHING = "publishing"
    REMOVING = "removing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FeatureAvailabilityState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


def normalize_runtime_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or path.is_absolute()
        or normalized.startswith("/")
        or ":" in normalized
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != normalized
    ):
        raise ValueError("runtime package paths must be normalized safe relative paths")
    return normalized


def validate_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if _SHA256_PATTERN.fullmatch(normalized) is None:
        raise ValueError("SHA-256 values must contain exactly 64 lowercase hex characters")
    return normalized


class RuntimePayloadFile(RuntimeModel):
    relative_path: str
    size_bytes: int = Field(ge=0)
    sha256: str

    @field_validator("relative_path", mode="before")
    @classmethod
    def validate_path(cls, value: object) -> str:
        return normalize_runtime_relative_path(str(value))

    @field_validator("sha256", mode="before")
    @classmethod
    def validate_hash(cls, value: object) -> str:
        return validate_sha256(str(value))

    @property
    def path(self) -> str:
        return self.relative_path


class RuntimeLicenseSpec(RuntimeModel):
    name: str = Field(min_length=1, max_length=200)
    relative_path: str

    @field_validator("relative_path", mode="before")
    @classmethod
    def validate_path(cls, value: object) -> str:
        return normalize_runtime_relative_path(str(value))


class RuntimeCapabilitySpec(RuntimeModel):
    capability_id: str = Field(pattern=_CAPABILITY_ID_PATTERN)
    engine_id: str = Field(pattern=_CAPABILITY_ID_PATTERN)
    protocol_version: int = Field(ge=1, le=1000)
    transport: RuntimeTransport
    entrypoint: str | None = None
    supported_devices: tuple[str, ...] = Field(min_length=1)

    @field_validator("entrypoint", mode="before")
    @classmethod
    def validate_entrypoint(cls, value: object) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        return normalize_runtime_relative_path(str(value))

    @field_validator("supported_devices", mode="before")
    @classmethod
    def normalize_devices(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)):
            return value
        normalized = tuple(str(item).strip().lower() for item in value)
        if any(item not in {"cpu", "cuda"} for item in normalized):
            raise ValueError("runtime devices must be cpu or cuda")
        if len(normalized) != len(set(normalized)):
            raise ValueError("runtime devices must be unique")
        return normalized

    @model_validator(mode="after")
    def require_fixed_entrypoint(self) -> Self:
        if self.transport is RuntimeTransport.IN_PROCESS:
            if self.entrypoint is not None:
                raise ValueError("in-process capabilities cannot declare an executable")
        elif self.entrypoint is None:
            raise ValueError("worker and executable capabilities require an entrypoint")
        return self


class RuntimePackageManifest(RuntimeModel):
    """Exact ``runtime-package.json`` schema emitted by build_runtime_pack.ps1."""

    schema_version: int = Field(default=1, alias="schema", ge=1, le=1)
    package_id: str = Field(pattern=_PACKAGE_ID_PATTERN)
    version: str = Field(pattern=_PACKAGE_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    target_triple: str = Field(pattern=_PACKAGE_ID_PATTERN)
    runtime_protocol_version: int = Field(ge=1, le=1000)
    capabilities: tuple[RuntimeCapabilitySpec, ...] = Field(min_length=1)
    licenses: tuple[RuntimeLicenseSpec, ...] = Field(min_length=1)
    upstream_sources: tuple[str, ...] = Field(min_length=1)
    payload_size_bytes: int = Field(ge=0)
    user_model_weights_included: bool = False
    files: tuple[RuntimePayloadFile, ...] = Field(min_length=1)

    @field_validator("upstream_sources", mode="before")
    @classmethod
    def validate_upstream_sources(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)):
            return value
        normalized = tuple(str(item).strip() for item in value)
        if any(not item.startswith("https://") for item in normalized):
            raise ValueError("runtime upstream sources must use HTTPS")
        return normalized

    @model_validator(mode="after")
    def validate_package_contract(self) -> Self:
        if self.user_model_weights_included:
            raise ValueError("runtime packages must never contain user model weights")
        capability_ids = [item.capability_id.casefold() for item in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("runtime capability IDs must be unique")
        if any(
            item.protocol_version != self.runtime_protocol_version
            for item in self.capabilities
        ):
            raise ValueError("runtime capability protocol versions must match the package")

        file_paths = [item.relative_path.casefold() for item in self.files]
        if len(file_paths) != len(set(file_paths)):
            raise ValueError("runtime file paths must be unique ignoring case")
        if sum(item.size_bytes for item in self.files) != self.payload_size_bytes:
            raise ValueError("runtime payload size must equal the declared file sizes")
        available_paths = set(file_paths)
        if any(item.relative_path.casefold() not in available_paths for item in self.licenses):
            raise ValueError("runtime license files must be part of the hashed payload")
        if any(
            item.entrypoint is not None and item.entrypoint.casefold() not in available_paths
            for item in self.capabilities
        ):
            raise ValueError("runtime entrypoints must be part of the hashed payload")
        return self

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(item.capability_id for item in self.capabilities)

    @property
    def payload(self) -> tuple[RuntimePayloadFile, ...]:
        return self.files


class RuntimeArchiveSpec(RuntimeModel):
    file_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}\.zip$")
    source_url: str | None = Field(default=None, max_length=2048)
    size_bytes: int = Field(gt=0)
    sha256: str
    offline_only: bool

    @field_validator("sha256", mode="before")
    @classmethod
    def validate_hash(cls, value: object) -> str:
        return validate_sha256(str(value))

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.source_url is None:
            if not self.offline_only:
                raise ValueError("published runtime archives require an HTTPS source URL")
            return self
        if self.source_url.startswith("file://"):
            if not self.offline_only:
                raise ValueError("local runtime archives must remain offline-only")
            return self
        if self.offline_only or not self.source_url.startswith("https://"):
            raise ValueError("published runtime archives require an HTTPS source URL")
        return self


class RuntimePackageRelease(RuntimeModel):
    """Exact ``*.zip.catalog-entry.json`` schema emitted by the pack builder."""

    schema_version: int = Field(default=1, alias="schema", ge=1, le=1)
    package_id: str = Field(pattern=_PACKAGE_ID_PATTERN)
    version: str = Field(pattern=_PACKAGE_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    target_triple: str = Field(pattern=_PACKAGE_ID_PATTERN)
    runtime_protocol_version: int = Field(ge=1, le=1000)
    capabilities: tuple[RuntimeCapabilitySpec, ...] = Field(min_length=1)
    archive: RuntimeArchiveSpec
    installed_size_bytes: int = Field(gt=0)
    files: tuple[RuntimePayloadFile, ...] = Field(min_length=2)
    licenses: tuple[RuntimeLicenseSpec, ...] = Field(min_length=1)
    upstream_sources: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_release_contract(self) -> Self:
        if any(
            item.protocol_version != self.runtime_protocol_version
            or item.transport is RuntimeTransport.IN_PROCESS
            for item in self.capabilities
        ):
            raise ValueError("managed runtime capabilities require the package worker protocol")
        paths = [item.relative_path.casefold() for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("runtime catalog file paths must be unique ignoring case")
        if RUNTIME_PACKAGE_MANIFEST.casefold() not in paths:
            raise ValueError("runtime catalog must hash the internal manifest")
        if sum(item.size_bytes for item in self.files) != self.installed_size_bytes:
            raise ValueError("runtime installed size must equal the catalog file sizes")
        available_paths = set(paths)
        if any(item.relative_path.casefold() not in available_paths for item in self.licenses):
            raise ValueError("runtime catalog license files must be hashed")
        if any(
            item.entrypoint is None or item.entrypoint.casefold() not in available_paths
            for item in self.capabilities
        ):
            raise ValueError("runtime catalog entrypoints must be hashed")
        return self

    @property
    def archive_url(self) -> str | None:
        return self.archive.source_url

    @property
    def archive_sha256(self) -> str:
        return self.archive.sha256

    @property
    def archive_size_bytes(self) -> int:
        return self.archive.size_bytes

    @property
    def manifest(self) -> RuntimePackageManifest:
        payload = tuple(
            item
            for item in self.files
            if item.relative_path.casefold() != RUNTIME_PACKAGE_MANIFEST.casefold()
        )
        return RuntimePackageManifest(
            package_id=self.package_id,
            version=self.version,
            display_name=self.display_name,
            target_triple=self.target_triple,
            runtime_protocol_version=self.runtime_protocol_version,
            capabilities=self.capabilities,
            licenses=self.licenses,
            upstream_sources=self.upstream_sources,
            payload_size_bytes=sum(item.size_bytes for item in payload),
            user_model_weights_included=False,
            files=payload,
        )


class RuntimePackageCandidate(RuntimeModel):
    source: RuntimePackageSource
    root: str = Field(min_length=1)
    manifest: RuntimePackageManifest | None = None

    @model_validator(mode="after")
    def require_discoverable_source(self) -> Self:
        if self.source not in {RuntimePackageSource.BUNDLED, RuntimePackageSource.SYSTEM}:
            raise ValueError("runtime candidates may only describe bundled or system packages")
        return self


class RuntimeCustomRegistration(RuntimeModel):
    instance_id: str = Field(min_length=1, max_length=300)
    package_id: str = Field(pattern=_PACKAGE_ID_PATTERN)
    version: str = Field(pattern=_PACKAGE_ID_PATTERN)
    root: str = Field(min_length=1)
    manifest_sha256: str
    registered_at_utc: str

    @field_validator("manifest_sha256", mode="before")
    @classmethod
    def validate_manifest_hash(cls, value: object) -> str:
        return validate_sha256(str(value))


class RuntimeBinding(RuntimeModel):
    requirement_id: str = Field(pattern=_REQUIREMENT_ID_PATTERN)
    capability_id: str = Field(pattern=_CAPABILITY_ID_PATTERN)
    instance_id: str = Field(min_length=1, max_length=300)
    package_id: str = Field(pattern=_PACKAGE_ID_PATTERN)
    package_version: str = Field(pattern=_PACKAGE_ID_PATTERN)
    source: RuntimePackageSource
    manifest_sha256: str
    bound_at_utc: str

    @field_validator("manifest_sha256", mode="before")
    @classmethod
    def validate_manifest_hash(cls, value: object) -> str:
        return validate_sha256(str(value))


class RuntimePackageConfig(RuntimeModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    bindings: dict[str, RuntimeBinding] = Field(default_factory=dict)
    custom_packages: dict[str, RuntimeCustomRegistration] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_mapping_keys(self) -> Self:
        if any(key != binding.requirement_id for key, binding in self.bindings.items()):
            raise ValueError("runtime binding keys must match their requirement IDs")
        if any(key != item.instance_id for key, item in self.custom_packages.items()):
            raise ValueError("custom package keys must match their instance IDs")
        return self


class RuntimePackageInstance(RuntimeModel):
    instance_id: str
    package_id: str
    version: str
    display_name: str
    source: RuntimePackageSource
    root: str
    state: RuntimePackageState
    ready: bool
    detail: str | None = None
    manifest_sha256: str | None = None
    target_triple: str | None = None
    runtime_protocol_version: int | None = None
    transport: RuntimeTransport | None = None
    capabilities: tuple[str, ...] = ()
    bound_requirements: tuple[str, ...] = ()
    leased: bool = False
    removable: bool = False
    available_version: str | None = None


class RuntimePackageOperation(RuntimeModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    operation_id: str
    kind: RuntimeOperationKind
    package_id: str
    target_version: str | None = None
    instance_id: str | None = None
    source_instance_id: str | None = None
    status: RuntimeOperationStatus
    phase: RuntimeOperationPhase = RuntimeOperationPhase.QUEUED
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    downloaded_bytes: int = Field(default=0, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    expected_installed_bytes: int | None = Field(default=None, ge=0)
    transfer_speed_bytes_per_second: float | None = Field(default=None, ge=0)
    eta_seconds: float | None = Field(default=None, ge=0)
    resumable: bool = False
    target_root: str | None = None
    cancel_requested: bool = False
    created_at_utc: str
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    owner_pid: int = Field(ge=1)
    result_instance_id: str | None = None
    detail: str | None = None
    error_code: str | None = None


class RuntimePackageLease(RuntimeModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    lease_id: str
    instance_id: str
    owner: str
    owner_pid: int = Field(ge=1)
    created_at_utc: str
    expires_at_utc: str | None = None


class RuntimePackageInventory(RuntimeModel):
    instances: tuple[RuntimePackageInstance, ...]
    bindings: dict[str, RuntimeBinding]
    operations: tuple[RuntimePackageOperation, ...]
    available_releases: tuple[RuntimePackageRelease, ...] = ()


class FeatureAvailability(RuntimeModel):
    feature_id: str = Field(pattern=_REQUIREMENT_ID_PATTERN)
    state: FeatureAvailabilityState
    required_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...] = ()
    selected_instances: dict[str, str] = Field(default_factory=dict)
    detail: str | None = None
