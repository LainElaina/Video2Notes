from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from video2notes.components.runtime_catalog import (
    RUNTIME_CATALOG_ENVIRONMENT,
    RuntimePackageCatalog,
    runtime_catalog_from_environment,
)
from video2notes.components.runtime_models import (
    RUNTIME_PACKAGE_MANIFEST,
    RuntimeArchivePartSpec,
    RuntimeArchiveSpec,
    RuntimePackageManifest,
    RuntimePackageRelease,
)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def manifest_payload() -> dict[str, object]:
    worker = b"worker"
    license_text = b"notice"
    return {
        "schema": 1,
        "package_id": "local-inference-cpu-win-x64",
        "version": "1.0.0",
        "display_name": "Local inference CPU",
        "target_triple": "x86_64-pc-windows-msvc",
        "runtime_protocol_version": 1,
        "capabilities": [
            {
                "capability_id": "asr.faster_whisper",
                "engine_id": "faster-whisper",
                "protocol_version": 1,
                "transport": "worker",
                "entrypoint": "runtime-worker.exe",
                "supported_devices": ["cpu"],
            }
        ],
        "licenses": [
            {
                "name": "Third-party notices",
                "relative_path": "licenses/THIRD_PARTY_NOTICES.md",
            }
        ],
        "upstream_sources": ["https://example.invalid/faster-whisper"],
        "payload_size_bytes": len(worker) + len(license_text),
        "user_model_weights_included": False,
        "files": [
            {
                "relative_path": "licenses/THIRD_PARTY_NOTICES.md",
                "size_bytes": len(license_text),
                "sha256": sha256(license_text),
            },
            {
                "relative_path": "runtime-worker.exe",
                "size_bytes": len(worker),
                "sha256": sha256(worker),
            },
        ],
    }


def release_payload(manifest: RuntimePackageManifest) -> dict[str, object]:
    manifest_bytes = manifest.model_dump_json(indent=2).encode("utf-8")
    files = [
        *[item.model_dump(mode="json") for item in manifest.files],
        {
            "relative_path": RUNTIME_PACKAGE_MANIFEST,
            "size_bytes": len(manifest_bytes),
            "sha256": sha256(manifest_bytes),
        },
    ]
    return {
        "schema": 1,
        "package_id": manifest.package_id,
        "version": manifest.version,
        "display_name": manifest.display_name,
        "target_triple": manifest.target_triple,
        "runtime_protocol_version": manifest.runtime_protocol_version,
        "capabilities": [item.model_dump(mode="json") for item in manifest.capabilities],
        "archive": {
            "file_name": "local-inference-cpu-win-x64.zip",
            "source_url": None,
            "size_bytes": 123,
            "sha256": "a" * 64,
            "offline_only": True,
        },
        "installed_size_bytes": sum(int(item["size_bytes"]) for item in files),
        "files": files,
        "licenses": [item.model_dump(mode="json") for item in manifest.licenses],
        "upstream_sources": list(manifest.upstream_sources),
    }


