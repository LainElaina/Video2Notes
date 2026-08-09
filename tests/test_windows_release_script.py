from __future__ import annotations

import ast
import json
import shutil
import subprocess
import tomllib
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "build_windows_release.ps1"


class WindowsReleaseScriptContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = SCRIPT_PATH.read_text(encoding="utf-8-sig")

    def test_desktop_release_versions_are_consistent(self) -> None:
        tauri = json.loads(
            (REPOSITORY_ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )
        desktop = json.loads(
            (REPOSITORY_ROOT / "apps/desktop/package.json").read_text(
                encoding="utf-8"
            )
        )
        with (REPOSITORY_ROOT / "apps/desktop/src-tauri/Cargo.toml").open(
            "rb"
        ) as handle:
            cargo = tomllib.load(handle)
        with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        backend_module = ast.parse(
            (REPOSITORY_ROOT / "src/video2notes/__init__.py").read_text(
                encoding="utf-8"
            )
        )
        backend_version = next(
            node.value.value
            for node in backend_module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )

        self.assertEqual(tauri["version"], project["project"]["version"])
        self.assertEqual(backend_version, tauri["version"])
        self.assertEqual(desktop["version"], tauri["version"])
        self.assertEqual(cargo["package"]["version"], tauri["version"])
        self.assertTrue(tauri["bundle"]["active"])
        self.assertEqual(
            tauri["bundle"]["resources"]["resources/backend/"], "backend/"
        )

    def test_installer_build_is_hard_limited_to_core_sidecars(self) -> None:
        self.assertIn('[ValidateSet("core")]', self.script)
        self.assertIn('[string]$ReleaseProfile = "core"', self.script)
        self.assertIn(
            '$releaseProfileDefinition.sidecar_flavor -ne "core-only"',
            self.script,
        )
        self.assertIn('$manifest.runtime_flavor -ne "core-only"', self.script)
        self.assertIn(
            '$ExpectedReleaseProfile -notin @($manifest.compatible_release_profiles)',
            self.script,
        )
        self.assertIn("Get-Video2NotesSidecarSourceFingerprint", self.script)
        self.assertIn("source_fingerprint_schema -ne 1", self.script)
        self.assertIn("user_model_weights_included -ne $false", self.script)
        self.assertIn(
            "$null -ne $manifest.packaged_runtime_assets",
            self.script,
        )
        self.assertIn(
            "@($manifest.packaged_runtime_assets).Count -ne 0",
            self.script,
        )
        self.assertIn(
            '$tauriConfiguration.bundle.resources."resources/backend/"',
            self.script,
        )
        self.assertNotIn("legacy_full", self.script)

    def test_builds_only_nsis_and_msi_from_a_verified_core_resource_tree(self) -> None:
        self.assertIn('$BundleIds = @("nsis", "msi")', self.script)
        self.assertIn('"build_sidecar.ps1"', self.script)
        self.assertIn('$sidecarArguments = @{', self.script)
        self.assertIn('ReleaseProfile = $ReleaseProfile', self.script)
        self.assertNotIn('@("-ReleaseProfile", $ReleaseProfile)', self.script)
        self.assertIn('"test_sidecar.ps1"', self.script)
        self.assertIn("-CoreOnly", self.script)
        self.assertIn("pnpm tauri build --bundles $bundleArgument --ci", self.script)
        self.assertIn("CARGO_TARGET_DIR", self.script)
        self.assertIn("Assert-InstallerSignature", self.script)
        self.assertIn("-SkipHealthSmoke", self.script)

    def test_publishes_stable_assets_and_machine_readable_checksums(self) -> None:
        self.assertIn(
            '"artifacts\\release\\windows\\$productVersion"', self.script
        )
        self.assertIn(
            '"Video2Notes-$productVersion-core-windows-x64-setup.exe"',
            self.script,
        )
        self.assertIn(
            '"Video2Notes-$productVersion-core-windows-x64.msi"', self.script
        )
        self.assertIn('"windows-release-manifest.json"', self.script)
        self.assertIn('"SHA256SUMS.txt"', self.script)
        self.assertIn("checksum_file_name", self.script)
        self.assertIn("sidecarAfterBuild.ManifestSha256", self.script)

    @unittest.skipUnless(
        shutil.which("powershell"), "Windows PowerShell is required for parsing"
    )
    def test_powershell_script_parses_without_errors(self) -> None:
        quoted_path = str(SCRIPT_PATH).replace("'", "''")
        command = (
            "$tokens = $null; $errors = $null; "
            "[void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{quoted_path}', [ref]$tokens, [ref]$errors); "
            "if ($errors.Count -gt 0) { "
            "$errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
        )
        subprocess.run(
            [
                "powershell",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
