from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from video2notes.cli import (
    TOKEN_ENVIRONMENT_VARIABLE,
    CliDependencies,
    PreparedPipeline,
    PreparedServer,
    _prepare_server,
    main,
)
from video2notes.domain import ArtifactKind, ArtifactRef
from video2notes.pipeline import PipelineOutcome, PipelineRequest
from video2notes.sources import AuthKind, BrowserKind
from video2notes.sources import QualityMode as AcquisitionQualityMode
from video2notes.system import HardwareSnapshot, QualityMode


def _hardware() -> HardwareSnapshot:
    return HardwareSnapshot(
        os_name="Windows",
        os_version="11",
        architecture="AMD64",
        cpu_name="Test CPU",
        logical_cores=8,
    )


def _artifact(kind: ArtifactKind, path: str) -> ArtifactRef:
    return ArtifactRef(
        kind=kind,
        relative_path=path,
        sha256="0" * 64,
        size_bytes=1,
    )


@dataclass
class _FakeManifest:
    run_id: str = "run-cli"


class _FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root / "run-cli"
        self.root.mkdir(parents=True)
        self.manifest = _FakeManifest()
        self.cancelled = False

    def mark_cancelled(self) -> None:
        self.cancelled = True


class _FakePipeline:
    def __init__(self, runs_root: Path):
        self.runs_root = runs_root
        self.created_request: PipelineRequest | None = None
        self.run_request: PipelineRequest | None = None
        self.workspace: _FakeWorkspace | None = None

    def create_run(self, request: PipelineRequest) -> _FakeWorkspace:
        self.created_request = request
        self.workspace = _FakeWorkspace(self.runs_root)
        return self.workspace

    def run(
        self,
        workspace: _FakeWorkspace,
        request: PipelineRequest,
        *,
        cancel: object,
        emit: object,
    ) -> PipelineOutcome:
        del cancel
        self.run_request = request
        progress = emit
        progress(  # type: ignore[operator]
            "source.acquire",
            progress=0.5,
            message="downloading ?token=must-not-appear",
            metrics={"downloaded_bytes": 4},
        )
        return PipelineOutcome(
            run_id=workspace.manifest.run_id,
            markdown=_artifact(ArtifactKind.NOTE, "notes/note.md"),
            html=_artifact(ArtifactKind.RENDER, "render/note.html"),
            pdf=(
                _artifact(ArtifactKind.RENDER, "render/note.pdf") if request.generate_pdf else None
            ),
            note_document=_artifact(ArtifactKind.NOTE, "notes/document.json"),
            evidence_count=7,
            visual_state_count=3,
            used_deterministic_note_fallback=True,
        )


