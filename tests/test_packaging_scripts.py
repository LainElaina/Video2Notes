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
        expected_asr_versions = {
            "nvidia-cublas-cu12": "12.9.0.13",
            "nvidia-cuda-nvrtc-cu12": "12.9.86",
            "nvidia-cudnn-cu12": "9.9.0.52",
            "nvidia-nvjitlink-cu12": "12.9.86",
        }
        for distribution, version in expected_asr_versions.items():
            requirement = next(item for item in asr if item.startswith(distribution))
            self.assertIn(f"=={version}", requirement)
            self.assertIn("platform_system == 'Windows'", requirement)
        self.assertTrue(
            any(
                item.startswith("huggingface-hub")
                for item in project["optional-dependencies"]["ocr"]
            )
        )
        self.assertTrue(
            any(item.startswith("psutil") for item in project["dependencies"])
        )

    def test_gpu_ocr_extra_and_bootstrap_are_reproducible_and_exclusive(self) -> None:
        with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
            extras = tomllib.load(handle)["project"]["optional-dependencies"]

        gpu = extras["ocr-gpu"]
        expected_versions = {
            "paddleocr": "3.7.0",
            "nvidia-cuda-runtime-cu12": "12.9.37",
            "nvidia-cublas-cu12": "12.9.0.13",
            "nvidia-cudnn-cu12": "9.9.0.52",
            "nvidia-cufft-cu12": "11.4.0.6",
            "nvidia-curand-cu12": "10.3.10.19",
            "nvidia-cusolver-cu12": "11.7.4.40",
            "nvidia-cusparse-cu12": "12.5.9.5",
            "nvidia-cuda-nvrtc-cu12": "12.9.86",
            "nvidia-nvjitlink-cu12": "12.9.86",
        }
        for distribution, version in expected_versions.items():
            requirement = next(item for item in gpu if item.startswith(distribution))
            self.assertIn(f"=={version}", requirement)
        self.assertFalse(any(item.startswith("paddlepaddle") for item in gpu))

        bootstrap = self.read("scripts/bootstrap.ps1")
        self.assertIn("[switch]$WithOcrGpu", bootstrap)
        self.assertIn("$WithOcr -and $WithOcrGpu", bootstrap)
        self.assertIn("mutually exclusive", bootstrap)
        self.assertIn("https://pypi.org/simple", bootstrap)
        self.assertIn(
            "https://www.paddlepaddle.org.cn/packages/stable/cu129/",
            bootstrap,
        )
        self.assertIn('$PaddleGpuDistribution = "paddlepaddle-gpu"', bootstrap)
        self.assertIn('$PaddleGpuVersion = "3.3.1"', bootstrap)
        self.assertIn('if ($WithOcrGpu -and $Offline)', bootstrap)
        self.assertIn('if ($WithOcrGpu -and -not $Offline)', bootstrap)
        self.assertIn("--no-deps", bootstrap)
        self.assertIn("--no-index", bootstrap)
        self.assertIn("--isolated", bootstrap)
        self.assertIn("--no-input", bootstrap)
        self.assertIn("Uninstall-PythonDistribution", bootstrap)
        self.assertIn("Get-PythonDistributionVersion", bootstrap)

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

    def test_full_sidecar_bundles_and_verifies_nvidia_cuda_runtime(self) -> None:
        build = self.read("scripts/build_sidecar.ps1")
        smoke = self.read("scripts/test_sidecar.ps1")
        hook = self.read("scripts/pyinstaller_runtime_hook.py")

        runtimes = (
            ("nvidia-cublas-cu12", "nvidia.cublas", "cublas", "cublas64_12.dll"),
            (
                "nvidia-cuda-nvrtc-cu12",
                "nvidia.cuda_nvrtc",
                "cuda_nvrtc",
                "nvrtc64_120_0.dll",
            ),
            (
                "nvidia-cuda-runtime-cu12",
                "nvidia.cuda_runtime",
                "cuda_runtime",
                "cudart64_12.dll",
            ),
            ("nvidia-cudnn-cu12", "nvidia.cudnn", "cudnn", "cudnn64_9.dll"),
            ("nvidia-cufft-cu12", "nvidia.cufft", "cufft", "cufft64_11.dll"),
            ("nvidia-curand-cu12", "nvidia.curand", "curand", "curand64_10.dll"),
            (
                "nvidia-cusolver-cu12",
                "nvidia.cusolver",
                "cusolver",
                "cusolver64_11.dll",
            ),
            (
                "nvidia-cusparse-cu12",
                "nvidia.cusparse",
                "cusparse",
                "cusparse64_12.dll",
            ),
            (
                "nvidia-nvjitlink-cu12",
                "nvidia.nvjitlink",
                "nvjitlink",
                "nvJitLink_120_0.dll",
            ),
        )
        for distribution, module, package_path, filename in runtimes:
            self.assertIn(f'id = "{distribution}"', build)
            self.assertIn(f'distribution = "{distribution}"', build)
            self.assertIn(f'module = "{module}"', build)
            self.assertIn(f'"{distribution}"', smoke)
            self.assertIn(filename, build)
            self.assertIn(filename, smoke)
            self.assertIn(f'/ "{package_path}" / "bin"', hook)
        self.assertIn("cublasLt64_12.dll", build)
        self.assertIn("cublasLt64_12.dll", smoke)
        self.assertIn('foreach ($runtime in $IncludedNvidiaRuntimeComponents)', build)
        self.assertIn('"--hidden-import", [string]$runtime.module', build)
        self.assertIn('"--collect-binaries", [string]$runtime.module', build)
        self.assertIn('"--copy-metadata", [string]$runtime.distribution', build)
        self.assertIn('$_.Name -match "(?i)^licen[cs]e', build)
        self.assertIn('$_.Name -match "(?i)^licen[cs]e', smoke)
        self.assertIn('"--exclude-module", "nvidia"', build)
        self.assertIn("nvidia/cuda12.9-complete-paddle-ocr-and-ctranslate2-runtime", build)
        self.assertIn('$PaddleDistribution -eq "paddlepaddle-gpu"', build)
        self.assertIn('distributions = @("paddlepaddle-gpu", "paddlepaddle")', build)

        self.assertIn('getattr(sys, "_MEIPASS"', hook)
        self.assertIn('getattr(os, "add_dll_directory", None)', hook)
        self.assertIn("_DLL_DIRECTORY_HANDLES.append", hook)
        self.assertIn('importlib.import_module("ctranslate2")', hook)
        self.assertIn(
            "_prepend_bundled_tools()\n_preload_ctranslate2_before_paddle()",
            hook,
        )

    def test_nvidia_runtime_notice_matches_retained_metadata_licenses(self) -> None:
        notice = self.read("THIRD_PARTY_NOTICES.md")

        self.assertIn("CUDA runtime, cuBLAS, cuDNN, cuFFT, cuRAND", notice)
        self.assertIn("every bundled wheel's license file is retained", notice)

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
        self.assertIn('$StagedPaddleDistribution -eq "paddlepaddle-gpu"', script)
        self.assertIn("full GPU 构建", script)
        self.assertIn("full CPU OCR 构建", script)
        self.assertIn('Get-Video2NotesFileSha256', script)
        self.assertNotIn('Get-FileHash', script)

    def test_portable_replacement_uses_a_validated_temporary_backup(self) -> None:
        script = self.read("scripts/build_portable.ps1")

        self.assertIn('$PortableBackup = Join-Path $PortableParent (', script)
        self.assertIn('".backup-" + [DateTime]::UtcNow.ToString', script)
        self.assertIn('$leaf.StartsWith(".backup-")', script)
        self.assertNotIn('$PortableOld', script)
        self.assertNotIn('$safeOld', script)

        self.assertIn('[switch]$AllowPreNvidiaFullRuntime', script)
        for component_id in (
            "nvidia-cublas-cu12",
            "nvidia-cuda-nvrtc-cu12",
            "nvidia-cuda-runtime-cu12",
            "nvidia-cudnn-cu12",
            "nvidia-cufft-cu12",
            "nvidia-curand-cu12",
            "nvidia-cusolver-cu12",
            "nvidia-cusparse-cu12",
            "nvidia-nvjitlink-cu12",
        ):
            self.assertIn(f'"{component_id}"', script)
        self.assertIn('$manifest.runtime_flavor -eq "full"', script)
        self.assertIn('$componentId -in $requiredNvidiaInferenceIds', script)
        self.assertIn('$matches.Count -eq 0', script)
        self.assertIn("$hasNoNvidiaComponentContract", script)
        self.assertIn('$paddleDistribution -eq "paddlepaddle-gpu"', script)

        # A newly assembled staging tree never receives the migration switch.
        staging_validation = next(
            line
            for line in script.splitlines()
            if "Assert-PortableLayout $safeStaging" in line
        )
        self.assertEqual(
            staging_validation.strip(),
            "Assert-PortableLayout $safeStaging $RuntimeFlavor",
        )
        self.assertEqual(
            script.count("-AllowPreNvidiaFullRuntimeBackend"),
            2,
        )

        self.assertIn(
            "Move-Item -LiteralPath $safeCurrent -Destination $safeBackup",
            script,
        )
        self.assertIn(
            "Move-Item -LiteralPath $safeBackup -Destination $safeCurrent",
            script,
        )
        self.assertLess(
            script.index("Assert-PortableChecksums $safeBackup"),
            script.index(
                "Move-Item -LiteralPath $safeStaging -Destination $safeCurrent"
            ),
        )
        self.assertIn("[switch]$KeepPreviousPortable", script)
        self.assertIn("Assert-PortableChecksums $safeCurrent", script)
        self.assertIn("$safeFailedCurrent = Assert-ManagedPortablePath $safeCurrent", script)
        self.assertIn("Remove-Item -LiteralPath $safeFailedCurrent", script)
        self.assertIn("Move-Item -LiteralPath $safeBackup -Destination $safeCurrent", script)
        self.assertIn("if ($KeepPreviousPortable)", script)
        self.assertIn("Previous portable app retained as requested", script)
        self.assertIn("Remove-Item -LiteralPath $safeBackup", script)
        self.assertLess(
            script.index("Assert-PortableChecksums $safeCurrent"),
            script.index("Remove-Item -LiteralPath $safeBackup"),
        )

    def test_portable_cuda_acceptance_is_isolated_and_token_safe(self) -> None:
        script = self.read("scripts/test_portable_cuda.ps1")

        for endpoint in ("/api/health", "/api/system"):
            self.assertIn(endpoint, script)
        self.assertIn('"X-Video2Notes-Token" = $Token', script)
        self.assertIn("[int]$response.StatusCode -ne 401", script)
        self.assertIn("$Asr.cuda_available -ne $true", script)
        self.assertIn("[int]$Asr.device_count -lt 1", script)
        self.assertIn('$_ -in @(\"float16\", \"int8_float16\")', script)
        for package_path in (
            "cublas",
            "cudnn",
            "cuda_nvrtc",
            "nvjitlink",
            "cuda_runtime",
            "cufft",
            "curand",
            "cusolver",
            "cusparse",
        ):
            self.assertIn(f'directory = "{package_path}"', script)
        self.assertIn('$PaddleGpuRuntime = $PaddleComponent.distribution -eq', script)
        self.assertIn("$Ocr.cuda_available -ne $true", script)

        self.assertIn('$IsolatedPath = Join-Path $env:WINDIR "System32"', script)
        self.assertIn('"PATH",', script)
        for variable in (
            "VIDEO2NOTES_NVIDIA_RUNTIME_ROOT",
            "PYTHONHOME",
            "PYTHONPATH",
        ):
            self.assertIn(f'"{variable}"', script)
        self.assertIn("$SavedEnvironment[$name]", script)
        self.assertIn("[EnvironmentVariableTarget]::Process", script)

        self.assertIn("video2notes-portable-cuda-", script)
        self.assertIn("$resolved.StartsWith($temporaryBase", script)
        self.assertIn("Assert-SafeTemporaryRoot $TemporaryRoot", script)
        self.assertIn("finally {", script)
        self.assertIn("Stop-Process -Id $Process.Id -Force", script)
        self.assertIn("-LiteralPath $SafeCleanupRoot", script)
        self.assertIn("-Recurse", script)
        self.assertIn("Start-Process", script)
        self.assertIn("-WindowStyle Hidden", script)

        argument_block = script.split(
            "$Arguments = Join-WindowsCommandLineArguments", 1
        )[1].split("$Process = Start-Process", 1)[0]
        self.assertNotIn("$Token", argument_block)
        for output_sink in (
            "Write-Host $Token",
            "Write-Output $Token",
            "Write-Verbose $Token",
            "Write-Debug $Token",
        ):
            self.assertNotIn(output_sink, script)

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

    def test_generated_data_cleanup_is_dry_run_first_and_protects_deliverables(self) -> None:
        script = self.read("scripts/cleanup_generated.ps1")

        self.assertIn("[switch]$Execute", script)
        self.assertIn('Mode: {0}', script)
        self.assertIn("if (-not $Execute)", script)
        self.assertIn("Dry run only", script)
        self.assertIn("Assert-ReparsePointsAreInternal", script)
        self.assertIn("Get-TreeInventory", script)
        self.assertIn("Remove-Item -LiteralPath $link.FullName", script)
        self.assertIn("Remove-Item -LiteralPath $target.Path", script)
        for protected in (
            "root .venv",
            "node_modules",
            "artifacts/models",
            "canonical benchmarks",
            "portable/current",
        ):
            self.assertIn(protected, script)
        self.assertIn("[switch]$IncludePortableZip", script)
        self.assertIn("Portable ZIP is protected", script)
        self.assertIn("if ($IncludePortableZip)", script)
        self.assertNotIn('Relative = ".venv"', script)
        self.assertNotIn('Relative = "apps\\desktop\\node_modules"', script)
        self.assertNotIn('Relative = "artifacts\\portable\\current"', script)


if __name__ == "__main__":
    unittest.main()
