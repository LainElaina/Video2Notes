from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from video2notes.components.runtime_catalog import RuntimePackageCatalog

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPOSITORY_ROOT / "scripts" / "build_runtime_catalog.ps1"
SPLIT_SCRIPT = REPOSITORY_ROOT / "scripts" / "split_release_archive.ps1"
POWERSHELL = shutil.which("powershell")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
class RuntimeCatalogBuildTests(unittest.TestCase):
    def run_script(
        self, script: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                *arguments,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def write_release(
        self,
        root: Path,
        package_id: str,
        archive_payload: bytes,
    ) -> tuple[Path, Path]:
        package_root = root / package_id
        package_root.mkdir(parents=True)
        archive_name = f"{package_id}-0.2.0-x86_64-pc-windows-msvc.zip"
        archive_path = package_root / archive_name
        archive_path.write_bytes(archive_payload)
        payload_files = [
            ("runtime-package.json", b"{}"),
            ("runtime-worker.exe", b"worker"),
            ("licenses/NOTICE.md", b"notice"),
        ]
        entry = {
            "schema": 1,
            "package_id": package_id,
            "version": "0.2.0",
            "display_name": package_id,
            "target_triple": "x86_64-pc-windows-msvc",
            "runtime_protocol_version": 1,
            "capabilities": [
                {
                    "capability_id": "asr.test",
                    "engine_id": "test-engine",
                    "protocol_version": 1,
                    "transport": "worker",
                    "entrypoint": "runtime-worker.exe",
                    "supported_devices": ["cpu"],
                }
            ],
            "archive": {
                "file_name": archive_name,
                "source_url": None,
                "size_bytes": len(archive_payload),
                "sha256": sha256(archive_payload),
                "offline_only": True,
            },
            "installed_size_bytes": sum(len(payload) for _, payload in payload_files),
            "files": [
                {
                    "relative_path": relative_path,
                    "size_bytes": len(payload),
                    "sha256": sha256(payload),
                }
                for relative_path, payload in payload_files
            ],
            "licenses": [
                {
                    "name": "Test notice",
                    "relative_path": "licenses/NOTICE.md",
                }
            ],
            "upstream_sources": ["https://example.invalid/runtime"],
        }
        entry_path = package_root / f"{archive_name}.catalog-entry.json"
        entry_path.write_text(json.dumps(entry), encoding="utf-8")
        return archive_path, entry_path

    def build_arguments(self, entry_root: Path, output_path: Path) -> tuple[str, ...]:
        return (
            "-CatalogEntryDirectory",
            str(entry_root),
            "-PartManifestDirectory",
            str(entry_root),
            "-OutputPath",
            str(output_path),
            "-GitHubRepository",
            "LainElaina/Video2Notes",
            "-ReleaseTag",
            "v0.2.0",
            "-PythonPath",
            sys.executable,
        )

    def test_builds_deterministic_whole_and_multipart_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entries = root / "entries"
            output = root / "catalog.json"
            self.write_release(entries, "local-inference-cpu-win-x64", b"cpu-runtime")
            self.write_release(
                entries,
                "local-inference-nvidia-asr-cu129-win-x64",
                b"nvidia-asr-runtime",
            )
            full_archive, _ = self.write_release(
                entries,
                "local-inference-nvidia-full-cu129-win-x64",
                b"full-gpu-runtime-payload",
            )
            split = self.run_script(
                SPLIT_SCRIPT,
                "-ArchivePath",
                str(full_archive),
                "-OutputDirectory",
                str(full_archive.parent),
                "-MaxPartBytes",
                "9",
            )
            self.assertEqual(split.returncode, 0, split.stdout + split.stderr)

            build = self.run_script(
                BUILD_SCRIPT,
                *self.build_arguments(entries, output),
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            first_bytes = output.read_bytes()
            payload = json.loads(first_bytes)
            catalog = RuntimePackageCatalog.model_validate(payload)
            self.assertEqual(len(catalog.releases), 3)
            self.assertEqual(
                [release.package_id for release in catalog.releases],
                [
                    "local-inference-cpu-win-x64",
                    "local-inference-nvidia-asr-cu129-win-x64",
                    "local-inference-nvidia-full-cu129-win-x64",
                ],
            )
            base_url = (
                "https://github.com/LainElaina/Video2Notes/releases/download/v0.2.0"
            )
            for release in catalog.releases[:2]:
                self.assertEqual(
                    release.archive.source_url,
                    f"{base_url}/{release.archive.file_name}",
                )
                self.assertFalse(release.archive.offline_only)
                self.assertEqual(release.archive.parts, ())

            full = catalog.releases[2]
            self.assertIsNone(full.archive.source_url)
            self.assertFalse(full.archive.offline_only)
            self.assertEqual(len(full.archive.parts), 3)
            self.assertEqual(
                [part.file_name for part in full.archive.parts],
                [
                    f"{full.archive.file_name}.part001",
                    f"{full.archive.file_name}.part002",
                    f"{full.archive.file_name}.part003",
                ],
            )
            self.assertEqual(
                [part.source_url for part in full.archive.parts],
                [f"{base_url}/{part.file_name}" for part in full.archive.parts],
            )
            self.assertEqual(
                sum(part.size_bytes for part in full.archive.parts),
                full.archive.size_bytes,
            )

            rebuild = self.run_script(
                BUILD_SCRIPT,
                *self.build_arguments(entries, output),
                "-Overwrite",
            )
            self.assertEqual(rebuild.returncode, 0, rebuild.stdout + rebuild.stderr)
            self.assertEqual(output.read_bytes(), first_bytes)

    def test_archive_tampering_is_rejected_without_replacing_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entries = root / "entries"
            output = root / "catalog.json"
            archive, _ = self.write_release(
                entries,
                "local-inference-cpu-win-x64",
                b"trusted-runtime",
            )
            first = self.run_script(
                BUILD_SCRIPT,
                *self.build_arguments(entries, output),
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            trusted_catalog = output.read_bytes()

            archive.write_bytes(b"tampered-runtime")
            rejected = self.run_script(
                BUILD_SCRIPT,
                *self.build_arguments(entries, output),
                "-Overwrite",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("does not match", (rejected.stdout + rejected.stderr).lower())
            self.assertEqual(output.read_bytes(), trusted_catalog)


if __name__ == "__main__":
    unittest.main()