def _events(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


class CliProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "input video.mp4"
        self.source.write_bytes(b"fixture")
        self.cookie_file = self.root / "cookies.txt"
        self.cookie_file.write_text("SESSDATA=super-secret-cookie", encoding="utf-8")
        self.pipeline: _FakePipeline | None = None
        self.prepare_calls: list[tuple[Path, Path, HardwareSnapshot]] = []

    def dependencies(self) -> CliDependencies:
        def prepare(
            runs_root: Path,
            data_root: Path,
            hardware: HardwareSnapshot,
        ) -> PreparedPipeline:
            self.prepare_calls.append((runs_root, data_root, hardware))
            self.pipeline = _FakePipeline(runs_root)
            return PreparedPipeline(  # type: ignore[arg-type]
                pipeline=self.pipeline,
                warnings=("OCR model path is not configured.",),
            )

        def unused_server(
            data_root: Path,
            token: str,
            port: int,
            prepared: PreparedServer,
        ) -> None:
            del data_root, token, port, prepared
            raise AssertionError("server should not run")

        return CliDependencies(
            hardware_detector=_hardware,
            pipeline_preparer=prepare,
            server_preparer=lambda data_root, hardware: PreparedServer(),
            server_runner=unused_server,
        )

    def test_process_local_source_emits_ndjson_without_cookie_secrets(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        data_root = self.root / "data"
        runs_root = self.root / "custom-runs"

        result = main(
            [
                "process",
                str(self.source),
                "--mode",
                "balanced",
                "--cookie-file",
                str(self.cookie_file),
                "--language",
                "zh_Hans,en",
                "--language",
                "ja",
                "--no-screenshots",
                "--no-pdf",
                "--data-root",
                str(data_root),
                "--runs-root",
                str(runs_root),
            ],
            dependencies=self.dependencies(),
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn("super-secret-cookie", stdout.getvalue())
        self.assertNotIn("must-not-appear", stdout.getvalue())
        events = _events(stdout)
        self.assertEqual(
            [event["event"] for event in events],
            ["warning", "started", "progress", "completed"],
        )
        self.assertIn("<redacted>", str(events[2]["message"]))
        completed = events[-1]
        self.assertEqual(completed["run_id"], "run-cli")
        self.assertIsNone(completed["outputs"]["pdf"])  # type: ignore[index]
        self.assertEqual(self.prepare_calls[0][0], runs_root.resolve())
        self.assertEqual(self.prepare_calls[0][1], data_root.resolve())

        assert self.pipeline is not None
        request = self.pipeline.created_request
        assert request is not None
        self.assertEqual(request.source.value, str(self.source.resolve()))
        self.assertEqual(request.quality_mode, QualityMode.BALANCED)
        self.assertEqual(request.acquisition.mode, AcquisitionQualityMode.ACCURATE)
        self.assertEqual(request.acquisition.max_height, 1080)
        self.assertEqual(request.auth.kind, AuthKind.COOKIE_FILE)
        self.assertEqual(
            request.auth.cookie_file,
            str(self.cookie_file.resolve()),
        )
        self.assertEqual(request.language_hints, ["zh-Hans", "en", "ja"])
        self.assertFalse(request.include_screenshots)
        self.assertFalse(request.generate_pdf)

    def test_process_url_uses_browser_profile_and_accurate_acquisition(self) -> None:
        stdout = io.StringIO()
        result = main(
            [
                "process",
                "https://www.youtube.com/watch?v=fixture&token=url-secret",
                "--mode",
                "accurate",
                "--browser",
                "edge",
                "--profile",
                "Profile 2",
                "--data-root",
                str(self.root / "data"),
            ],
            dependencies=self.dependencies(),
            stdout=stdout,
            stderr=io.StringIO(),
        )

        self.assertEqual(result, 0)
        self.assertNotIn("url-secret", stdout.getvalue())
        assert self.pipeline is not None
        request = self.pipeline.created_request
        assert request is not None
        self.assertEqual(request.quality_mode, QualityMode.ACCURATE)
        self.assertEqual(request.acquisition.mode, AcquisitionQualityMode.ACCURATE)
        self.assertIsNone(request.acquisition.max_height)
        self.assertEqual(request.auth.kind, AuthKind.BROWSER_PROFILE)
        self.assertEqual(request.auth.browser, BrowserKind.EDGE)
        self.assertEqual(request.auth.profile, "Profile 2")
        self.assertTrue(request.include_screenshots)
        self.assertTrue(request.generate_pdf)

    def test_invalid_auth_is_usage_error_before_hardware_or_runtime_work(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        def should_not_run() -> HardwareSnapshot:
            raise AssertionError("hardware detection should not run")

        dependencies = self.dependencies()
        dependencies = CliDependencies(
            hardware_detector=should_not_run,
            pipeline_preparer=dependencies.pipeline_preparer,
            server_preparer=dependencies.server_preparer,
            server_runner=dependencies.server_runner,
        )
        result = main(
            ["process", str(self.source), "--browser", "chrome"],
            dependencies=dependencies,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        error = _events(stderr)[0]
        self.assertEqual(error["error_type"], "usage")
        self.assertIn("--browser and --profile", str(error["message"]))
        self.assertEqual(self.prepare_calls, [])

    def test_unsupported_url_is_a_machine_readable_usage_error(self) -> None:
        stderr = io.StringIO()
        result = main(
            ["process", "https://example.test/video"],
            dependencies=self.dependencies(),
            stdout=io.StringIO(),
            stderr=stderr,
        )
        self.assertEqual(result, 2)
        self.assertEqual(_events(stderr)[0]["error_type"], "usage")
        self.assertEqual(self.prepare_calls, [])


class CliServeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_default_server_preparation_reuses_persisted_runtime_services(self) -> None:
        prepared = _prepare_server(self.root / "prepared", _hardware())

        self.assertIsNotNone(prepared.pipeline_runtime)
        self.assertIsNotNone(prepared.model_registry)
        self.assertIsNotNone(prepared.secret_store)
        self.assertTrue((self.root / "prepared" / "config" / "providers.json").is_file())
        assert prepared.pipeline_runtime is not None
        self.assertEqual(prepared.pipeline_runtime.hardware, _hardware())
        self.assertTrue(any("model_path" in warning for warning in prepared.warnings))

    def dependencies(
        self,
        server_runner: object,
        *,
        warnings: tuple[str, ...] = (),
    ) -> CliDependencies:
        def unused_pipeline(
            runs_root: Path,
            data_root: Path,
            hardware: HardwareSnapshot,
        ) -> PreparedPipeline:
            del runs_root, data_root, hardware
            raise AssertionError("pipeline should not be prepared")

        return CliDependencies(
            hardware_detector=_hardware,
            pipeline_preparer=unused_pipeline,
            server_preparer=lambda data_root, hardware: PreparedServer(warnings=warnings),
            server_runner=server_runner,  # type: ignore[arg-type]
        )

    def test_serve_generates_ephemeral_token_file_and_never_prints_token(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        captured: dict[str, object] = {}

        def server(
            data_root: Path,
            token: str,
            port: int,
            prepared: PreparedServer,
        ) -> None:
            del prepared
            captured.update(data_root=data_root, token=token, port=port)
            token_files = list(data_root.glob(".session-token-*"))
            self.assertEqual(len(token_files), 1)
            token_file = token_files[0]
            self.assertEqual(token_file.read_text(encoding="utf-8"), token)

        data_root = self.root / "api-data"
        result = main(
            ["serve", "--data-root", str(data_root), "--port", "45678"],
            dependencies=self.dependencies(
                server,
                warnings=("ASR is disabled until a local model is configured.",),
            ),
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        token = str(captured["token"])
        self.assertGreaterEqual(len(token), 16)
        self.assertNotIn(token, stdout.getvalue())
        self.assertEqual(list(data_root.glob(".session-token-*")), [])
        self.assertEqual(captured["port"], 45678)
        events = _events(stdout)
        self.assertEqual(
            [event["event"] for event in events],
            ["warning", "server_starting", "server_stopped"],
        )
        self.assertEqual(events[1]["host"], "127.0.0.1")
        self.assertEqual(events[1]["token_source"], "generated_file")

    def test_explicit_token_is_redacted_from_server_failure(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        supplied_token = "this-is-a-private-loopback-token"

        def failed_server(
            data_root: Path,
            token: str,
            port: int,
            prepared: PreparedServer,
        ) -> None:
            del data_root, port, prepared
            raise RuntimeError(f"Authorization: Bearer {token}")

        result = main(
            [
                "serve",
                "--data-root",
                str(self.root / "data"),
                "--token",
                supplied_token,
            ],
            dependencies=self.dependencies(failed_server),
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(result, 1)
        self.assertNotIn(supplied_token, stdout.getvalue())
        self.assertNotIn(supplied_token, stderr.getvalue())
        self.assertIn("<redacted>", stderr.getvalue())
        self.assertEqual(_events(stdout)[0]["token_source"], "argument")

    def test_server_host_cannot_be_overridden(self) -> None:
        stderr = io.StringIO()
        result = main(
            ["serve", "--host", "0.0.0.0"],
            dependencies=self.dependencies(lambda data_root, token, port, prepared: None),
            stdout=io.StringIO(),
            stderr=stderr,
        )
        self.assertEqual(result, 2)
        self.assertEqual(_events(stderr)[0]["error_type"], "usage")

    def test_environment_token_is_not_persisted_or_printed(self) -> None:
        stdout = io.StringIO()
        token = "environment-only-loopback-token"
        previous = os.environ.get(TOKEN_ENVIRONMENT_VARIABLE)
        os.environ[TOKEN_ENVIRONMENT_VARIABLE] = token
        self.addCleanup(self._restore_environment, previous)

        def server(
            data_root: Path,
            supplied: str,
            port: int,
            prepared: PreparedServer,
        ) -> None:
            del port, prepared
            self.assertEqual(supplied, token)
            self.assertEqual(list(data_root.glob(".session-token-*")), [])

        result = main(
            ["serve", "--data-root", str(self.root / "data")],
            dependencies=self.dependencies(server),
            stdout=stdout,
            stderr=io.StringIO(),
        )
        self.assertEqual(result, 0)
        self.assertNotIn(token, stdout.getvalue())
        self.assertEqual(_events(stdout)[0]["token_source"], "environment")

    @staticmethod
    def _restore_environment(previous: str | None) -> None:
        if previous is None:
            os.environ.pop(TOKEN_ENVIRONMENT_VARIABLE, None)
        else:
            os.environ[TOKEN_ENVIRONMENT_VARIABLE] = previous


if __name__ == "__main__":
    unittest.main()
