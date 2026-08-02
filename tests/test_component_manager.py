from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from video2notes.components import (
    DEFAULT_COMPONENT_CATALOG,
    ComponentActionKind,
    ComponentDownloader,
    ComponentDownloadError,
    ComponentManager,
    ComponentManifest,
    ComponentNotReadyError,
    ComponentState,
    DownloadResult,
    DownloadSource,
    HuggingFaceSnapshotDownloader,
    LocalModelRole,
    ModuleProbeResult,
    PaddleCompatibleDownloader,
    PaddleHuggingFaceDownloader,
    PrepareStatus,
)
from video2notes.system import QualityMode
from video2notes.system.hardware import HardwareTier


class FakeDownloader:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[str, Path, bool]] = []

    def download(
        self,
        manifest: ComponentManifest,
        destination: Path,
        *,
        resume: bool,
    ) -> DownloadResult:
        self.calls.append((manifest.id, destination, resume))
        destination.mkdir(parents=True, exist_ok=True)
        if self.failures > 0:
            self.failures -= 1
            partial = destination / (
                manifest.required_files[0] if manifest.required_files else "partial.bin"
            )
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(b"partial")
            raise RuntimeError("simulated interruption")
        for relative in manifest.required_files:
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"payload:{relative}".encode())
        for relative in manifest.required_nonempty_directories:
            directory = destination / relative
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "inference.bin").write_bytes(b"model")
        return DownloadResult(source_revision="test-revision")


def available_module(module_name: str, distribution_name: str) -> ModuleProbeResult:
    return ModuleProbeResult(
        available=True,
        version=f"test-{distribution_name}",
        path=f"runtime/{module_name}",
    )


class ComponentManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app_data = self.root / "app-data"
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.binaries: dict[str, str] = {}
        for name in ("ffmpeg", "ffprobe"):
            path = self.runtime / f"{name}.exe"
            path.write_bytes(b"portable-tool")
            self.binaries[name] = str(path)

    def manager(
        self,
        *,
        asr_downloader: ComponentDownloader | None = None,
        ocr_downloader: ComponentDownloader | None = None,
    ) -> ComponentManager:
        downloaders = {}
        if asr_downloader is not None:
            downloaders[DownloadSource.HUGGINGFACE_SNAPSHOT] = asr_downloader
        if ocr_downloader is not None:
            downloaders[DownloadSource.PADDLE_COMPATIBLE] = ocr_downloader
        return ComponentManager(
            self.app_data,
            runtime_root=self.runtime,
            binary_locator=lambda name: self.binaries.get(name),
            module_probe=available_module,
            downloaders=downloaders,
        )

    def test_inventory_distinguishes_packaged_runtime_from_missing_models(self) -> None:
        manager = self.manager(
            asr_downloader=FakeDownloader(),
            ocr_downloader=FakeDownloader(),
        )

        inventory = manager.inventory(HardwareTier.GPU_8GB)
        items = {item.id: item for item in inventory.items}

        self.assertFalse(inventory.ready)
        self.assertTrue(inventory.degraded)
        self.assertTrue(inventory.capabilities["core"])
        self.assertFalse(inventory.capabilities["asr"])
        self.assertFalse(inventory.capabilities["ocr"])
        self.assertTrue(items["ffmpeg"].ready)
        self.assertEqual(items["faster-whisper"].version, "test-faster-whisper")
        self.assertEqual(items["asr-faster-whisper-large-v3"].state, ComponentState.MISSING)
        self.assertTrue(
            any(action.kind is ComponentActionKind.PREPARE for action in inventory.actions)
        )
        self.assertTrue(all(action.automatic for action in inventory.actions))

    def test_one_click_prepare_makes_recommended_models_ready(self) -> None:
        asr = FakeDownloader()
        ocr = FakeDownloader()
        manager = self.manager(asr_downloader=asr, ocr_downloader=ocr)

        prepared = manager.prepare_recommended(HardwareTier.GPU_12GB)
        inventory = manager.inventory(HardwareTier.GPU_12GB)
        settings = manager.local_adapter_settings(HardwareTier.GPU_12GB)

        self.assertTrue(prepared.ready)
        self.assertTrue(inventory.ready)
        self.assertTrue(all(item.status is PrepareStatus.PREPARED for item in prepared.results))
        self.assertEqual(len(asr.calls), 1)
        self.assertEqual(len(ocr.calls), 1)
        for _, destination, _ in (*asr.calls, *ocr.calls):
            self.assertTrue(destination.resolve().is_relative_to(manager.managed_root))
        self.assertEqual(settings.asr["engine"], "faster_whisper")
        self.assertEqual(settings.asr["device"], "cuda")
        self.assertEqual(settings.ocr["engine"], "paddleocr")
        self.assertEqual(settings.ocr["device"], "cpu")
        self.assertTrue(Path(str(settings.asr["model_path"])).is_dir())
        self.assertTrue(Path(str(settings.ocr["detection_model_dir"])).is_dir())
        self.assertEqual(set(settings.asr_profiles), set(QualityMode))
        self.assertEqual(set(settings.ocr_profiles), set(QualityMode))
        self.assertEqual(
            settings.asr_profiles[QualityMode.FAST]["model_path"],
            settings.asr["model_path"],
        )
        self.assertEqual(
            settings.ocr_profiles[QualityMode.FAST]["detection_model_dir"],
            settings.ocr["detection_model_dir"],
        )

    def test_accurate_profile_uses_only_ready_higher_capacity_models(self) -> None:
        manager = self.manager(
            asr_downloader=FakeDownloader(),
            ocr_downloader=FakeDownloader(),
        )
        manager.prepare_recommended(HardwareTier.CPU_IGPU)

        fallback = manager.local_adapter_settings(HardwareTier.CPU_IGPU)
        self.assertEqual(
            fallback.asr_profiles[QualityMode.ACCURATE]["model_path"],
            fallback.asr["model_path"],
        )
        self.assertEqual(
            fallback.ocr_profiles[QualityMode.ACCURATE]["detection_model_dir"],
            fallback.ocr["detection_model_dir"],
        )

        manager.prepare("asr-faster-whisper-large-v3")
        manager.prepare("ocr-paddle-ppocrv5-server")
        upgraded = manager.local_adapter_settings(HardwareTier.CPU_IGPU)

        self.assertIn(
            "faster-whisper-large-v3",
            str(upgraded.asr_profiles[QualityMode.ACCURATE]["model_path"]),
        )
        self.assertIn(
            "ppocrv5-server",
            str(upgraded.ocr_profiles[QualityMode.ACCURATE]["detection_model_dir"]),
        )
        self.assertEqual(
            upgraded.asr_profiles[QualityMode.FAST]["model_path"],
            upgraded.asr["model_path"],
        )
        self.assertEqual(
            upgraded.asr_profiles[QualityMode.BALANCED]["model_path"],
            upgraded.asr["model_path"],
        )

    def test_fast_profile_never_downgrades_a_strong_recommended_primary(self) -> None:
        manager = self.manager(
            asr_downloader=FakeDownloader(),
            ocr_downloader=FakeDownloader(),
        )
        manager.prepare_recommended(HardwareTier.GPU_12GB)
        manager.prepare("asr-faster-whisper-small")
        manager.prepare("ocr-paddle-ppocrv5-mobile")

        settings = manager.local_adapter_settings(HardwareTier.GPU_12GB)

        for mode in QualityMode:
            self.assertEqual(
                settings.asr_profiles[mode]["model_path"],
                settings.asr["model_path"],
            )
            self.assertEqual(
                settings.ocr_profiles[mode]["detection_model_dir"],
                settings.ocr["detection_model_dir"],
            )

    def test_interrupted_download_has_no_ready_marker_and_resumes(self) -> None:
        asr = FakeDownloader(failures=1)
        manager = self.manager(asr_downloader=asr, ocr_downloader=FakeDownloader())
        component_id = "asr-faster-whisper-large-v3"

        failed = manager.prepare(component_id)
        inventory = manager.inventory(HardwareTier.GPU_8GB)
        item = next(entry for entry in inventory.items if entry.id == component_id)
        staging = manager.managed_root / ".staging" / f"{component_id}-1.0.0"

        self.assertEqual(failed.status, PrepareStatus.FAILED)
        self.assertFalse((staging / ".video2notes-component.json").exists())
        self.assertEqual(item.state, ComponentState.INCOMPLETE)
        self.assertEqual(item.actions[0].kind, ComponentActionKind.RESUME)

        resumed = manager.prepare(component_id)
        item_after = next(
            entry
            for entry in manager.inventory(HardwareTier.GPU_8GB).items
            if entry.id == component_id
        )
        self.assertEqual(resumed.status, PrepareStatus.PREPARED)
        self.assertTrue(resumed.resumed)
        self.assertTrue(asr.calls[1][2])
        self.assertTrue(item_after.ready)

    def test_completion_marker_does_not_hide_missing_required_payload(self) -> None:
        manager = self.manager(
            asr_downloader=FakeDownloader(),
            ocr_downloader=FakeDownloader(),
        )
        component_id = "asr-faster-whisper-small"
        prepared = manager.prepare(component_id)
        self.assertEqual(prepared.status, PrepareStatus.PREPARED)
        assert prepared.path is not None
        (Path(prepared.path) / "config.json").unlink()

        item = next(
            entry
            for entry in manager.inventory(HardwareTier.CPU_IGPU).items
            if entry.id == component_id
        )
        self.assertFalse(item.ready)
        self.assertEqual(item.state, ComponentState.INCOMPLETE)
        self.assertIn("required model file", item.detail or "")

    def test_completion_marker_rejects_truncated_or_modified_model_weights(self) -> None:
        manager = self.manager(
            asr_downloader=FakeDownloader(),
            ocr_downloader=FakeDownloader(),
        )
        component_id = "asr-faster-whisper-small"
        prepared = manager.prepare(component_id)
        assert prepared.path is not None
        model = Path(prepared.path) / "model.bin"
        original = model.read_bytes()

        model.write_bytes(b"")
        truncated = next(
            item
            for item in manager.inventory(HardwareTier.CPU_IGPU).items
            if item.id == component_id
        )
        self.assertFalse(truncated.ready)
        self.assertIn("empty", truncated.detail or "")

        repaired = manager.prepare(component_id)
        assert repaired.path is not None
        model = Path(repaired.path) / "model.bin"
        model.write_bytes(b"x" * len(original))
        modified = next(
            item
            for item in manager.inventory(HardwareTier.CPU_IGPU).items
            if item.id == component_id
        )
        self.assertFalse(modified.ready)
        self.assertIn("changed after preparation", modified.detail or "")

    def test_ocr_cache_metadata_alone_is_not_a_model_payload(self) -> None:
        class CacheOnlyDownloader:
            def download(
                self,
                manifest: ComponentManifest,
                destination: Path,
                *,
                resume: bool,
            ) -> DownloadResult:
                del manifest, resume
                for role in ("detection", "recognition"):
                    cache = destination / role / ".hf-cache"
                    cache.mkdir(parents=True, exist_ok=True)
                    (cache / "metadata.json").write_text("{}", encoding="utf-8")
                return DownloadResult(source_revision="cache-only")

        manager = self.manager(
            asr_downloader=FakeDownloader(),
            ocr_downloader=CacheOnlyDownloader(),
        )
        component_id = "ocr-paddle-ppocrv5-mobile"

        result = manager.prepare(component_id)
        item = next(
            entry
            for entry in manager.inventory(HardwareTier.CPU_IGPU).items
            if entry.id == component_id
        )

        self.assertEqual(result.status, PrepareStatus.FAILED)
        self.assertFalse(item.ready)

    def test_existing_incomplete_managed_target_is_recovered_not_deleted(self) -> None:
        manager = self.manager(
            asr_downloader=FakeDownloader(),
            ocr_downloader=FakeDownloader(),
        )
        manifest = DEFAULT_COMPONENT_CATALOG.manifests["asr-faster-whisper-small"]
        incomplete = manager.managed_root / manifest.target_subdirectory
        incomplete.mkdir(parents=True)
        (incomplete / "sentinel.txt").write_text("recover me", encoding="utf-8")

        result = manager.prepare(manifest.id)
        recovered = list((manager.managed_root / ".recovery").rglob("sentinel.txt"))

        self.assertEqual(result.status, PrepareStatus.PREPARED)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].read_text(encoding="utf-8"), "recover me")

    def test_manager_never_scans_or_changes_private_model_directory(self) -> None:
        private = self.root / "private-models"
        private.mkdir()
        sentinel = private / "user-model.bin"
        sentinel.write_bytes(b"private")
        manager = self.manager(
            asr_downloader=FakeDownloader(),
            ocr_downloader=FakeDownloader(),
        )

        manager.inventory(HardwareTier.CPU_IGPU)
        manager.prepare_recommended(HardwareTier.CPU_IGPU)

        self.assertEqual(sentinel.read_bytes(), b"private")
        self.assertFalse(private.is_relative_to(manager.managed_root))

    def test_manifest_rejects_paths_that_escape_app_data(self) -> None:
        with self.assertRaises(ValidationError):
            ComponentManifest(
                id="asr-unsafe-model",
                version="1",
                display_name="Unsafe",
                role=LocalModelRole.ASR,
                engine="faster_whisper",
                source_kind=DownloadSource.HUGGINGFACE_SNAPSHOT,
                source="example/model",
                target_subdirectory="../private-models",
                required_files=("model.bin",),
            )
        with self.assertRaises(ValidationError):
            ComponentManifest(
                id="ocr-unsafe-model",
                version="1",
                display_name="Unsafe",
                role=LocalModelRole.OCR,
                engine="paddleocr",
                source_kind=DownloadSource.PADDLE_COMPATIBLE,
                source="paddleocr://unsafe",
                target_subdirectory="models/ocr/unsafe",
                required_files=("../../outside.bin",),
            )

    def test_missing_packaged_dependencies_produce_repair_actions(self) -> None:
        manager = ComponentManager(
            self.app_data,
            runtime_root=self.root / "missing-runtime",
            binary_locator=lambda _name: None,
            module_probe=lambda _module, _distribution: ModuleProbeResult(available=False),
            downloaders={},
        )

        inventory = manager.inventory(HardwareTier.CPU_IGPU)

        self.assertFalse(inventory.ready)
        self.assertFalse(inventory.capabilities["core"])
        self.assertTrue(
            any(action.kind is ComponentActionKind.REPAIR_RUNTIME for action in inventory.actions)
        )
        self.assertTrue(
            all(
                not action.automatic
                for action in inventory.actions
                if action.kind is ComponentActionKind.REPAIR_RUNTIME
            )
        )

    def test_paddle_preparation_is_automatic_in_the_default_manager(self) -> None:
        manager = self.manager(asr_downloader=FakeDownloader())
        recommendation = manager.recommendation(HardwareTier.CPU_IGPU)
        item = next(
            entry
            for entry in manager.inventory(HardwareTier.CPU_IGPU).items
            if entry.id == recommendation.ocr_component_id
        )

        self.assertTrue(item.actions[0].automatic)

    def test_catalog_contains_only_versioned_local_asr_and_ocr_models(self) -> None:
        catalog = DEFAULT_COMPONENT_CATALOG
        self.assertEqual(set(catalog.recommendations), set(HardwareTier))
        self.assertTrue(catalog.manifests)
        self.assertTrue(
            all(
                manifest.role in {LocalModelRole.ASR, LocalModelRole.OCR}
                and bool(manifest.version)
                for manifest in catalog.manifests.values()
            )
        )
        self.assertTrue(all("llm" not in manifest.id for manifest in catalog.manifests.values()))
        self.assertTrue(
            all(
                recommendation.ocr_device == "cpu"
                for tier, recommendation in catalog.recommendations.items()
                if tier is not HardwareTier.CPU_IGPU
            )
        )

    def test_adapter_settings_require_both_models_to_be_complete(self) -> None:
        manager = self.manager(
            asr_downloader=FakeDownloader(),
            ocr_downloader=FakeDownloader(),
        )
        with self.assertRaises(ComponentNotReadyError):
            manager.local_adapter_settings(HardwareTier.GPU_24GB_PLUS)


