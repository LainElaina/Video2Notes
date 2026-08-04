from __future__ import annotations

import hashlib
import json
import platform
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import dataclass
from pathlib import Path

from video2notes.components.runtime_catalog import RuntimePackageCatalog
from video2notes.components.runtime_downloaders import (
    RuntimeDownloadResult,
    RuntimePackageArchiveError,
    RuntimePackageDownloadCancelled,
    safe_extract_runtime_archive,
)
from video2notes.components.runtime_manager import (
    RuntimePackageBindingError,
    RuntimePackageBusyError,
    RuntimePackageManager,
    RuntimePackageOperationError,
    RuntimePackageOwnershipError,
)
from video2notes.components.runtime_models import (
    RUNTIME_PACKAGE_MANIFEST,
    RuntimeBindingSnapshot,
    RuntimeOperationStatus,
    RuntimePackageCandidate,
    RuntimePackageManifest,
    RuntimePackageRelease,
    RuntimePackageSource,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def host_target_triple() -> str:
    machine = platform.machine().casefold()
    architecture = "aarch64" if machine in {"aarch64", "arm64"} else "x86_64"
    system = platform.system().casefold()
    if system == "windows":
        return f"{architecture}-pc-windows-msvc"
    if system == "darwin":
        return f"{architecture}-apple-darwin"
    return f"{architecture}-unknown-linux-gnu"


def incompatible_target_triple() -> str:
    architecture = host_target_triple().split("-", maxsplit=1)[0]
    if platform.system().casefold() == "windows":
        return f"{architecture}-unknown-linux-gnu"
    return f"{architecture}-pc-windows-msvc"


@dataclass(frozen=True, slots=True)
class PackageArtifact:
    root: Path
    archive: Path
    release: RuntimePackageRelease


def build_package(
    parent: Path,
    *,
    package_id: str = "local-inference-test",
    version: str = "1.0.0",
    target_triple: str | None = None,
    untrusted_extra_file: bool = False,
) -> PackageArtifact:
    package_root = parent / f"{package_id}-{version}-tree"
    license_path = package_root / "licenses" / "THIRD_PARTY_NOTICES.md"
    worker_path = package_root / "runtime-worker.exe"
    license_path.parent.mkdir(parents=True)
    license_path.write_text("test notice\n", encoding="utf-8")
    worker_path.write_bytes(f"worker-{version}".encode("ascii"))

    payload_paths = (license_path, worker_path)
    payload_files = tuple(
        {
            "relative_path": path.relative_to(package_root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in sorted(payload_paths)
    )
    manifest = RuntimePackageManifest.model_validate(
        {
            "schema": 1,
            "package_id": package_id,
            "version": version,
            "display_name": f"Test runtime {version}",
            "target_triple": target_triple or host_target_triple(),
            "runtime_protocol_version": 1,
            "capabilities": [
                {
                    "capability_id": "asr.faster_whisper",
                    "engine_id": "test-worker",
                    "protocol_version": 1,
                    "transport": "worker",
                    "entrypoint": "runtime-worker.exe",
                    "supported_devices": ["cpu"],
                }
            ],
            "licenses": [
                {
                    "name": "Test notices",
                    "relative_path": "licenses/THIRD_PARTY_NOTICES.md",
                }
            ],
            "upstream_sources": ["https://example.invalid/test-worker"],
            "payload_size_bytes": sum(path.stat().st_size for path in payload_paths),
            "user_model_weights_included": False,
            "files": payload_files,
        }
    )
    manifest_path = package_root / RUNTIME_PACKAGE_MANIFEST
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    installed_paths = tuple(sorted(path for path in package_root.rglob("*") if path.is_file()))
    installed_files = tuple(
        {
            "relative_path": path.relative_to(package_root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in installed_paths
    )
    archive = parent / f"{package_id}-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in installed_paths:
            package.write(path, path.relative_to(package_root).as_posix())
        if untrusted_extra_file:
            package.writestr("untrusted.dll", b"not in catalog")

    release = RuntimePackageRelease.model_validate(
        {
            "schema": 1,
            "package_id": package_id,
            "version": version,
            "display_name": manifest.display_name,
            "target_triple": manifest.target_triple,
            "runtime_protocol_version": manifest.runtime_protocol_version,
            "capabilities": [item.model_dump(mode="json") for item in manifest.capabilities],
            "archive": {
                "file_name": archive.name,
                "source_url": None,
                "size_bytes": archive.stat().st_size,
                "sha256": file_sha256(archive),
                "offline_only": True,
            },
            "installed_size_bytes": sum(path.stat().st_size for path in installed_paths),
            "files": installed_files,
            "licenses": [item.model_dump(mode="json") for item in manifest.licenses],
            "upstream_sources": list(manifest.upstream_sources),
        }
    )
    return PackageArtifact(root=package_root, archive=archive, release=release)


class ArchiveDownloader:
    def __init__(self, artifacts: tuple[PackageArtifact, ...]) -> None:
        self.archives = {
            (item.release.package_id, item.release.version): item.archive for item in artifacts
        }
        self.calls: list[tuple[str, str]] = []

    def download(
        self,
        release: RuntimePackageRelease,
        destination: Path,
        *,
        cancel_event: threading.Event,
        progress: object,
    ) -> RuntimeDownloadResult:
        del progress
        if cancel_event.is_set():
            raise RuntimePackageDownloadCancelled("cancelled")
        self.calls.append((release.package_id, release.version))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.archives[(release.package_id, release.version)], destination)
        return RuntimeDownloadResult(
            archive_path=destination,
            downloaded_bytes=destination.stat().st_size,
            resumed=False,
            reused=False,
        )


class BlockingDownloader(ArchiveDownloader):
    def __init__(self, artifact: PackageArtifact) -> None:
        super().__init__((artifact,))
        self.started = threading.Event()

    def download(
        self,
        release: RuntimePackageRelease,
        destination: Path,
        *,
        cancel_event: threading.Event,
        progress: object,
    ) -> RuntimeDownloadResult:
        del release, destination, progress
        self.started.set()
        while not cancel_event.wait(0.01):
            time.sleep(0.001)
        raise RuntimePackageDownloadCancelled("cancelled")


class AllowingProber:
    def probe(self, root: Path, release: RuntimePackageRelease) -> None:
        self.last_root = root
        self.last_release = release


class RejectingProber:
    def probe(self, root: Path, release: RuntimePackageRelease) -> None:
        del root, release
        raise RuntimeError("worker failed to start")


class RuntimePackageManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "data"
        self.artifacts_root = self.root / "artifacts"
        self.artifacts_root.mkdir()
        self.managers: list[RuntimePackageManager] = []

    def tearDown(self) -> None:
        for manager in reversed(self.managers):
            manager.close()

    def manager(
        self,
        artifacts: tuple[PackageArtifact, ...] = (),
        *,
        downloader: object | None = None,
        bundled: tuple[RuntimePackageCandidate, ...] = (),
        system: tuple[RuntimePackageCandidate, ...] = (),
        prober: object | None = None,
    ) -> RuntimePackageManager:
        manager = RuntimePackageManager(
            self.data_root,
            catalog=RuntimePackageCatalog(
                packages=tuple(item.release for item in artifacts)
            ),
            downloader=downloader or ArchiveDownloader(artifacts),  # type: ignore[arg-type]
            prober=prober or AllowingProber(),  # type: ignore[arg-type]
            bundled_packages=bundled,
            system_packages=system,
        )
        self.managers.append(manager)
        return manager

    def test_inventory_supports_all_sources_and_persists_custom_binding(self) -> None:
        artifact = build_package(self.artifacts_root)
        bundled_root = self.root / "bundled"
        system_root = self.root / "system"
        custom_root = self.root / "custom"
        shutil.copytree(artifact.root, bundled_root)
        shutil.copytree(artifact.root, system_root)
        shutil.copytree(artifact.root, custom_root)
        bundled = RuntimePackageCandidate(
            source=RuntimePackageSource.BUNDLED,
            root=str(bundled_root),
        )
        system = RuntimePackageCandidate(
            source=RuntimePackageSource.SYSTEM,
            root=str(system_root),
        )
        manager = self.manager(bundled=(bundled,), system=(system,))

        custom = manager.register_custom(custom_root / RUNTIME_PACKAGE_MANIFEST)
        binding = manager.bind("asr.default", custom.instance_id, "asr.faster_whisper")
        inventory = manager.inventory()

        self.assertEqual(
            {item.source for item in inventory.instances},
            {
                RuntimePackageSource.BUNDLED,
                RuntimePackageSource.SYSTEM,
                RuntimePackageSource.CUSTOM,
            },
        )
        self.assertEqual(manager.resolve("asr.default").instance_id, custom.instance_id)
        self.assertEqual(binding.capability_id, "asr.faster_whisper")
        self.assertTrue(manager.config_path.is_file())

        manager.close()
        reopened = self.manager(bundled=(bundled,), system=(system,))
        self.assertEqual(reopened.resolve("asr.default").instance_id, custom.instance_id)
        self.assertTrue(reopened.unbind("asr.default"))
        self.assertTrue(reopened.forget_custom(custom.instance_id))
        self.assertTrue(custom_root.is_dir())
        system_instance = next(
            item
            for item in reopened.inventory().instances
            if item.source is RuntimePackageSource.SYSTEM
        )
        with self.assertRaises(RuntimePackageOwnershipError):
            reopened.uninstall(system_instance.instance_id)

    def test_async_install_is_atomic_and_operation_survives_reopen(self) -> None:
        artifact = build_package(self.artifacts_root)
        downloader = ArchiveDownloader((artifact,))
        manager = self.manager((artifact,), downloader=downloader)

        operation = manager.install_async(artifact.release.package_id, artifact.release.version)
        finished = manager.wait(operation.operation_id, timeout=5)
        managed = next(
            item
            for item in manager.inventory().instances
            if item.source is RuntimePackageSource.MANAGED
        )

        self.assertEqual(finished.status, RuntimeOperationStatus.SUCCEEDED)
        self.assertEqual(finished.phase, "completed")
        self.assertEqual(
            finished.expected_installed_bytes,
            artifact.release.installed_size_bytes,
        )
        self.assertTrue(finished.resumable)
        self.assertEqual(finished.target_root, managed.root)
        self.assertEqual(finished.result_instance_id, managed.instance_id)
        self.assertTrue(Path(managed.root, RUNTIME_PACKAGE_MANIFEST).is_file())
        self.assertEqual(downloader.calls, [(artifact.release.package_id, "1.0.0")])
        self.assertFalse(any(manager.staging_root.iterdir()))

        manager.close()
        reopened = self.manager((artifact,))
        self.assertEqual(
            reopened.operation(operation.operation_id).status,
            RuntimeOperationStatus.SUCCEEDED,
        )

    def test_inventory_hides_incompatible_catalog_releases(self) -> None:
        installed_release = build_package(
            self.artifacts_root,
            version="1.0.0",
        )
        latest_compatible = build_package(
            self.artifacts_root,
            version="2.0.0",
        )
        incompatible = build_package(
            self.artifacts_root,
            version="99.0.0",
            target_triple=incompatible_target_triple(),
        )
        manager = self.manager((incompatible, installed_release, latest_compatible))
        installed = manager.wait(
            manager.install_async(
                installed_release.release.package_id,
                installed_release.release.version,
            ).operation_id,
            timeout=5,
        )
        assert installed.result_instance_id is not None

        inventory = manager.inventory()
        available = {
            (item.package_id, item.version)
            for item in inventory.available_releases
        }
        managed = next(
            item for item in inventory.instances if item.instance_id == installed.result_instance_id
        )

        self.assertIn(
            (latest_compatible.release.package_id, latest_compatible.release.version),
            available,
        )
        self.assertNotIn(
            (incompatible.release.package_id, incompatible.release.version),
            available,
        )
        self.assertEqual(managed.available_version, latest_compatible.release.version)

    def test_install_rejects_incompatible_release_before_download(self) -> None:
        artifact = build_package(
            self.artifacts_root,
            target_triple=incompatible_target_triple(),
        )
        downloader = ArchiveDownloader((artifact,))
        manager = self.manager((artifact,), downloader=downloader)

        with self.assertRaisesRegex(RuntimePackageOperationError, "incompatible"):
            manager.install_async(artifact.release.package_id, artifact.release.version)

        self.assertEqual(downloader.calls, [])
        self.assertEqual(manager.inventory().operations, ())
        self.assertFalse(any(manager.managed_root.rglob(RUNTIME_PACKAGE_MANIFEST)))

    def test_install_can_bind_and_snapshot_leases_are_atomic(self) -> None:
        artifact = build_package(self.artifacts_root)
        manager = self.manager((artifact,))

        finished = manager.wait(
            manager.install_async(
                artifact.release.package_id,
                bind_requirements=("asr.faster_whisper",),
            ).operation_id,
            timeout=5,
        )
        assert finished.result_instance_id is not None
        binding = manager.inventory().bindings["asr.faster_whisper"]
        snapshot = RuntimeBindingSnapshot(
            requirement_id=binding.requirement_id,
            capability_id=binding.capability_id,
            instance_id=binding.instance_id,
            source=binding.source,
            manifest_sha256=binding.manifest_sha256,
        )
        leases = manager.acquire_snapshot_leases(
            {snapshot.requirement_id: snapshot},
            owner="test-job",
        )

        self.assertEqual(len(leases), 1)
        self.assertTrue(manager.get_instance(binding.instance_id).ready)
        manager.unbind(binding.requirement_id)
        with self.assertRaises(RuntimePackageBusyError):
            manager.uninstall(binding.instance_id)
        manager.release_leases(leases)
        removed = manager.wait(manager.uninstall(binding.instance_id).operation_id, timeout=5)
        self.assertEqual(removed.status, RuntimeOperationStatus.SUCCEEDED)

    def test_archive_with_file_outside_catalog_never_publishes(self) -> None:
        artifact = build_package(self.artifacts_root, untrusted_extra_file=True)
        manager = self.manager((artifact,))

        operation = manager.install_async(artifact.release.package_id)
        finished = manager.wait(operation.operation_id, timeout=5)

        self.assertEqual(finished.status, RuntimeOperationStatus.FAILED)
        self.assertFalse(any(manager.managed_root.rglob(RUNTIME_PACKAGE_MANIFEST)))

    def test_worker_probe_failure_never_publishes(self) -> None:
        artifact = build_package(self.artifacts_root)
        manager = self.manager((artifact,), prober=RejectingProber())

        operation = manager.install_async(artifact.release.package_id)
        finished = manager.wait(operation.operation_id, timeout=5)

        self.assertEqual(finished.status, RuntimeOperationStatus.FAILED)
        self.assertEqual(finished.phase, "failed")
        self.assertFalse(any(manager.managed_root.rglob(RUNTIME_PACKAGE_MANIFEST)))

    def test_zip_path_escape_is_rejected_before_writing_outside(self) -> None:
        archive = self.root / "unsafe.zip"
        destination = self.root / "extract"
        outside = self.root / "outside.txt"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("../outside.txt", b"escape")
            package.writestr(RUNTIME_PACKAGE_MANIFEST, json.dumps({"schema": 1}))

        with self.assertRaises(RuntimePackageArchiveError):
            safe_extract_runtime_archive(archive, destination)

        self.assertFalse(outside.exists())

    def test_running_install_can_be_cancelled_and_persists_cancelled_state(self) -> None:
        artifact = build_package(self.artifacts_root)
        downloader = BlockingDownloader(artifact)
        manager = self.manager((artifact,), downloader=downloader)

        operation = manager.install_async(artifact.release.package_id)
        self.assertTrue(downloader.started.wait(timeout=2))
        manager.cancel(operation.operation_id)
        finished = manager.wait(operation.operation_id, timeout=5)

        self.assertEqual(finished.status, RuntimeOperationStatus.CANCELLED)
        self.assertTrue(finished.cancel_requested)
        self.assertFalse(any(manager.managed_root.rglob(RUNTIME_PACKAGE_MANIFEST)))

    def test_upgrade_switches_bindings_and_uninstall_honors_binding_and_lease(self) -> None:
        first = build_package(self.artifacts_root, version="1.0.0")
        second = build_package(self.artifacts_root, version="2.0.0")
        manager = self.manager((first, second))
        installed = manager.wait(
            manager.install_async(first.release.package_id, first.release.version).operation_id,
            timeout=5,
        )
        assert installed.result_instance_id is not None
        manager.bind(
            "asr.default",
            installed.result_instance_id,
            "asr.faster_whisper",
        )

        with manager.lease(
            installed.result_instance_id, owner="test-job"
        ), self.assertRaises(RuntimePackageBusyError):
            manager.upgrade_async(installed.result_instance_id, second.release.version)

        upgraded = manager.wait(
            manager.upgrade_async(
                installed.result_instance_id,
                second.release.version,
            ).operation_id,
            timeout=5,
        )
        assert upgraded.result_instance_id is not None
        self.assertEqual(manager.resolve("asr.default").version, "2.0.0")

        with self.assertRaises(RuntimePackageBusyError):
            manager.uninstall(upgraded.result_instance_id)
        manager.unbind("asr.default")
        lease = manager.acquire_lease(upgraded.result_instance_id, owner="test-job")
        with self.assertRaises(RuntimePackageBusyError):
            manager.uninstall(upgraded.result_instance_id)
        manager.release_lease(lease.lease_id)

        removed = manager.wait(
            manager.uninstall(upgraded.result_instance_id).operation_id,
            timeout=5,
        )
        self.assertEqual(removed.status, RuntimeOperationStatus.SUCCEEDED)
        self.assertNotIn(
            upgraded.result_instance_id,
            {item.instance_id for item in manager.inventory().instances},
        )

    def test_upgrade_rejects_incompatible_release_before_download(self) -> None:
        first = build_package(self.artifacts_root, version="1.0.0")
        second = build_package(
            self.artifacts_root,
            version="2.0.0",
            target_triple=incompatible_target_triple(),
        )
        downloader = ArchiveDownloader((first, second))
        manager = self.manager((first, second), downloader=downloader)
        installed = manager.wait(
            manager.install_async(first.release.package_id, first.release.version).operation_id,
            timeout=5,
        )
        assert installed.result_instance_id is not None

        with self.assertRaisesRegex(RuntimePackageOperationError, "incompatible"):
            manager.upgrade_async(installed.result_instance_id, second.release.version)

        self.assertEqual(
            downloader.calls,
            [(first.release.package_id, first.release.version)],
        )
        self.assertFalse(
            (manager.managed_root / second.release.package_id / second.release.version).exists()
        )

    def test_changed_custom_manifest_invalidates_binding_without_deleting_files(self) -> None:
        artifact = build_package(self.artifacts_root)
        custom_root = self.root / "custom"
        shutil.copytree(artifact.root, custom_root)
        manager = self.manager()
        custom = manager.register_custom(custom_root)
        manager.bind("asr.default", custom.instance_id, "asr.faster_whisper")
        manifest_path = custom_root / RUNTIME_PACKAGE_MANIFEST
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["display_name"] = "Changed outside Video2Notes"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(RuntimePackageBindingError):
            manager.resolve("asr.default")
        self.assertTrue(custom_root.is_dir())


if __name__ == "__main__":
    unittest.main()
