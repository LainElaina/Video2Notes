from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from video2notes.api import ApiContext, create_app
from video2notes.artifacts import RunWorkspace
from video2notes.domain import (
    ArtifactKind,
    RunStatus,
    SourceDescriptor,
)
from video2notes.pipeline import PipelineOutcome, PipelineRequest, Video2NotesPipeline
from video2notes.pipeline.runner import PipelineEmitter
from video2notes.providers import KeyringSecretStore, ModelRegistry
from video2notes.sources import CancellationToken


class InMemoryKeyring:
    def set_password(self, service_name: str, username: str, password: str) -> None:
        del service_name, username, password

    def get_password(self, service_name: str, username: str) -> str | None:
        del service_name, username
        return None

    def delete_password(self, service_name: str, username: str) -> None:
        del service_name, username


class FakePipeline(Video2NotesPipeline):
    def __init__(self, runs_root: str | Path):
        self.runs_root = Path(runs_root)
        self.entered = threading.Event()
        self.release = threading.Event()

    def create_run(
        self,
        request: PipelineRequest,
        *,
        run_id: str | None = None,
    ) -> RunWorkspace:
        return RunWorkspace.create(
            self.runs_root,
            run_id=run_id,
            source=SourceDescriptor(
                kind=request.source.kind.value,
                locator=request.source.value,
            ),
            profile=request.quality_mode.value,
            processing_scope=request.processing_scope,
        )

    def run(
        self,
        workspace: RunWorkspace,
        request: PipelineRequest,
        *,
        cancel: CancellationToken | None = None,
        emit: PipelineEmitter | None = None,
    ) -> PipelineOutcome:
        cancellation = cancel or CancellationToken()
        progress = emit or _ignore_progress
        workspace.set_status(RunStatus.RUNNING)
        self.entered.set()
        if request.title_override == "block":
            self.release.wait(timeout=3)
            cancellation.raise_if_cancelled()
        if request.title_override == "fail":
            with workspace.stage("fake.process", stage_version="1"):
                raise RuntimeError("api_key=never-return-this cookie=never-return-this-either")

        progress(
            "fake.process",
            progress=0.5,
            message="api_key=event-private token=event-token",
            metrics={"diagnostic": "Bearer metric-private"},
        )
        with workspace.stage("fake.process", stage_version="1") as stage:
            markdown = workspace.artifact_path("notes", "note.md")
            html = workspace.artifact_path("render", "note.html")
            document = workspace.artifact_path("notes", "document.json")
            markdown.write_text("# Fake note", encoding="utf-8")
            html.write_text("<h1>Fake note</h1>", encoding="utf-8")
            document.write_text('{"title":"Fake note"}', encoding="utf-8")
            markdown_ref = stage.add_output(markdown, kind=ArtifactKind.NOTE)
            html_ref = stage.add_output(html, kind=ArtifactKind.RENDER)
            document_ref = stage.add_output(document, kind=ArtifactKind.NOTE)
        workspace.set_status(RunStatus.COMPLETED)
        return PipelineOutcome(
            run_id=workspace.manifest.run_id,
            processing_scope=request.processing_scope,
            markdown=markdown_ref,
            html=html_ref,
            note_document=document_ref,
            evidence_count=2,
            visual_state_count=1,
            used_deterministic_note_fallback=True,
        )


def _ignore_progress(
    stage: str,
    *,
    progress: float | None = None,
    message: str | None = None,
    metrics: dict[str, float | int | str | bool | None] | None = None,
) -> None:
    del stage, progress, message, metrics


class ApiPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.pipeline = FakePipeline(Path(self.temporary.name) / "runs")
        self.context = ApiContext(
            self.temporary.name,
            token="test-token",
            model_registry=ModelRegistry.with_local_defaults(),
            secret_store=KeyringSecretStore(InMemoryKeyring()),
            pipeline=self.pipeline,
        )
        self.client = TestClient(create_app(self.context))
        self.addCleanup(self.client.close)
        self.headers = {"X-Video2Notes-Token": "test-token"}

    def test_submit_progress_manifest_and_result_are_safe(self) -> None:
        self.context.runtime_warnings = (
            "api_key=runtime-private",
            "ASR backend is not configured.",
        )
        payload = self._payload()
        self.assertEqual(self.client.post("/api/jobs", json=payload).status_code, 401)

        submitted = self.client.post(
            "/api/jobs",
            headers=self.headers,
            json=payload,
        )
        self.assertEqual(submitted.status_code, 202)
        self.assertNotIn("locator-secret", submitted.text)
        self.assertNotIn("cookies.txt", submitted.text)
        self.assertNotIn("runtime-private", submitted.text)
        self.assertIn(
            "ASR backend is not configured.",
            submitted.json()["run"]["warnings"],
        )
        run_id = submitted.json()["run"]["run_id"]

        result = self._wait_for_terminal(run_id)
        self.assertEqual(result["job"]["state"], "completed")
        self.assertEqual(result["result"]["evidence_count"], 2)
        self.assertEqual(
            result["result"]["markdown"]["relative_path"],
            "notes/note.md",
        )
        encoded = str(result)
        self.assertNotIn("event-private", encoded)
        self.assertNotIn("event-token", encoded)
        self.assertNotIn("metric-private", encoded)

        note = self.client.get(
            f"/api/runs/{run_id}/artifact",
            headers=self.headers,
            params={"path": "notes/note.md"},
        )
        self.assertEqual(note.text, "# Fake note")

    def test_submit_accepts_and_persists_audio_only_scope(self) -> None:
        payload = self._payload()
        payload["processing_scope"] = "audio_only"

        submitted = self.client.post(
            "/api/jobs",
            headers=self.headers,
            json=payload,
        )

        self.assertEqual(submitted.status_code, 202)
        run_id = submitted.json()["run"]["run_id"]
        self.assertEqual(submitted.json()["run"]["processing_scope"], "audio_only")
        result = self._wait_for_terminal(run_id)
        self.assertEqual(result["result"]["processing_scope"], "audio_only")

    def test_failed_worker_exposes_type_not_exception_text(self) -> None:
        payload = self._payload(title_override="fail")
        submitted = self.client.post(
            "/api/jobs",
            headers=self.headers,
            json=payload,
        )
        self.assertEqual(submitted.status_code, 202)
        run_id = submitted.json()["run"]["run_id"]

        result = self._wait_for_terminal(run_id)
        self.assertEqual(result["job"]["state"], "failed")
        self.assertEqual(result["job"]["error_type"], "RuntimeError")
        self.assertEqual(
            result["run"]["stages"]["fake.process"]["error"],
            "RuntimeError",
        )
        encoded = str(result)
        self.assertNotIn("never-return-this", encoded)
        self.assertIsNone(result["result"])

        run = self.client.get(f"/api/runs/{run_id}", headers=self.headers)
        self.assertEqual(run.status_code, 200)
        self.assertNotIn("never-return-this", run.text)

    def test_cancel_is_forwarded_to_pipeline_token(self) -> None:
        payload = self._payload(title_override="block")
        submitted = self.client.post(
            "/api/jobs",
            headers=self.headers,
            json=payload,
        )
        self.assertEqual(submitted.status_code, 202)
        run_id = submitted.json()["run"]["run_id"]
        self.assertTrue(self.pipeline.entered.wait(timeout=2))

        cancellation = self.client.post(
            f"/api/jobs/{run_id}/cancel",
            headers=self.headers,
        )
        self.assertEqual(cancellation.status_code, 200)
        self.pipeline.release.set()
        result = self._wait_for_terminal(run_id)
        self.assertEqual(result["job"]["state"], "cancelled")
        self.assertIsNone(result["result"])

    def _payload(self, *, title_override: str | None = None) -> dict[str, Any]:
        return {
            "source": {
                "kind": "url",
                "value": ("https://www.youtube.com/watch?v=fake&token=locator-secret"),
            },
            "auth": {
                "kind": "cookie_file",
                "cookie_file": "C:/private/cookies.txt",
            },
            "quality_mode": "fast",
            "title_override": title_override,
            "generate_pdf": False,
        }

    def _wait_for_terminal(self, run_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = self.client.get(
                f"/api/jobs/{run_id}/result",
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 200)
            payload = cast(dict[str, Any], response.json())
            if payload["job"]["state"] in {"completed", "failed", "cancelled"}:
                return payload
            time.sleep(0.01)
        self.fail("processing job did not reach a terminal state")