class OptionalDownloaderTests(unittest.TestCase):
    def test_huggingface_downloader_is_lazy_and_confines_its_cache(self) -> None:
        manifest = DEFAULT_COMPONENT_CATALOG.manifests["asr-faster-whisper-small"]
        calls: list[dict[str, object]] = []

        class FakeHub:
            @staticmethod
            def snapshot_download(**kwargs: object) -> str:
                calls.append(kwargs)
                destination = Path(str(kwargs["local_dir"]))
                destination.mkdir(parents=True, exist_ok=True)
                return str(destination)

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "managed-staging"
            with patch(
                "video2notes.components.downloaders.importlib.import_module",
                return_value=FakeHub(),
            ):
                result = HuggingFaceSnapshotDownloader().download(
                    manifest,
                    destination,
                    resume=True,
                )

        self.assertEqual(
            result.source_revision,
            "536b0662742c02347bc0e980a01041f333bce120",
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(Path(str(calls[0]["local_dir"])), destination)
        self.assertTrue(Path(str(calls[0]["cache_dir"])).is_relative_to(destination))
        self.assertIs(calls[0]["token"], False)
        self.assertNotIn("local_dir_use_symlinks", calls[0])

    def test_default_paddle_downloader_fetches_two_pinned_managed_snapshots(
        self,
    ) -> None:
        manifest = DEFAULT_COMPONENT_CATALOG.manifests["ocr-paddle-ppocrv5-mobile"]
        calls: list[dict[str, object]] = []

        def snapshot_download(**kwargs: object) -> str:
            calls.append(kwargs)
            destination = Path(str(kwargs["local_dir"]))
            destination.mkdir(parents=True, exist_ok=True)
            return str(destination)

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "managed-staging"
            result = PaddleHuggingFaceDownloader(snapshot_download).download(
                manifest,
                destination,
                resume=False,
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(
            [item["repo_id"] for item in calls],
            [
                "PaddlePaddle/PP-OCRv5_mobile_det",
                "PaddlePaddle/PP-OCRv5_mobile_rec",
            ],
        )
        self.assertTrue(all(len(str(item["revision"])) == 40 for item in calls))
        self.assertTrue(all(item["token"] is False for item in calls))
        self.assertIn("PP-OCRv5_mobile_det@", result.source_revision or "")

    def test_missing_huggingface_optional_dependency_is_actionable(self) -> None:
        manifest = DEFAULT_COMPONENT_CATALOG.manifests["asr-faster-whisper-small"]
        with tempfile.TemporaryDirectory() as temporary, patch(
            "video2notes.components.downloaders.importlib.import_module",
            side_effect=ImportError,
        ), self.assertRaises(ComponentDownloadError):
            HuggingFaceSnapshotDownloader().download(
                manifest,
                Path(temporary),
                resume=False,
            )

    def test_paddle_compatibility_resolver_receives_only_managed_destination(self) -> None:
        manifest = DEFAULT_COMPONENT_CATALOG.manifests["ocr-paddle-ppocrv5-mobile"]
        calls: list[tuple[Path, bool]] = []

        def resolver(
            selected: ComponentManifest,
            destination: Path,
            resume: bool,
        ) -> DownloadResult:
            self.assertEqual(selected.id, manifest.id)
            calls.append((destination, resume))
            return DownloadResult(source_revision="paddle-test")

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "managed-staging"
            result = PaddleCompatibleDownloader(resolver).download(
                manifest,
                destination,
                resume=True,
            )

        self.assertEqual(result.source_revision, "paddle-test")
        self.assertEqual(calls, [(destination, True)])


if __name__ == "__main__":
    unittest.main()
