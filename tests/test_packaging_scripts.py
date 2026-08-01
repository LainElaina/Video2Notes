from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class PackagingScriptContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8-sig")

    def test_asr_extra_declares_ctranslate2_as_a_direct_runtime(self) -> None:
        with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]

        asr = project["optional-dependencies"]["asr"]
        self.assertTrue(any(item.startswith("faster-whisper") for item in asr))
        self.assertTrue(any(item.startswith("ctranslate2") for item in asr))
        self.assertTrue(any(item.startswith("huggingface-hub") for item in asr))
        self.assertTrue(
            any(
                item.startswith("huggingface-hub")
                for item in project["optional-dependencies"]["ocr"]
            )
        )
        self.assertTrue(
            any(item.startswith("psutil") for item in project["dependencies"])
        )

    def test_sidecar_defaults_to_full_and_freezes_all_inference_runtimes(self) -> None:
        script = self.read("scripts/build_sidecar.ps1")

        self.assertIn('[switch]$CoreOnly', script)
        self.assertIn('$RuntimeFlavor = if ($CoreOnly) { "core-only" } else { "full" }', script)
        for module in ("faster_whisper", "ctranslate2", "huggingface_hub", "paddleocr"):
            self.assertIn(f'"--collect-all", "{module}"', script)
        self.assertIn('"--hidden-import", "paddle"', script)
        self.assertIn('"--exclude-module", "huggingface_hub"', script)
        self.assertIn('callable(getattr(module, "snapshot_download", None))', script)
        self.assertNotIn('"--collect-all", "paddle"', script)
        self.assertIn('"--collect-data", "paddlex"', script)
        self.assertIn('"--collect-binaries", "paddle"', script)
        self.assertIn('"--collect-all", "psutil"', script)
        self.assertIn("Get-PaddleMetadataDistributions", script)
        self.assertIn('$PyInstallerArguments += @("--copy-metadata", $distribution)', script)
        self.assertIn('VIDEO2NOTES_BUILD_COMPONENT_SPECS', script)
        self.assertIn('$inventoryScript | & $Python -', script)
        self.assertIn('$metadataScript | & $Python -', script)
        self.assertIn('$parsedInventory | ForEach-Object { $_ }', script)
        self.assertIn('$parsedMetadata | ForEach-Object { [string]$_ }', script)
        self.assertNotIn('& $Python -c $inventoryScript', script)
        self.assertNotIn('& $Python -c $metadataScript', script)
        self.assertIn('VIDEO2NOTES_BUILD_PYINSTALLER_ARGS', script)
        self.assertIn('from PyInstaller.__main__ import run', script)
        self.assertIn('arguments = json.loads(os.environ[', script)
        self.assertIn('PyInstaller arguments must be a non-empty string list', script)
        self.assertIn('run(arguments)', script)
        self.assertNotIn('& $VenvPython @PyInstallerArguments', script)
        self.assertIn('VIDEO2NOTES_RUNTIME_PROBE', script)
        self.assertIn('schema = 2', script)
        self.assertIn('runtime_flavor = $RuntimeFlavor', script)
        self.assertIn('source_fingerprint_schema = 1', script)
        self.assertIn('source_fingerprint_sha256 = $SourceFingerprintAfterBuild', script)
        self.assertIn('Get-Video2NotesSidecarSourceFingerprint', script)
        self.assertIn('user_model_weights_included = $false', script)
        self.assertIn('Where-Object { $_.FullName -ne $BackendManifestPath }', script)

    def test_private_payload_rules_allow_packages_but_not_user_models(self) -> None:
        sidecar = self.read("scripts/build_sidecar.ps1")
        portable = self.read("scripts/build_portable.ps1")

        old_package_directory_rule = (
            "faster[_-]?whisper|paddle(?:ocr|paddle)?|huggingface|modelscope|torch"
        )
        self.assertNotIn(old_package_directory_rule, sidecar)
        self.assertNotIn(old_package_directory_rule, portable)
        for script in (sidecar, portable):
            self.assertIn("silero_vad_v6.onnx", script)
            self.assertIn('".safetensors"', script)
            self.assertIn('".pdiparams"', script)
            self.assertIn("cookies?\\.txt", script)

    def test_portable_rejects_wrong_flavor_and_runs_frozen_runtime_probe(self) -> None:
        script = self.read("scripts/build_portable.ps1")

        self.assertIn('[switch]$CoreOnly', script)
        self.assertIn("cannot use a", script)
        self.assertIn('-SkipHealthSmoke', script)
        self.assertIn('-CoreOnly:$CoreOnly', script)
        self.assertIn('runtime_components = $StagedBackendManifest.components', script)
        self.assertIn('runtime_flavor = $RuntimeFlavor', script)
        self.assertIn('ExpectedSourceFingerprint', script)
        self.assertIn('sidecar_source_fingerprint_sha256', script)
        self.assertIn('Rebuild without -ReuseSidecar', script)
        self.assertIn('Get-Video2NotesFileSha256', script)
        self.assertNotIn('Get-FileHash', script)

    def test_sidecar_source_fingerprint_covers_code_and_packaging_inputs(self) -> None:
        script = self.read("scripts/packaging_common.ps1")

        self.assertIn('src\\video2notes', script)
        self.assertIn('pyproject.toml', script)
        self.assertIn('scripts\\build_sidecar.ps1', script)
        self.assertIn('scripts\\pyinstaller_runtime_hook.py', script)
        self.assertIn('scripts\\sidecar_entry.py', script)
        self.assertIn('Get-Video2NotesFileSha256', script)
        self.assertIn('[IO.File]::OpenRead', script)
        self.assertNotIn('Get-FileHash', script)
        self.assertIn('Security.Cryptography.SHA256', script)

    def test_sidecar_smoke_requires_tools_and_full_runtime_imports(self) -> None:
        script = self.read("scripts/test_sidecar.ps1")

        for component in (
            '"faster-whisper"',
            '"ctranslate2"',
            '"huggingface-hub"',
            '"paddleocr"',
            '"paddlepaddle"',
            '"yt-dlp"',
            '"psutil"',
        ):
            self.assertIn(component, script)
        self.assertIn('@("ffmpeg", "ffprobe")', script)
        self.assertIn('VIDEO2NOTES_RUNTIME_PROBE', script)
        self.assertIn('importable -ne $true', script)


if __name__ == "__main__":
    unittest.main()