class RuntimePackageModelTests(unittest.TestCase):
    def test_pack_builder_manifest_schema_round_trips_without_mapping(self) -> None:
        manifest = RuntimePackageManifest.model_validate(manifest_payload())

        dumped = manifest.model_dump(mode="json")

        self.assertEqual(dumped["schema"], 1)
        self.assertNotIn("schema_version", dumped)
        self.assertEqual(manifest.capability_ids, ("asr.faster_whisper",))
        self.assertEqual(manifest.files[1].relative_path, "runtime-worker.exe")

    def test_manifest_rejects_escape_protocol_mismatch_and_model_weights(self) -> None:
        unsafe = manifest_payload()
        unsafe["files"] = [
            {
                "relative_path": "../runtime-worker.exe",
                "size_bytes": 6,
                "sha256": sha256(b"worker"),
            }
        ]
        unsafe["payload_size_bytes"] = 6
        with self.assertRaises(ValidationError):
            RuntimePackageManifest.model_validate(unsafe)

        protocol = manifest_payload()
        protocol["capabilities"][0]["protocol_version"] = 2  # type: ignore[index]
        with self.assertRaises(ValidationError):
            RuntimePackageManifest.model_validate(protocol)

        weights = manifest_payload()
        weights["user_model_weights_included"] = True
        with self.assertRaises(ValidationError):
            RuntimePackageManifest.model_validate(weights)

    def test_catalog_entry_reconstructs_exact_internal_manifest(self) -> None:
        manifest = RuntimePackageManifest.model_validate(manifest_payload())
        release = RuntimePackageRelease.model_validate(release_payload(manifest))

        self.assertEqual(release.manifest, manifest)
        self.assertTrue(release.archive.offline_only)
        self.assertIsNone(release.archive_url)
        self.assertEqual(release.archive_sha256, "a" * 64)

    def test_trusted_offline_catalog_can_bind_a_local_archive(self) -> None:
        archive = RuntimeArchiveSpec(
            file_name="local-inference-cpu-win-x64.zip",
            source_url="file:///D:/runtime-packs/local-inference-cpu-win-x64.zip",
            size_bytes=123,
            sha256="a" * 64,
            offline_only=True,
        )

        self.assertTrue(archive.source_url and archive.source_url.startswith("file://"))

    def test_catalog_accepts_pinned_multipart_archives(self) -> None:
        manifest = RuntimePackageManifest.model_validate(manifest_payload())
        payload = release_payload(manifest)
        payload["archive"] = {
            "file_name": "local-inference-nvidia-full.zip",
            "source_url": None,
            "size_bytes": 123,
            "sha256": "a" * 64,
            "offline_only": False,
            "parts": [
                {
                    "file_name": "local-inference-nvidia-full.zip.001",
                    "source_url": "https://example.invalid/full.zip.001",
                    "size_bytes": 60,
                    "sha256": "b" * 64,
                },
                {
                    "file_name": "local-inference-nvidia-full.zip.002",
                    "source_url": "https://example.invalid/full.zip.002",
                    "size_bytes": 63,
                    "sha256": "c" * 64,
                },
            ],
        }

        release = RuntimePackageRelease.model_validate(payload)

        self.assertIsNone(release.archive_url)
        self.assertEqual(release.archive_part_count, 2)
        self.assertEqual(
            release.archive.parts[0].file_name,
            "local-inference-nvidia-full.zip.001",
        )
        self.assertIsInstance(release.archive.parts[0], RuntimeArchivePartSpec)

    def test_multipart_archive_rejects_ambiguous_or_untrusted_parts(self) -> None:
        manifest = RuntimePackageManifest.model_validate(manifest_payload())

        def multipart_archive() -> dict[str, object]:
            return {
                "file_name": "local-inference-nvidia-full.zip",
                "source_url": None,
                "size_bytes": 123,
                "sha256": "a" * 64,
                "offline_only": False,
                "parts": [
                    {
                        "file_name": "full.zip.001",
                        "source_url": "https://example.invalid/full.zip.001",
                        "size_bytes": 60,
                        "sha256": "b" * 64,
                    },
                    {
                        "file_name": "full.zip.002",
                        "source_url": "https://example.invalid/full.zip.002",
                        "size_bytes": 63,
                        "sha256": "c" * 64,
                    },
                ],
            }

        duplicate_name = release_payload(manifest)
        duplicate_name["archive"] = multipart_archive()
        duplicate_name["archive"]["parts"][1]["file_name"] = "FULL.ZIP.001"  # type: ignore[index]
        with self.assertRaises(ValidationError):
            RuntimePackageRelease.model_validate(duplicate_name)

        duplicate_url = release_payload(manifest)
        duplicate_url["archive"] = multipart_archive()
        duplicate_url["archive"]["parts"][1]["source_url"] = (  # type: ignore[index]
            "https://example.invalid/full.zip.001"
        )
        with self.assertRaises(ValidationError):
            RuntimePackageRelease.model_validate(duplicate_url)

        wrong_total = release_payload(manifest)
        wrong_total["archive"] = multipart_archive()
        wrong_total["archive"]["parts"][1]["size_bytes"] = 62  # type: ignore[index]
        with self.assertRaises(ValidationError):
            RuntimePackageRelease.model_validate(wrong_total)

        insecure_url = release_payload(manifest)
        insecure_url["archive"] = multipart_archive()
        insecure_url["archive"]["parts"][0]["source_url"] = (  # type: ignore[index]
            "http://example.invalid/full.zip.001"
        )
        with self.assertRaises(ValidationError):
            RuntimePackageRelease.model_validate(insecure_url)

        wrong_publication_mode = release_payload(manifest)
        wrong_publication_mode["archive"] = multipart_archive()
        wrong_publication_mode["archive"]["parts"][0]["source_url"] = (  # type: ignore[index]
            "file:///D:/runtime-packs/full.zip.001"
        )
        with self.assertRaises(ValidationError):
            RuntimePackageRelease.model_validate(wrong_publication_mode)

        ambiguous_source = release_payload(manifest)
        ambiguous_source["archive"] = multipart_archive()
        ambiguous_source["archive"]["source_url"] = (  # type: ignore[index]
            "https://example.invalid/full.zip"
        )
        with self.assertRaises(ValidationError):
            RuntimePackageRelease.model_validate(ambiguous_source)

    def test_catalog_rejects_unpinned_or_in_process_managed_release(self) -> None:
        manifest = RuntimePackageManifest.model_validate(manifest_payload())
        missing_hash = release_payload(manifest)
        missing_hash["archive"]["sha256"] = "not-a-hash"  # type: ignore[index]
        with self.assertRaises(ValidationError):
            RuntimePackageRelease.model_validate(missing_hash)

        in_process = release_payload(manifest)
        in_process["capabilities"][0]["transport"] = "in_process"  # type: ignore[index]
        in_process["capabilities"][0]["entrypoint"] = None  # type: ignore[index]
        with self.assertRaises(ValidationError):
            RuntimePackageRelease.model_validate(in_process)

    def test_environment_catalog_is_explicit_and_supports_multiple_files(self) -> None:
        manifest = RuntimePackageManifest.model_validate(manifest_payload())
        release = RuntimePackageRelease.model_validate(release_payload(manifest))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.json"
            second = root / "second.json"
            first.write_text(
                RuntimePackageCatalog(releases=(release,)).model_dump_json(indent=2),
                encoding="utf-8",
            )
            upgraded = release.model_copy(update={"version": "2.0.0"})
            second.write_text(
                RuntimePackageCatalog(releases=(upgraded,)).model_dump_json(indent=2),
                encoding="utf-8",
            )

            catalog = runtime_catalog_from_environment(
                {RUNTIME_CATALOG_ENVIRONMENT: os.pathsep.join((str(first), str(second)))}
            )

        self.assertEqual(len(catalog.releases), 2)
        self.assertEqual(catalog.latest(manifest.package_id).version, "2.0.0")

    def test_packaging_catalog_metadata_and_packages_alias_parse_directly(self) -> None:
        manifest = RuntimePackageManifest.model_validate(manifest_payload())
        release = RuntimePackageRelease.model_validate(release_payload(manifest))

        catalog = RuntimePackageCatalog.model_validate(
            {
                "schema": 1,
                "catalog_id": "video2notes-runtime-packs",
                "target_triple": manifest.target_triple,
                "runtime_protocol_version": 1,
                "packages": [release.model_dump(mode="json")],
            }
        )

        self.assertEqual(catalog.releases, (release,))
        self.assertIn("packages", catalog.model_dump(mode="json"))
        self.assertNotIn("releases", catalog.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
