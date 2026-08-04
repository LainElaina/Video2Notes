from __future__ import annotations

import asyncio
import hashlib
import platform
import tempfile
import unittest
from pathlib import Path

from video2notes.components.runtime_catalog import RuntimePackageCatalog
from video2notes.components.runtime_manager import RuntimePackageManager
from video2notes.components.runtime_models import (
    FeatureAvailabilityState,
    RuntimePackageCandidate,
    RuntimePackageManifest,
    RuntimePackageRelease,
    RuntimePackageSource,
)
from video2notes.components.runtime_preflight import build_runtime_preflight
from video2notes.domain import ProcessingScope
from video2notes.notes import EvidenceNoteComposer, OutputFormat, ReportSpec
from video2notes.pipeline import PipelineRequest, PipelineRuntime
from video2notes.sources import SourceInput, SourceRegistry


class FakeAsrBackend:
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _host_target_triple() -> str:
    machine = platform.machine().casefold()
    architecture = "aarch64" if machine in {"aarch64", "arm64"} else "x86_64"
    system = platform.system().casefold()
    if system == "windows":
        return f"{architecture}-pc-windows-msvc"
    if system == "darwin":
        return f"{architecture}-apple-darwin"
    return f"{architecture}-unknown-linux-gnu"


def _incompatible_target_triple() -> str:
    architecture = _host_target_triple().split("-", maxsplit=1)[0]
    if platform.system().casefold() == "windows":
        return f"{architecture}-unknown-linux-gnu"
    return f"{architecture}-pc-windows-msvc"


def _catalog_release(*, version: str, target_triple: str) -> RuntimePackageRelease:
    package_id = "test-pdf-runtime"
    return RuntimePackageRelease.model_validate(
        {
            "schema": 1,
            "package_id": package_id,
            "version": version,
            "display_name": f"Test PDF runtime {version}",
            "target_triple": target_triple,
            "runtime_protocol_version": 1,
            "capabilities": [
                {
                    "capability_id": "render.chromium_pdf",
                    "engine_id": "test-pdf-worker",
                    "protocol_version": 1,
                    "transport": "worker",
                    "entrypoint": "runtime-worker.exe",
                    "supported_devices": ["cpu"],
                }
            ],
            "archive": {
                "file_name": f"{package_id}-{version}.zip",
                "source_url": None,
                "size_bytes": 100,
                "sha256": "3" * 64,
                "offline_only": True,
            },
            "installed_size_bytes": 60,
            "files": [
                {
                    "relative_path": "runtime-package.json",
                    "size_bytes": 10,
                    "sha256": "0" * 64,
                },
                {
                    "relative_path": "runtime-worker.exe",
                    "size_bytes": 20,
                    "sha256": "1" * 64,
                },
                {
                    "relative_path": "THIRD_PARTY_NOTICES.md",
                    "size_bytes": 30,
                    "sha256": "2" * 64,
                },
            ],
            "licenses": [
                {
                    "name": "Test notices",
                    "relative_path": "THIRD_PARTY_NOTICES.md",
                }
            ],
            "upstream_sources": ["https://example.invalid/test-pdf-worker"],
        }
    )


def _bundled_candidate(root: Path) -> RuntimePackageCandidate:
    marker = root / "NOTICE.txt"
    marker.write_text("test runtime\n", encoding="utf-8")
    manifest = RuntimePackageManifest.model_validate(
        {
            "schema": 1,
            "package_id": "test-current-runtime",
            "version": "1.0.0",
            "display_name": "Test current runtime",
            "target_triple": _host_target_triple(),
            "runtime_protocol_version": 1,
            "capabilities": [
                {
                    "capability_id": capability,
                    "engine_id": capability,
                    "protocol_version": 1,
                    "transport": "in_process",
                    "entrypoint": None,
                    "supported_devices": ["cpu"],
                }
                for capability in (
                    "tool.ffmpeg",
                    "tool.ffprobe",
                    "asr.faster_whisper",
                )
            ],
            "licenses": [
                {"name": "Test notice", "relative_path": marker.name}
            ],
            "upstream_sources": ["https://example.invalid/runtime"],
            "payload_size_bytes": marker.stat().st_size,
            "user_model_weights_included": False,
            "files": [
                {
                    "relative_path": marker.name,
                    "size_bytes": marker.stat().st_size,
                    "sha256": _sha256(marker),
                }
            ],
        }
    )
    return RuntimePackageCandidate(
        source=RuntimePackageSource.BUNDLED,
        root=str(root),
        manifest=manifest,
    )


class RuntimePreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        bundled_root = root / "bundled"
        bundled_root.mkdir()
        self.manager = RuntimePackageManager(
            root / "data",
            catalog=RuntimePackageCatalog(),
            bundled_packages=(_bundled_candidate(bundled_root),),
        )
        self.addCleanup(self.manager.close)
        self.runtime = PipelineRuntime(
            source_registry=SourceRegistry.default(),
            note_composer=EvidenceNoteComposer(),
            asr_backend=FakeAsrBackend(),  # type: ignore[arg-type]
            ffmpeg_path="missing-ffmpeg-is-covered-by-candidate",
            ffprobe_path="missing-ffprobe-is-covered-by-candidate",
        )

    def test_audio_only_does_not_require_ocr_or_pdf(self) -> None:
        request = PipelineRequest(
            source=SourceInput.local("sample.mp4"),
            processing_scope=ProcessingScope.AUDIO_ONLY,
            generate_pdf=False,
        )

        result = asyncio.run(
            build_runtime_preflight(
                self.manager,
                request,
                source_registry=SourceRegistry.default(),
                fallback_runtime=self.runtime,
            )
        )

        self.assertEqual(result.state, FeatureAvailabilityState.READY)
        self.assertNotIn("ocr.paddleocr", [item.requirement_id for item in result.requirements])
        self.assertNotIn("render.chromium_pdf", result.missing_required)

    def test_visual_mode_degrades_without_ocr_and_pdf_is_explicitly_blocking(self) -> None:
        visual = PipelineRequest(
            source=SourceInput.local("sample.mp4"),
            processing_scope=ProcessingScope.AUDIO_VISUAL,
            generate_pdf=False,
        )
        visual_result = asyncio.run(
            build_runtime_preflight(
                self.manager,
                visual,
                source_registry=SourceRegistry.default(),
                fallback_runtime=self.runtime,
            )
        )
        pdf = visual.model_copy(
            update={
                "report_spec": ReportSpec(
                    output_formats={OutputFormat.MARKDOWN, OutputFormat.PDF}
                )
            }
        )
        pdf_result = asyncio.run(
            build_runtime_preflight(
                self.manager,
                pdf,
                source_registry=SourceRegistry.default(),
                fallback_runtime=self.runtime,
            )
        )

        self.assertEqual(visual_result.state, FeatureAvailabilityState.DEGRADED)
        self.assertEqual(visual_result.missing_optional, ("ocr.paddleocr",))
        self.assertEqual(pdf_result.state, FeatureAvailabilityState.BLOCKED)
        self.assertIn("render.chromium_pdf", pdf_result.missing_required)

    def test_recommendation_selects_latest_compatible_release_from_unsorted_catalog(
        self,
    ) -> None:
        latest_compatible = _catalog_release(
            version="10.0.0",
            target_triple=_host_target_triple(),
        )
        incompatible = _catalog_release(
            version="99.0.0",
            target_triple=_incompatible_target_triple(),
        )
        older_compatible = _catalog_release(
            version="2.0.0",
            target_triple=_host_target_triple(),
        )
        self.manager.catalog = RuntimePackageCatalog(
            packages=(latest_compatible, incompatible, older_compatible)
        )
        request = PipelineRequest(
            source=SourceInput.local("sample.mp4"),
            processing_scope=ProcessingScope.AUDIO_ONLY,
            report_spec=ReportSpec(
                output_formats={OutputFormat.MARKDOWN, OutputFormat.PDF}
            ),
        )

        result = asyncio.run(
            build_runtime_preflight(
                self.manager,
                request,
                source_registry=SourceRegistry.default(),
                fallback_runtime=self.runtime,
            )
        )

        self.assertEqual(len(result.recommended_actions), 1)
        recommendation = result.recommended_actions[0]
        self.assertEqual(recommendation.package_id, latest_compatible.package_id)
        self.assertEqual(recommendation.version, latest_compatible.version)
        self.assertNotEqual(recommendation.version, incompatible.version)


if __name__ == "__main__":
    unittest.main()
