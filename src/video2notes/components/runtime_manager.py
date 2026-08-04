"""Inventory and lifecycle management for isolated runtime packages."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Protocol

from .runtime_catalog import (
    RuntimePackageCatalog,
    runtime_catalog_from_environment,
)
from .runtime_downloaders import (
    RuntimePackageDownloadCancelled,
    RuntimePackageDownloader,
    RuntimePackageIntegrityError,
    UrlRuntimePackageDownloader,
    runtime_manifest_sha256,
    safe_extract_runtime_archive,
    validate_runtime_package_root,
    validate_runtime_release_root,
)
from .runtime_models import (
    RUNTIME_PACKAGE_INSTALL_MARKER,
    RUNTIME_PACKAGE_MANIFEST,
    RuntimeBinding,
    RuntimeBindingSnapshot,
    RuntimeCapabilitySpec,
    RuntimeCustomRegistration,
    RuntimeOperationKind,
    RuntimeOperationPhase,
    RuntimeOperationStatus,
    RuntimePackageCandidate,
    RuntimePackageConfig,
    RuntimePackageInstance,
    RuntimePackageInventory,
    RuntimePackageLease,
    RuntimePackageManifest,
    RuntimePackageOperation,
    RuntimePackageRelease,
    RuntimePackageSource,
    RuntimePackageState,
    RuntimeTransport,
)

SYSTEM_PACKAGES_ENVIRONMENT = "VIDEO2NOTES_RUNTIME_SYSTEM_PACKAGES"
_ACTIVE_OPERATION_STATUSES = {
    RuntimeOperationStatus.QUEUED,
    RuntimeOperationStatus.RUNNING,
}


class RuntimePackageManagerError(RuntimeError):
    """Base class for runtime package management failures."""


class RuntimePackageNotFoundError(RuntimePackageManagerError):
    """A package, instance, release, or operation could not be found."""


class RuntimePackagePathError(RuntimePackageManagerError):
    """A managed path escaped app data or traversed a filesystem link."""


class RuntimePackageOwnershipError(RuntimePackageManagerError):
    """The requested mutation is forbidden for this package source."""


class RuntimePackageBusyError(RuntimePackageManagerError):
    """A binding, lease, or active operation protects a package instance."""


class RuntimePackageBindingError(RuntimePackageManagerError):
    """A capability cannot satisfy the requested runtime binding."""


class RuntimePackageOperationError(RuntimePackageManagerError):
    """A persistent runtime operation cannot be created or updated."""


class RuntimePackageProber(Protocol):
    def probe(self, root: Path, release: RuntimePackageRelease) -> None: ...


class WorkerRuntimePackageProber:
    """Start each isolated worker entrypoint before publishing a package."""

    def __init__(self, *, timeout_seconds: float = 45.0) -> None:
        self.timeout_seconds = timeout_seconds

    def probe(self, root: Path, release: RuntimePackageRelease) -> None:
        from video2notes.workers.client import RuntimeWorkerClient, RuntimeWorkerError

        worker_capabilities = [
            item
            for item in release.capabilities
            if item.transport is RuntimeTransport.WORKER
        ]
        entrypoints: dict[str, list[RuntimeCapabilitySpec]] = {}
        for capability in worker_capabilities:
            assert capability.entrypoint is not None
            entrypoints.setdefault(capability.entrypoint, []).append(capability)
        for capabilities in entrypoints.values():
            first = capabilities[0]
            client = RuntimeWorkerClient(
                root,
                release.manifest,
                source=RuntimePackageSource.MANAGED.value,
                instance_id=_managed_instance_id(release.package_id, release.version),
                capability_id=first.capability_id,
                timeout_seconds=self.timeout_seconds,
            )
            try:
                hello = client.hello()
            except RuntimeWorkerError as error:
                raise RuntimePackageIntegrityError(
                    "runtime worker health probe failed"
                ) from error
            finally:
                client.close(force=True)
            advertised = set(hello.capabilities)
            for capability in capabilities:
                if capability.capability_id not in advertised:
                    raise RuntimePackageIntegrityError(
                        "runtime worker did not advertise every declared capability"
                    )
                actual_devices = set(
                    hello.supported_devices.get(capability.capability_id, ())
                )
                if not set(capability.supported_devices).issubset(actual_devices):
                    raise RuntimePackageIntegrityError(
                        "runtime worker device support does not match the catalog"
                    )


class RuntimePackageManager:
    """Manage worker/archive packs without mutating the app's Python runtime."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        catalog: RuntimePackageCatalog | None = None,
        downloader: RuntimePackageDownloader | None = None,
        prober: RuntimePackageProber | None = None,
        bundled_packages: Sequence[RuntimePackageCandidate] = (),
        system_packages: Sequence[RuntimePackageCandidate] = (),
        bundled_roots: Sequence[str | Path] = (),
        system_roots: Sequence[str | Path] = (),
        max_workers: int = 2,
    ) -> None:
        if max_workers < 1:
            raise ValueError("runtime package manager needs at least one worker")
        self.data_root = Path(data_root).expanduser().resolve()
        self.package_root = (self.data_root / "runtime-packages").resolve()
        self.managed_root = (self.package_root / "managed").resolve()
        self.staging_root = (self.package_root / ".staging").resolve()
        self.recovery_root = (self.package_root / ".recovery").resolve()
        self.download_root = (self.package_root / ".downloads").resolve()
        self.operation_root = (self.package_root / "operations").resolve()
        self.lease_root = (self.package_root / "leases").resolve()
        self.config_root = (self.data_root / "config").resolve()
        self.config_path = (self.config_root / "runtime-packages.json").resolve()
        self.lock_path = (self.package_root / ".runtime-packages.lock").resolve()

        for directory in (
            self.managed_root,
            self.staging_root,
            self.recovery_root,
            self.download_root,
            self.operation_root,
            self.lease_root,
            self.config_root,
        ):
            self._ensure_managed_directory(directory)

        self.catalog = catalog if catalog is not None else runtime_catalog_from_environment()
        self.downloader = downloader or UrlRuntimePackageDownloader()
        self.prober = prober or WorkerRuntimePackageProber()
        self._bundled_packages = (
            *bundled_packages,
            *(
                RuntimePackageCandidate(
                    source=RuntimePackageSource.BUNDLED,
                    root=str(Path(root).expanduser()),
                )
                for root in bundled_roots
            ),
        )
        discovered_system_roots = [Path(root).expanduser() for root in system_roots]
        raw_environment_roots = os.environ.get(SYSTEM_PACKAGES_ENVIRONMENT, "").strip()
        if raw_environment_roots:
            discovered_system_roots.extend(
                Path(item).expanduser()
                for item in raw_environment_roots.split(os.pathsep)
                if item.strip()
            )
        self._system_packages = (
            *system_packages,
            *(
                RuntimePackageCandidate(
                    source=RuntimePackageSource.SYSTEM,
                    root=str(root),
                )
                for root in discovered_system_roots
            ),
        )
        self._thread_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="runtime-package",
        )
        self._futures: dict[str, Future[None]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._closed = False
        with self._state_guard():
            if not self.config_path.exists():
                self._write_config(RuntimePackageConfig())
            else:
                self._read_config()

    def close(self, *, wait: bool = True) -> None:
        with self._thread_lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def __enter__(self) -> RuntimePackageManager:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def inventory(self) -> RuntimePackageInventory:
        with self._state_guard():
            config = self._read_config()
            leases = self._read_active_leases()
            operations = self._read_operations()
            instances = self._discover_instances(config)

        bindings_by_instance: dict[str, list[str]] = {}
        for requirement_id, binding in config.bindings.items():
            bindings_by_instance.setdefault(binding.instance_id, []).append(requirement_id)
        leased_instances = {item.instance_id for item in leases}
        active_operation_instances = {
            item.instance_id
            for item in operations
            if item.status in _ACTIVE_OPERATION_STATUSES and item.instance_id is not None
        }
        active_operation_instances.update(
            item.source_instance_id
            for item in operations
            if item.status in _ACTIVE_OPERATION_STATUSES and item.source_instance_id is not None
        )

        enriched: list[RuntimePackageInstance] = []
        for instance in instances:
            bound = tuple(sorted(bindings_by_instance.get(instance.instance_id, ())))
            leased = instance.instance_id in leased_instances
            available_version: str | None = None
            try:
                latest = self.catalog.latest(instance.package_id)
            except KeyError:
                pass
            else:
                if latest.version != instance.version:
                    available_version = latest.version
            removable = (
                instance.source is RuntimePackageSource.MANAGED
                and instance.ready
                and not bound
                and not leased
                and instance.instance_id not in active_operation_instances
            )
            enriched.append(
                instance.model_copy(
                    update={
                        "bound_requirements": bound,
                        "leased": leased,
                        "removable": removable,
                        "available_version": available_version,
                    }
                )
            )

        source_order = {
            RuntimePackageSource.BUNDLED: 0,
            RuntimePackageSource.MANAGED: 1,
            RuntimePackageSource.SYSTEM: 2,
            RuntimePackageSource.CUSTOM: 3,
        }
        enriched.sort(
            key=lambda item: (
                source_order[item.source],
                item.package_id,
                item.version,
                item.instance_id,
            )
        )
        return RuntimePackageInventory(
            instances=tuple(enriched),
            bindings=dict(config.bindings),
            operations=operations,
            available_releases=self.catalog.releases,
        )

    def discover(self) -> RuntimePackageInventory:
        """Refresh all four package sources and return the resulting inventory."""

        return self.inventory()

    def resolve(self, requirement_id: str) -> RuntimePackageInstance:
        inventory = self.inventory()
        binding = inventory.bindings.get(requirement_id)
        if binding is None:
            raise RuntimePackageBindingError(
                f"runtime requirement has no binding: {requirement_id}"
            )
        instance = next(
            (item for item in inventory.instances if item.instance_id == binding.instance_id),
            None,
        )
        if instance is None or not instance.ready:
            raise RuntimePackageBindingError("bound runtime package is unavailable")
        if instance.manifest_sha256 != binding.manifest_sha256:
            raise RuntimePackageBindingError("bound runtime package identity changed")
        if binding.capability_id not in instance.capabilities:
            raise RuntimePackageBindingError("bound runtime capability is unavailable")
        return instance

    def select(
        self,
        requirement_id: str,
        capability_id: str | None = None,
    ) -> RuntimePackageInstance:
        """Resolve an explicit binding, otherwise select the first ready compatible source."""

        inventory = self.inventory()
        if requirement_id in inventory.bindings:
            return self.resolve(requirement_id)
        selected_capability = capability_id or requirement_id
        instance = next(
            (
                item
                for item in inventory.instances
                if item.ready and selected_capability in item.capabilities
            ),
            None,
        )
        if instance is None:
            raise RuntimePackageBindingError(
                f"no ready runtime package provides {selected_capability}"
            )
        return instance

    def manifest_for_instance(self, instance_id: str) -> RuntimePackageManifest:
        instance = self._find_instance(instance_id)
        root = Path(instance.root).expanduser().resolve()
        manifest_path = root / RUNTIME_PACKAGE_MANIFEST
        if manifest_path.is_file():
            return validate_runtime_package_root(root, full_hash=False).manifest
        for candidate in (*self._bundled_packages, *self._system_packages):
            if candidate.manifest is None:
                continue
            candidate_root = Path(candidate.root).expanduser().resolve()
            if (
                candidate_root == root
                and candidate.manifest.package_id == instance.package_id
                and candidate.manifest.version == instance.version
            ):
                return candidate.manifest
        raise RuntimePackageBindingError("runtime package manifest is unavailable")

    def get_instance(self, instance_id: str) -> RuntimePackageInstance:
        return self._find_instance(instance_id)

    def bind(
        self,
        requirement_id: str,
        instance_id: str,
        capability_id: str | None = None,
    ) -> RuntimeBinding:
        inventory = self.inventory()
        instance = next(
            (item for item in inventory.instances if item.instance_id == instance_id),
            None,
        )
        if instance is None:
            raise RuntimePackageNotFoundError(f"unknown runtime instance: {instance_id}")
        if not instance.ready or instance.manifest_sha256 is None:
            raise RuntimePackageBindingError("only ready runtime packages can be bound")
        selected_capability = capability_id
        if selected_capability is None:
            if requirement_id in instance.capabilities:
                selected_capability = requirement_id
            elif len(instance.capabilities) == 1:
                selected_capability = instance.capabilities[0]
            else:
                raise RuntimePackageBindingError(
                    "capability_id is required for a package with multiple capabilities"
                )
        if selected_capability not in instance.capabilities:
            raise RuntimePackageBindingError(
                f"runtime package does not provide {selected_capability}"
            )
        if instance.source in {
            RuntimePackageSource.SYSTEM,
            RuntimePackageSource.CUSTOM,
        }:
            try:
                validation = validate_runtime_package_root(Path(instance.root), full_hash=True)
            except RuntimePackageIntegrityError as error:
                raise RuntimePackageBindingError(str(error)) from None
            if validation.manifest_sha256 != instance.manifest_sha256:
                raise RuntimePackageBindingError(
                    "runtime package changed while its binding was being created"
                )
        binding = RuntimeBinding(
            requirement_id=requirement_id,
            capability_id=selected_capability,
            instance_id=instance.instance_id,
            package_id=instance.package_id,
            package_version=instance.version,
            source=instance.source,
            manifest_sha256=instance.manifest_sha256,
            bound_at_utc=_utc_now(),
        )
        with self._state_guard():
            config = self._read_config()
            updated = dict(config.bindings)
            updated[requirement_id] = binding
            self._write_config(config.model_copy(update={"bindings": updated}))
        return binding

    def unbind(self, requirement_id: str) -> bool:
        with self._state_guard():
            config = self._read_config()
            if requirement_id not in config.bindings:
                return False
            updated = dict(config.bindings)
            del updated[requirement_id]
            self._write_config(config.model_copy(update={"bindings": updated}))
            return True

    def register_custom(self, root: str | Path) -> RuntimePackageInstance:
        selected = Path(root).expanduser().resolve()
        if selected.is_file():
            if selected.name != RUNTIME_PACKAGE_MANIFEST:
                raise RuntimePackageOwnershipError(
                    f"custom package manifest must be named {RUNTIME_PACKAGE_MANIFEST}"
                )
            custom_root = selected.parent
        else:
            custom_root = selected
        if custom_root.is_relative_to(self.package_root):
            raise RuntimePackageOwnershipError(
                "custom packages cannot point into app-managed package storage"
            )
        try:
            validation = validate_runtime_package_root(custom_root, full_hash=True)
        except RuntimePackageIntegrityError as error:
            raise RuntimePackagePathError(str(error)) from None
        if any(
            item.transport is RuntimeTransport.IN_PROCESS
            for item in validation.manifest.capabilities
        ):
            raise RuntimePackageOwnershipError(
                "custom packages must run as an isolated worker or executable"
            )
        instance_id = _instance_id(
            RuntimePackageSource.CUSTOM,
            validation.manifest,
            custom_root,
        )
        registration = RuntimeCustomRegistration(
            instance_id=instance_id,
            package_id=validation.manifest.package_id,
            version=validation.manifest.version,
            root=str(custom_root),
            manifest_sha256=validation.manifest_sha256,
            registered_at_utc=_utc_now(),
        )
        with self._state_guard():
            config = self._read_config()
            custom_packages = dict(config.custom_packages)
            custom_packages[instance_id] = registration
            self._write_config(config.model_copy(update={"custom_packages": custom_packages}))
        return self._instance_from_root(
            RuntimePackageSource.CUSTOM,
            custom_root,
            expected_registration=registration,
        )

    def forget_custom(self, instance_id: str) -> bool:
        with self._state_guard():
            config = self._read_config()
            if instance_id not in config.custom_packages:
                return False
            self._assert_no_bindings(config, instance_id)
            self._assert_no_leases(instance_id)
            custom_packages = dict(config.custom_packages)
            del custom_packages[instance_id]
            self._write_config(config.model_copy(update={"custom_packages": custom_packages}))
            return True

    def acquire_lease(
        self,
        instance_id: str,
        *,
        owner: str,
        expires_in_seconds: float | None = None,
    ) -> RuntimePackageLease:
        if not owner.strip():
            raise ValueError("runtime lease owner cannot be empty")
        instance = self._find_instance(instance_id)
        if not instance.ready:
            raise RuntimePackageBusyError("unavailable runtime packages cannot be leased")
        expires_at: str | None = None
        if expires_in_seconds is not None:
            if expires_in_seconds <= 0:
                raise ValueError("runtime lease duration must be positive")
            expires_at = (
                datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
            ).isoformat()
        lease = RuntimePackageLease(
            lease_id=uuid.uuid4().hex,
            instance_id=instance_id,
            owner=owner.strip(),
            owner_pid=os.getpid(),
            created_at_utc=_utc_now(),
            expires_at_utc=expires_at,
        )
        with self._state_guard():
            self._atomic_write_json(
                self._lease_path(lease.lease_id),
                lease.model_dump(mode="json"),
            )
        return lease

    def release_lease(self, lease_id: str) -> bool:
        with self._state_guard():
            path = self._lease_path(lease_id)
            if not path.is_file():
                return False
            path.unlink()
            return True

    def acquire_snapshot_leases(
        self,
        snapshot: dict[str, RuntimeBindingSnapshot],
        *,
        owner: str,
    ) -> tuple[RuntimePackageLease, ...]:
        """Atomically revalidate a preflight snapshot and lease its distinct instances."""

        if not owner.strip():
            raise ValueError("runtime lease owner cannot be empty")
        if not snapshot:
            return ()
        with self._state_guard():
            config = self._read_config()
            instances = {
                item.instance_id: item for item in self._discover_instances(config)
            }
            selected: dict[str, RuntimePackageInstance] = {}
            for requirement_id, expected in snapshot.items():
                if requirement_id != expected.requirement_id:
                    raise RuntimePackageBindingError(
                        "runtime preflight snapshot keys are inconsistent"
                    )
                instance = instances.get(expected.instance_id)
                if (
                    instance is None
                    or not instance.ready
                    or instance.source is not expected.source
                    or instance.manifest_sha256 != expected.manifest_sha256
                    or expected.capability_id not in instance.capabilities
                ):
                    raise RuntimePackageBindingError(
                        "runtime package selection changed after preflight"
                    )
                self._assert_no_active_instance_operation(instance.instance_id)
                selected[instance.instance_id] = instance

            leases: list[RuntimePackageLease] = []
            try:
                for instance_id in sorted(selected):
                    lease = RuntimePackageLease(
                        lease_id=uuid.uuid4().hex,
                        instance_id=instance_id,
                        owner=owner.strip(),
                        owner_pid=os.getpid(),
                        created_at_utc=_utc_now(),
                    )
                    self._atomic_write_json(
                        self._lease_path(lease.lease_id),
                        lease.model_dump(mode="json"),
                    )
                    leases.append(lease)
            except Exception:
                for lease in leases:
                    self._lease_path(lease.lease_id).unlink(missing_ok=True)
                raise
            return tuple(leases)

    def release_leases(self, leases: Sequence[RuntimePackageLease]) -> None:
        with self._state_guard():
            for lease in leases:
                self._lease_path(lease.lease_id).unlink(missing_ok=True)

    @contextmanager
    def lease(
        self,
        instance_id: str,
        *,
        owner: str,
        expires_in_seconds: float | None = None,
    ) -> Iterator[RuntimePackageLease]:
        lease = self.acquire_lease(
            instance_id,
            owner=owner,
            expires_in_seconds=expires_in_seconds,
        )
        try:
            yield lease
        finally:
            self.release_lease(lease.lease_id)

    def install_async(
        self,
        package_id: str,
        version: str | None = None,
        *,
        bind_requirements: Sequence[str] = (),
    ) -> RuntimePackageOperation:
        try:
            release = (
                self.catalog.latest(package_id)
                if version is None
                else self.catalog.get(package_id, version)
            )
        except KeyError as error:
            raise RuntimePackageNotFoundError(str(error)) from None
        requested_bindings = tuple(dict.fromkeys(bind_requirements))
        self._binding_capabilities(release.manifest, requested_bindings)

        with self._state_guard():
            existing = self._active_release_operation(
                RuntimeOperationKind.INSTALL,
                package_id,
                release.version,
            )
            if existing is not None:
                return existing
            ready_instance = self._managed_instance(package_id, release.version)
            if (
                ready_instance is not None
                and ready_instance.ready
                and ready_instance.manifest_sha256
                == runtime_manifest_sha256(release.manifest)
            ):
                self._bind_install_requirements(
                    requested_bindings,
                    ready_instance.instance_id,
                    release.manifest,
                    ready_instance.manifest_sha256,
                )
                operation = self._new_operation(
                    RuntimeOperationKind.INSTALL,
                    package_id=package_id,
                    target_version=release.version,
                    instance_id=ready_instance.instance_id,
                    status=RuntimeOperationStatus.SUCCEEDED,
                    phase=RuntimeOperationPhase.COMPLETED,
                    progress=1.0,
                    expected_installed_bytes=release.installed_size_bytes,
                    target_root=str(
                        self._managed_target(release.package_id, release.version)
                    ),
                    requested_bindings=requested_bindings,
                    result_instance_id=ready_instance.instance_id,
                    detail="Runtime package is already installed.",
                )
                return operation
            operation = self._new_operation(
                RuntimeOperationKind.INSTALL,
                package_id=package_id,
                target_version=release.version,
                instance_id=_managed_instance_id(package_id, release.version),
                total_bytes=release.archive_size_bytes,
                expected_installed_bytes=release.installed_size_bytes,
                resumable=True,
                target_root=str(self._managed_target(package_id, release.version)),
                requested_bindings=requested_bindings,
            )
        self._submit_install(operation, release, upgrade_source=None)
        return self.operation(operation.operation_id)

    def install(
        self,
        package_id: str,
        version: str | None = None,
        *,
        bind_requirements: Sequence[str] = (),
    ) -> RuntimePackageOperation:
        return self.install_async(
            package_id,
            version,
            bind_requirements=bind_requirements,
        )

    def upgrade_async(
        self,
        instance_id: str,
        version: str | None = None,
    ) -> RuntimePackageOperation:
        source = self._find_instance(instance_id)
        if source.source is not RuntimePackageSource.MANAGED:
            raise RuntimePackageOwnershipError("only managed runtime packages can be upgraded")
        if source.leased:
            raise RuntimePackageBusyError("leased runtime packages cannot switch versions")
        try:
            release = (
                self.catalog.latest(source.package_id)
                if version is None
                else self.catalog.get(source.package_id, version)
            )
        except KeyError as error:
            raise RuntimePackageNotFoundError(str(error)) from None
        if release.version == source.version:
            raise RuntimePackageOperationError("runtime package is already at that version")

        with self._state_guard():
            self._assert_no_leases(source.instance_id)
            existing = self._active_release_operation(
                RuntimeOperationKind.UPGRADE,
                source.package_id,
                release.version,
            )
            if existing is not None:
                return existing
            operation = self._new_operation(
                RuntimeOperationKind.UPGRADE,
                package_id=source.package_id,
                target_version=release.version,
                instance_id=_managed_instance_id(source.package_id, release.version),
                source_instance_id=source.instance_id,
                total_bytes=release.archive_size_bytes,
                expected_installed_bytes=release.installed_size_bytes,
                resumable=True,
                target_root=str(
                    self._managed_target(source.package_id, release.version)
                ),
                requested_bindings=(),
            )
        self._submit_install(operation, release, upgrade_source=source.instance_id)
        return self.operation(operation.operation_id)

    def upgrade(
        self,
        instance_id: str,
        version: str | None = None,
    ) -> RuntimePackageOperation:
        return self.upgrade_async(instance_id, version)

    def uninstall(self, instance_id: str) -> RuntimePackageOperation:
        instance = self._find_instance(instance_id)
        if instance.source is not RuntimePackageSource.MANAGED:
            raise RuntimePackageOwnershipError("only managed runtime packages can be uninstalled")
        with self._state_guard():
            config = self._read_config()
            self._assert_no_bindings(config, instance_id)
            self._assert_no_leases(instance_id)
            self._assert_no_active_instance_operation(instance_id)
            operation = self._new_operation(
                RuntimeOperationKind.UNINSTALL,
                package_id=instance.package_id,
                target_version=instance.version,
                instance_id=instance.instance_id,
            )
        cancel_event = threading.Event()
        self._cancel_events[operation.operation_id] = cancel_event
        self._futures[operation.operation_id] = self._executor.submit(
            self._run_uninstall_operation,
            operation.operation_id,
            instance,
            cancel_event,
        )
        return self.operation(operation.operation_id)

    def operation(self, operation_id: str) -> RuntimePackageOperation:
        path = self._operation_path(operation_id)
        with self._state_guard():
            if not path.is_file():
                raise RuntimePackageNotFoundError(
                    f"unknown runtime package operation: {operation_id}"
                )
            try:
                return RuntimePackageOperation.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as error:
                raise RuntimePackageOperationError(
                    f"runtime operation state is invalid: {type(error).__name__}"
                ) from None

    def cancel(self, operation_id: str) -> RuntimePackageOperation:
        current = self.operation(operation_id)
        if current.status not in _ACTIVE_OPERATION_STATUSES:
            return current
        event = self._cancel_events.get(operation_id)
        if event is not None:
            event.set()
        future = self._futures.get(operation_id)
        if future is not None and future.cancel():
            return self._update_operation(
                operation_id,
                status=RuntimeOperationStatus.CANCELLED,
                phase=RuntimeOperationPhase.CANCELLED,
                cancel_requested=True,
                finished_at_utc=_utc_now(),
                detail="Runtime operation was cancelled before it started.",
            )
        return self._update_operation(operation_id, cancel_requested=True)

    def wait(
        self,
        operation_id: str,
        *,
        timeout: float | None = None,
    ) -> RuntimePackageOperation:
        future = self._futures.get(operation_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.operation(operation_id)

    def _submit_install(
        self,
        operation: RuntimePackageOperation,
        release: RuntimePackageRelease,
        *,
        upgrade_source: str | None,
    ) -> None:
        cancel_event = threading.Event()
        self._cancel_events[operation.operation_id] = cancel_event
        self._futures[operation.operation_id] = self._executor.submit(
            self._run_install_operation,
            operation.operation_id,
            release,
            cancel_event,
            upgrade_source,
        )

    def _run_install_operation(
        self,
        operation_id: str,
        release: RuntimePackageRelease,
        cancel_event: threading.Event,
        upgrade_source: str | None,
    ) -> None:
        staging = self._staging_path(operation_id)
        archive = self._download_path(release.archive_sha256)
        requested_bindings = self.operation(operation_id).requested_bindings
        try:
            self._update_operation(
                operation_id,
                status=RuntimeOperationStatus.RUNNING,
                phase=RuntimeOperationPhase.DOWNLOADING,
                started_at_utc=_utc_now(),
                detail="Downloading the pinned runtime archive.",
            )
            if staging.exists():
                self._remove_managed_tree(staging, self.staging_root)
            staging.mkdir(parents=True)
            payload_root = staging / "payload"

            last_persisted = [0, 0.0]
            last_sample = [0.0, time.monotonic(), 0.0]

            def progress(downloaded: int, total: int | None) -> None:
                now = time.monotonic()
                if (
                    downloaded != (total or -1)
                    and downloaded - last_persisted[0] < 8 * 1024 * 1024
                    and now - last_persisted[1] < 0.25
                ):
                    return
                last_persisted[:] = [downloaded, now]
                denominator = total or release.archive_size_bytes
                ratio = min(downloaded / max(denominator, 1), 1.0)
                elapsed = max(now - last_sample[1], 0.0)
                speed: float | None = None
                if elapsed > 0 and downloaded >= last_sample[0]:
                    instantaneous = (downloaded - last_sample[0]) / elapsed
                    previous = last_sample[2]
                    speed = (
                        instantaneous
                        if previous <= 0
                        else previous * 0.65 + instantaneous * 0.35
                    )
                    last_sample[:] = [float(downloaded), now, speed]
                remaining = max(denominator - downloaded, 0)
                eta = remaining / speed if speed is not None and speed > 0 else None
                self._update_operation(
                    operation_id,
                    progress=ratio * 0.55,
                    downloaded_bytes=downloaded,
                    total_bytes=denominator,
                    transfer_speed_bytes_per_second=speed,
                    eta_seconds=eta,
                )

            result = self.downloader.download(
                release,
                archive,
                cancel_event=cancel_event,
                progress=progress,
            )
            self._raise_if_cancelled(cancel_event)
            self._verify_archive(result.archive_path, release)
            self._update_operation(
                operation_id,
                phase=RuntimeOperationPhase.VERIFYING_ARCHIVE,
                progress=0.58,
                downloaded_bytes=release.archive_size_bytes,
                transfer_speed_bytes_per_second=None,
                eta_seconds=None,
                detail="Verifying the pinned runtime archive.",
            )
            self._raise_if_cancelled(cancel_event)
            self._update_operation(
                operation_id,
                phase=RuntimeOperationPhase.EXTRACTING,
                progress=0.64,
                detail="Extracting the verified runtime archive.",
            )
            safe_extract_runtime_archive(result.archive_path, payload_root)
            self._raise_if_cancelled(cancel_event)
            validation = validate_runtime_release_root(payload_root, release)
            self._update_operation(
                operation_id,
                phase=RuntimeOperationPhase.PROBING,
                progress=0.84,
                detail="Starting the isolated runtime health check.",
            )
            self.prober.probe(payload_root, release)
            self._raise_if_cancelled(cancel_event)
            self._atomic_write_json(
                payload_root / RUNTIME_PACKAGE_INSTALL_MARKER,
                {
                    "schema_version": 1,
                    "package_id": release.package_id,
                    "version": release.version,
                    "manifest_sha256": validation.manifest_sha256,
                    "archive_sha256": release.archive_sha256,
                    "installed_at_utc": _utc_now(),
                },
            )
            self._update_operation(
                operation_id,
                phase=RuntimeOperationPhase.PUBLISHING,
                progress=0.90,
                detail="Publishing the verified runtime package.",
            )
            self._raise_if_cancelled(cancel_event)
            with self._state_guard():
                target = self._publish_managed(payload_root, release)
                target_instance_id = _managed_instance_id(
                    release.package_id,
                    release.version,
                )
                if upgrade_source is not None:
                    self._assert_no_leases(upgrade_source)
                    self._switch_upgrade_bindings(
                        upgrade_source,
                        target_instance_id,
                        target,
                        release.manifest,
                        validation.manifest_sha256,
                    )
                self._bind_install_requirements(
                    requested_bindings,
                    target_instance_id,
                    release.manifest,
                    validation.manifest_sha256,
                )
            archive.unlink(missing_ok=True)
            self._update_operation(
                operation_id,
                status=RuntimeOperationStatus.SUCCEEDED,
                phase=RuntimeOperationPhase.COMPLETED,
                progress=1.0,
                result_instance_id=target_instance_id,
                finished_at_utc=_utc_now(),
                detail="Runtime package is ready.",
            )
        except RuntimePackageDownloadCancelled:
            self._update_operation(
                operation_id,
                status=RuntimeOperationStatus.CANCELLED,
                phase=RuntimeOperationPhase.CANCELLED,
                cancel_requested=True,
                finished_at_utc=_utc_now(),
                detail="Runtime package operation was cancelled.",
            )
        except Exception as error:
            self._update_operation(
                operation_id,
                status=RuntimeOperationStatus.FAILED,
                phase=RuntimeOperationPhase.FAILED,
                finished_at_utc=_utc_now(),
                detail=_safe_error_detail(error),
                error_code=type(error).__name__,
            )
        finally:
            if staging.exists():
                with suppress(OSError, RuntimePackagePathError):
                    self._remove_managed_tree(staging, self.staging_root)

    def _run_uninstall_operation(
        self,
        operation_id: str,
        instance: RuntimePackageInstance,
        cancel_event: threading.Event,
    ) -> None:
        recovery: Path | None = None
        try:
            self._update_operation(
                operation_id,
                status=RuntimeOperationStatus.RUNNING,
                phase=RuntimeOperationPhase.REMOVING,
                started_at_utc=_utc_now(),
                progress=0.1,
                detail="Preparing managed runtime removal.",
            )
            self._raise_if_cancelled(cancel_event)
            target = Path(instance.root).resolve()
            with self._state_guard():
                config = self._read_config()
                self._assert_no_bindings(config, instance.instance_id)
                self._assert_no_leases(instance.instance_id)
                self._assert_managed_package_path(target)
                if not target.exists():
                    raise RuntimePackageNotFoundError("managed runtime package disappeared")
                recovery = self._recovery_path(
                    f"uninstall-{instance.package_id}-{instance.version}-{uuid.uuid4().hex}"
                )
                os.replace(target, recovery)
            self._update_operation(
                operation_id,
                progress=0.7,
                detail="Removing the detached runtime package.",
            )
            self._remove_managed_tree(recovery, self.recovery_root)
            recovery = None
            self._update_operation(
                operation_id,
                status=RuntimeOperationStatus.SUCCEEDED,
                phase=RuntimeOperationPhase.COMPLETED,
                progress=1.0,
                finished_at_utc=_utc_now(),
                detail="Managed runtime package was removed.",
            )
        except RuntimePackageDownloadCancelled:
            self._update_operation(
                operation_id,
                status=RuntimeOperationStatus.CANCELLED,
                phase=RuntimeOperationPhase.CANCELLED,
                cancel_requested=True,
                finished_at_utc=_utc_now(),
                detail="Runtime package removal was cancelled.",
            )
        except Exception as error:
            detail = _safe_error_detail(error)
            if recovery is not None and recovery.exists():
                detail = f"{detail} Detached data remains in managed recovery storage."
            self._update_operation(
                operation_id,
                status=RuntimeOperationStatus.FAILED,
                phase=RuntimeOperationPhase.FAILED,
                finished_at_utc=_utc_now(),
                detail=detail,
                error_code=type(error).__name__,
            )

    def _discover_instances(
        self,
        config: RuntimePackageConfig,
    ) -> tuple[RuntimePackageInstance, ...]:
        instances: list[RuntimePackageInstance] = []
        instances.extend(self._instance_from_candidate(item) for item in self._bundled_packages)
        instances.extend(self._discover_managed())
        instances.extend(self._instance_from_candidate(item) for item in self._system_packages)
        instances.extend(
            self._instance_from_registration(item)
            for item in config.custom_packages.values()
        )
        return tuple(instances)

    def _discover_managed(self) -> tuple[RuntimePackageInstance, ...]:
        instances: list[RuntimePackageInstance] = []
        for package_directory in sorted(self.managed_root.iterdir()):
            if package_directory.name.startswith("."):
                continue
            if _is_link(package_directory) or not package_directory.is_dir():
                instances.append(
                    self._invalid_instance(
                        RuntimePackageSource.MANAGED,
                        package_directory,
                        package_id=package_directory.name,
                        detail="Managed runtime package directory is not regular.",
                    )
                )
                continue
            for version_directory in sorted(package_directory.iterdir()):
                if version_directory.name.startswith("."):
                    continue
                instances.append(
                    self._instance_from_root(
                        RuntimePackageSource.MANAGED,
                        version_directory,
                    )
                )
        return tuple(instances)

    def _instance_from_candidate(
        self,
        candidate: RuntimePackageCandidate,
    ) -> RuntimePackageInstance:
        root = Path(candidate.root).expanduser().resolve()
        if candidate.manifest is not None and candidate.source is RuntimePackageSource.BUNDLED:
            manifest_path = root / RUNTIME_PACKAGE_MANIFEST
            if not manifest_path.is_file():
                return self._synthetic_bundled_instance(root, candidate.manifest)
        return self._instance_from_root(
            candidate.source,
            root,
            expected_manifest=candidate.manifest,
        )

    def _instance_from_registration(
        self,
        registration: RuntimeCustomRegistration,
    ) -> RuntimePackageInstance:
        root = Path(registration.root).expanduser().resolve()
        if not root.is_dir():
            return RuntimePackageInstance(
                instance_id=registration.instance_id,
                package_id=registration.package_id,
                version=registration.version,
                display_name=registration.package_id,
                source=RuntimePackageSource.CUSTOM,
                root=str(root),
                state=RuntimePackageState.MISSING,
                ready=False,
                detail="Registered custom runtime package is missing.",
                manifest_sha256=registration.manifest_sha256,
            )
        return self._instance_from_root(
            RuntimePackageSource.CUSTOM,
            root,
            expected_registration=registration,
        )

    def _instance_from_root(
        self,
        source: RuntimePackageSource,
        root: Path,
        *,
        expected_manifest: RuntimePackageManifest | None = None,
        expected_registration: RuntimeCustomRegistration | None = None,
    ) -> RuntimePackageInstance:
        try:
            validation = validate_runtime_package_root(
                root,
                expected_manifest=expected_manifest,
                full_hash=False,
            )
            if (
                expected_registration is not None
                and validation.manifest_sha256 != expected_registration.manifest_sha256
            ):
                raise RuntimePackageIntegrityError(
                    "registered custom runtime manifest changed"
                )
            if source is RuntimePackageSource.MANAGED:
                self._validate_install_marker(root, validation.manifest, validation.manifest_sha256)
            compatible, compatibility_detail = _platform_compatible(validation.manifest)
            instance_id = _instance_id(source, validation.manifest, root)
            return RuntimePackageInstance(
                instance_id=instance_id,
                package_id=validation.manifest.package_id,
                version=validation.manifest.version,
                display_name=validation.manifest.display_name,
                source=source,
                root=str(root),
                state=(RuntimePackageState.READY if compatible else RuntimePackageState.DEGRADED),
                ready=compatible,
                detail=compatibility_detail,
                manifest_sha256=validation.manifest_sha256,
                target_triple=validation.manifest.target_triple,
                runtime_protocol_version=validation.manifest.runtime_protocol_version,
                transport=_manifest_transport(validation.manifest),
                capabilities=validation.manifest.capability_ids,
            )
        except (OSError, RuntimePackageIntegrityError, ValueError) as error:
            package_id = (
                expected_registration.package_id
                if expected_registration is not None
                else expected_manifest.package_id
                if expected_manifest is not None
                else root.parent.name
                if source is RuntimePackageSource.MANAGED
                else root.name
            )
            version = (
                expected_registration.version
                if expected_registration is not None
                else expected_manifest.version
                if expected_manifest is not None
                else root.name
                if source is RuntimePackageSource.MANAGED
                else "unknown"
            )
            instance_id = (
                expected_registration.instance_id
                if expected_registration is not None
                else _unknown_instance_id(source, root)
            )
            return RuntimePackageInstance(
                instance_id=instance_id,
                package_id=_safe_inventory_id(package_id),
                version=_safe_inventory_version(version),
                display_name=package_id or "Invalid runtime package",
                source=source,
                root=str(root),
                state=(
                    RuntimePackageState.MISSING
                    if not root.exists()
                    else RuntimePackageState.INVALID
                ),
                ready=False,
                detail=_safe_error_detail(error),
                manifest_sha256=(
                    expected_registration.manifest_sha256
                    if expected_registration is not None
                    else None
                ),
            )

    def _synthetic_bundled_instance(
        self,
        root: Path,
        manifest: RuntimePackageManifest,
    ) -> RuntimePackageInstance:
        try:
            if not root.is_dir() or _is_link(root):
                raise RuntimePackageIntegrityError("bundled runtime root is unavailable")
            for item in manifest.payload:
                path = (root / item.path.replace("/", os.sep)).resolve()
                if (
                    not path.is_relative_to(root)
                    or not path.is_file()
                    or _is_link(path)
                    or path.stat().st_size != item.size_bytes
                ):
                    raise RuntimePackageIntegrityError(
                        f"bundled runtime payload is missing: {item.path}"
                    )
            compatible, detail = _platform_compatible(manifest)
            return RuntimePackageInstance(
                instance_id=_instance_id(RuntimePackageSource.BUNDLED, manifest, root),
                package_id=manifest.package_id,
                version=manifest.version,
                display_name=manifest.display_name,
                source=RuntimePackageSource.BUNDLED,
                root=str(root),
                state=(RuntimePackageState.READY if compatible else RuntimePackageState.DEGRADED),
                ready=compatible,
                detail=detail,
                manifest_sha256=runtime_manifest_sha256(manifest),
                target_triple=manifest.target_triple,
                runtime_protocol_version=manifest.runtime_protocol_version,
                transport=_manifest_transport(manifest),
                capabilities=manifest.capability_ids,
            )
        except (OSError, RuntimePackageIntegrityError) as error:
            return self._invalid_instance(
                RuntimePackageSource.BUNDLED,
                root,
                package_id=manifest.package_id,
                version=manifest.version,
                detail=_safe_error_detail(error),
            )

    def _invalid_instance(
        self,
        source: RuntimePackageSource,
        root: Path,
        *,
        package_id: str,
        version: str = "unknown",
        detail: str,
    ) -> RuntimePackageInstance:
        return RuntimePackageInstance(
            instance_id=_unknown_instance_id(source, root),
            package_id=_safe_inventory_id(package_id),
            version=_safe_inventory_version(version),
            display_name=package_id or "Invalid runtime package",
            source=source,
            root=str(root),
            state=RuntimePackageState.INVALID,
            ready=False,
            detail=detail,
        )

    def _managed_instance(
        self,
        package_id: str,
        version: str,
    ) -> RuntimePackageInstance | None:
        target = self._managed_target(package_id, version)
        if not target.exists():
            return None
        return self._instance_from_root(RuntimePackageSource.MANAGED, target)

    def _find_instance(self, instance_id: str) -> RuntimePackageInstance:
        instance = next(
            (item for item in self.inventory().instances if item.instance_id == instance_id),
            None,
        )
        if instance is None:
            raise RuntimePackageNotFoundError(f"unknown runtime instance: {instance_id}")
        return instance

    def _publish_managed(
        self,
        staging: Path,
        release: RuntimePackageRelease,
    ) -> Path:
        target = self._managed_target(release.package_id, release.version)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = self._instance_from_root(RuntimePackageSource.MANAGED, target)
            if existing.ready and existing.manifest_sha256 == runtime_manifest_sha256(
                release.manifest
            ):
                return target
            recovery = self._recovery_path(
                f"replaced-{release.package_id}-{release.version}-{uuid.uuid4().hex}"
            )
            os.replace(target, recovery)
        os.replace(staging, target)
        published = self._instance_from_root(RuntimePackageSource.MANAGED, target)
        if not published.ready:
            raise RuntimePackageIntegrityError(
                published.detail or "published runtime package is invalid"
            )
        return target

    def _switch_upgrade_bindings(
        self,
        source_instance_id: str,
        target_instance_id: str,
        target_root: Path,
        manifest: RuntimePackageManifest,
        manifest_sha256: str,
    ) -> None:
        config = self._read_config()
        updates = dict(config.bindings)
        capabilities = set(manifest.capability_ids)
        for requirement_id, binding in config.bindings.items():
            if binding.instance_id != source_instance_id:
                continue
            if binding.capability_id not in capabilities:
                raise RuntimePackageBindingError(
                    "upgrade target no longer provides a bound capability"
                )
            updates[requirement_id] = binding.model_copy(
                update={
                    "instance_id": target_instance_id,
                    "package_version": manifest.version,
                    "manifest_sha256": manifest_sha256,
                    "bound_at_utc": _utc_now(),
                }
            )
        del target_root  # The binding stores stable identity, never a machine path.
        self._write_config(config.model_copy(update={"bindings": updates}))

    def _bind_install_requirements(
        self,
        requirements: Sequence[str],
        instance_id: str,
        manifest: RuntimePackageManifest,
        manifest_sha256: str,
    ) -> None:
        selected = self._binding_capabilities(manifest, requirements)
        if not selected:
            return
        config = self._read_config()
        bindings = dict(config.bindings)
        for requirement_id, capability_id in selected.items():
            bindings[requirement_id] = RuntimeBinding(
                requirement_id=requirement_id,
                capability_id=capability_id,
                instance_id=instance_id,
                package_id=manifest.package_id,
                package_version=manifest.version,
                source=RuntimePackageSource.MANAGED,
                manifest_sha256=manifest_sha256,
                bound_at_utc=_utc_now(),
            )
        self._write_config(config.model_copy(update={"bindings": bindings}))

    @staticmethod
    def _binding_capabilities(
        manifest: RuntimePackageManifest,
        requirements: Sequence[str],
    ) -> dict[str, str]:
        capabilities = set(manifest.capability_ids)
        selected: dict[str, str] = {}
        for requirement_id in requirements:
            if requirement_id in capabilities:
                selected[requirement_id] = requirement_id
            elif len(capabilities) == 1:
                selected[requirement_id] = next(iter(capabilities))
            else:
                raise RuntimePackageBindingError(
                    f"runtime package cannot infer a capability for {requirement_id}"
                )
        return selected

    def _validate_install_marker(
        self,
        root: Path,
        manifest: RuntimePackageManifest,
        manifest_sha256: str,
    ) -> None:
        marker = root / RUNTIME_PACKAGE_INSTALL_MARKER
        if not marker.is_file() or _is_link(marker):
            raise RuntimePackageIntegrityError("managed runtime install marker is missing")
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise RuntimePackageIntegrityError(
                "managed runtime install marker is invalid"
            ) from None
        if (
            payload.get("schema_version") != 1
            or payload.get("package_id") != manifest.package_id
            or payload.get("version") != manifest.version
            or payload.get("manifest_sha256") != manifest_sha256
            or not isinstance(payload.get("archive_sha256"), str)
        ):
            raise RuntimePackageIntegrityError("managed runtime install identity changed")

    def _new_operation(
        self,
        kind: RuntimeOperationKind,
        *,
        package_id: str,
        target_version: str | None = None,
        instance_id: str | None = None,
        source_instance_id: str | None = None,
        status: RuntimeOperationStatus = RuntimeOperationStatus.QUEUED,
        phase: RuntimeOperationPhase = RuntimeOperationPhase.QUEUED,
        progress: float = 0.0,
        total_bytes: int | None = None,
        expected_installed_bytes: int | None = None,
        resumable: bool = False,
        target_root: str | None = None,
        requested_bindings: tuple[str, ...] = (),
        result_instance_id: str | None = None,
        detail: str | None = None,
    ) -> RuntimePackageOperation:
        now = _utc_now()
        operation = RuntimePackageOperation(
            operation_id=uuid.uuid4().hex,
            kind=kind,
            package_id=package_id,
            target_version=target_version,
            instance_id=instance_id,
            source_instance_id=source_instance_id,
            status=status,
            phase=phase,
            progress=progress,
            total_bytes=total_bytes,
            expected_installed_bytes=expected_installed_bytes,
            resumable=resumable,
            target_root=target_root,
            requested_bindings=requested_bindings,
            created_at_utc=now,
            started_at_utc=now if status is RuntimeOperationStatus.SUCCEEDED else None,
            finished_at_utc=now if status is RuntimeOperationStatus.SUCCEEDED else None,
            owner_pid=os.getpid(),
            result_instance_id=result_instance_id,
            detail=detail,
        )
        self._atomic_write_json(
            self._operation_path(operation.operation_id),
            operation.model_dump(mode="json"),
        )
        return operation

    def _update_operation(
        self,
        operation_id: str,
        **updates: object,
    ) -> RuntimePackageOperation:
        with self._state_guard():
            path = self._operation_path(operation_id)
            if not path.is_file():
                raise RuntimePackageOperationError("runtime operation state disappeared")
            current = RuntimePackageOperation.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            updated = current.model_copy(update=updates)
            self._atomic_write_json(path, updated.model_dump(mode="json"))
            return updated

    def _read_operations(self) -> tuple[RuntimePackageOperation, ...]:
        operations: list[RuntimePackageOperation] = []
        for path in self.operation_root.glob("*.json"):
            if _is_link(path) or not path.is_file():
                continue
            try:
                operations.append(
                    RuntimePackageOperation.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, ValueError):
                continue
        operations.sort(key=lambda item: (item.created_at_utc, item.operation_id))
        return tuple(operations)

    def _active_release_operation(
        self,
        kind: RuntimeOperationKind,
        package_id: str,
        version: str,
    ) -> RuntimePackageOperation | None:
        return next(
            (
                item
                for item in self._read_operations()
                if item.kind is kind
                and item.package_id == package_id
                and item.target_version == version
                and item.status in _ACTIVE_OPERATION_STATUSES
            ),
            None,
        )

    def _assert_no_bindings(
        self,
        config: RuntimePackageConfig,
        instance_id: str,
    ) -> None:
        if any(item.instance_id == instance_id for item in config.bindings.values()):
            raise RuntimePackageBusyError("bound runtime packages cannot be removed")

    def _assert_no_leases(self, instance_id: str) -> None:
        if any(item.instance_id == instance_id for item in self._read_active_leases()):
            raise RuntimePackageBusyError("leased runtime packages cannot be changed")

    def _assert_no_active_instance_operation(self, instance_id: str) -> None:
        if any(
            item.status in _ACTIVE_OPERATION_STATUSES
            and instance_id in {item.instance_id, item.source_instance_id}
            for item in self._read_operations()
        ):
            raise RuntimePackageBusyError("runtime package already has an active operation")

    def _read_active_leases(self) -> tuple[RuntimePackageLease, ...]:
        now = datetime.now(UTC)
        active: list[RuntimePackageLease] = []
        for path in self.lease_root.glob("*.json"):
            if _is_link(path) or not path.is_file():
                continue
            try:
                lease = RuntimePackageLease.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                expires = (
                    datetime.fromisoformat(lease.expires_at_utc)
                    if lease.expires_at_utc is not None
                    else None
                )
            except (OSError, ValueError):
                continue
            if expires is not None and expires <= now:
                path.unlink(missing_ok=True)
                continue
            active.append(lease)
        return tuple(active)

    def _read_config(self) -> RuntimePackageConfig:
        try:
            return RuntimePackageConfig.model_validate_json(
                self.config_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return RuntimePackageConfig()
        except (OSError, ValueError) as error:
            raise RuntimePackageManagerError(
                f"runtime package config is invalid: {type(error).__name__}"
            ) from None

    def _write_config(self, config: RuntimePackageConfig) -> None:
        self._atomic_write_json(self.config_path, config.model_dump(mode="json"))

    def _verify_archive(self, archive: Path, release: RuntimePackageRelease) -> None:
        if not archive.is_file() or archive.stat().st_size != release.archive_size_bytes:
            raise RuntimePackageIntegrityError("runtime archive size is not trusted")
        if _sha256_file(archive) != release.archive_sha256:
            raise RuntimePackageIntegrityError("runtime archive SHA-256 is not trusted")

    def _raise_if_cancelled(self, event: threading.Event) -> None:
        if event.is_set():
            raise RuntimePackageDownloadCancelled("runtime package operation was cancelled")

    def _managed_target(self, package_id: str, version: str) -> Path:
        target = (self.managed_root / package_id / version).resolve()
        self._assert_managed_package_path(target)
        return target

    def _assert_managed_package_path(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.managed_root) or resolved == self.managed_root:
            raise RuntimePackagePathError("managed runtime path escaped app data")

    def _staging_path(self, operation_id: str) -> Path:
        return self._safe_child(self.staging_root, operation_id)

    def _recovery_path(self, name: str) -> Path:
        return self._safe_child(self.recovery_root, name)

    def _download_path(self, archive_sha256: str) -> Path:
        return self._safe_child(self.download_root, f"{archive_sha256}.zip")

    def _operation_path(self, operation_id: str) -> Path:
        if not operation_id or any(
            character not in "0123456789abcdef" for character in operation_id
        ):
            raise RuntimePackagePathError("runtime operation ID is invalid")
        return self._safe_child(self.operation_root, f"{operation_id}.json")

    def _lease_path(self, lease_id: str) -> Path:
        if not lease_id or any(character not in "0123456789abcdef" for character in lease_id):
            raise RuntimePackagePathError("runtime lease ID is invalid")
        return self._safe_child(self.lease_root, f"{lease_id}.json")

    @staticmethod
    def _safe_child(root: Path, name: str) -> Path:
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path == root:
            raise RuntimePackagePathError("runtime package path escaped its managed root")
        return path

    def _ensure_managed_directory(self, path: Path) -> None:
        if not path.is_relative_to(self.data_root):
            raise RuntimePackagePathError("runtime package directory escaped app data")
        path.mkdir(parents=True, exist_ok=True)
        current = path
        while current != self.data_root:
            if _is_link(current):
                raise RuntimePackagePathError("runtime package storage cannot contain links")
            current = current.parent

    def _remove_managed_tree(self, path: Path, allowed_root: Path) -> None:
        resolved = path.resolve()
        if (
            resolved == allowed_root
            or not resolved.is_relative_to(allowed_root)
            or _is_link(path)
        ):
            raise RuntimePackagePathError("refusing to remove an unsafe runtime package path")
        shutil.rmtree(resolved)

    @contextmanager
    def _state_guard(self) -> Iterator[None]:
        with self._thread_lock, _InterProcessFileLock(self.lock_path):
            yield

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)


class _InterProcessFileLock:
    """Small cross-platform advisory lock for shared DATA_ROOT mutations."""

    def __init__(self, path: Path, *, timeout_seconds: float = 30.0) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._stream: BinaryIO | None = None

    def __enter__(self) -> _InterProcessFileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                _lock_stream(stream)
                self._stream = stream
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    stream.close()
                    raise RuntimePackageBusyError(
                        "timed out waiting for runtime package state lock"
                    ) from None
                time.sleep(0.05)

    def __exit__(self, *_args: object) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            _unlock_stream(stream)
        finally:
            stream.close()


def _lock_stream(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(  # type: ignore[attr-defined]
            stream.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
        )


def _unlock_stream(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(  # type: ignore[attr-defined]
            stream.fileno(),
            fcntl.LOCK_UN,  # type: ignore[attr-defined]
        )


def _instance_id(
    source: RuntimePackageSource,
    manifest: RuntimePackageManifest,
    root: Path,
) -> str:
    if source is RuntimePackageSource.MANAGED:
        return _managed_instance_id(manifest.package_id, manifest.version)
    root_hash = hashlib.sha256(str(root.resolve()).casefold().encode("utf-8")).hexdigest()[:16]
    return f"{source.value}:{manifest.package_id}:{manifest.version}:{root_hash}"


def _managed_instance_id(package_id: str, version: str) -> str:
    return f"managed:{package_id}:{version}"


def _unknown_instance_id(source: RuntimePackageSource, root: Path) -> str:
    root_hash = hashlib.sha256(str(root.resolve()).casefold().encode("utf-8")).hexdigest()[:16]
    return f"{source.value}:invalid:{root_hash}"


def _safe_inventory_id(value: str) -> str:
    normalized = "".join(
        character if character.isascii() and (character.isalnum() or character == "-") else "-"
        for character in value.lower()
    ).strip("-")
    if not normalized or not normalized[0].isalpha():
        normalized = f"invalid-{normalized}".strip("-")
    return (normalized or "invalid-package")[:64]


def _safe_inventory_version(value: str) -> str:
    normalized = "".join(
        character if character.isascii() and (character.isalnum() or character in "._-") else "-"
        for character in value
    ).strip("-._")
    return (normalized or "unknown")[:64]


def _platform_compatible(manifest: RuntimePackageManifest) -> tuple[bool, str | None]:
    target = manifest.target_triple.casefold()
    current_system = platform.system().casefold()
    required_system = (
        "windows"
        if "windows" in target
        else "linux"
        if "linux" in target
        else "darwin"
        if "darwin" in target or "apple" in target
        else None
    )
    if required_system is not None and current_system != required_system:
        return False, f"Runtime package target {manifest.target_triple} is incompatible."
    current_architecture = platform.machine().casefold()
    x64_aliases = {"amd64", "x86_64", "x64"}
    arm64_aliases = {"arm64", "aarch64"}
    if target.startswith(("x86_64", "amd64")) and current_architecture not in x64_aliases:
        return False, f"Runtime package target {manifest.target_triple} is incompatible."
    if target.startswith(("aarch64", "arm64")) and current_architecture not in arm64_aliases:
        return False, f"Runtime package target {manifest.target_triple} is incompatible."
    return True, None


def _manifest_transport(manifest: RuntimePackageManifest) -> RuntimeTransport | None:
    transports = {item.transport for item in manifest.capabilities}
    return next(iter(transports)) if len(transports) == 1 else None


def _safe_error_detail(error: Exception) -> str:
    message = str(error).strip()
    if not message:
        return type(error).__name__
    return message[:500]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


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
