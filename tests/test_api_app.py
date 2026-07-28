from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from video2notes.api import ApiContext, create_app
from video2notes.providers import (
    KeyringSecretStore,
    Locality,
    ModelRegistry,
    ProviderKind,
    ProviderSpec,
)
from video2notes.sources import SourceInput


class InMemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def delete_password(self, service_name: str, username: str) -> None:
        del self.values[(service_name, username)]


class ApiAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.backend = InMemoryKeyring()
        registry = ModelRegistry.with_local_defaults()
        registry.providers["cloud"] = ProviderSpec(
            id="cloud",
            display_name="Test cloud",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://example.test/v1",
            locality=Locality.CLOUD,
        )
        self.context = ApiContext(
            self.temporary.name,
            token="test-token",
            model_registry=registry,
            secret_store=KeyringSecretStore(self.backend),
        )
        self.client = TestClient(create_app(self.context))
        self.addCleanup(self.client.close)
        self.headers = {"X-Video2Notes-Token": "test-token"}

    def test_health_is_public_but_machine_data_is_protected(self) -> None:
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/system").status_code, 401)
        response = self.client.get("/api/system", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("fast", response.json()["plans"])
        self.assertIn("accurate", response.json()["plans"])

    def test_processing_estimate_and_runtime_diagnostics_are_protected(self) -> None:
        self.assertEqual(
            self.client.post(
                "/api/estimate",
                json={"duration_seconds": 600, "quality_mode": "balanced"},
            ).status_code,
            401,
        )
        estimate = self.client.post(
            "/api/estimate",
            headers=self.headers,
            json={
                "duration_seconds": 600,
                "quality_mode": "balanced",
                "source_height": 2160,
                "source_fps": 60,
            },
        )
        self.assertEqual(estimate.status_code, 200)
        self.assertGreater(
            estimate.json()["upper_seconds"],
            estimate.json()["lower_seconds"],
        )
        runtime = self.client.get("/api/runtime", headers=self.headers)
        self.assertEqual(runtime.status_code, 200)
        self.assertFalse(runtime.json()["injected"])
        self.assertIsInstance(runtime.json()["warnings"], list)

    def test_provider_secret_is_write_only(self) -> None:
        previous_pipeline = self.context.pipeline
        response = self.client.put(
            "/api/providers/cloud/secret",
            headers=self.headers,
            json={"secret": "private-value"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("private-value", response.text)
        self.assertEqual(
            self.backend.values[("Video2Notes", "cloud")],
            "private-value",
        )
        self.assertIsNot(self.context.pipeline, previous_pipeline)

    def test_run_manifest_and_artifact_are_confined(self) -> None:
        response = self.client.post(
            "/api/runs",
            headers=self.headers,
            json={
                "source": SourceInput.local("C:/video.mp4").model_dump(mode="json"),
                "quality_mode": "balanced",
            },
        )
        self.assertEqual(response.status_code, 200)
        run_id = response.json()["run_id"]
        workspace = self.context.get_workspace(run_id)
        note = workspace.artifact_path("notes", "note.md")
        note.write_text("# safe", encoding="utf-8")

        result = self.client.get(
            f"/api/runs/{run_id}/artifact",
            headers=self.headers,
            params={"path": "notes/note.md"},
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.text, "# safe")
        escape = self.client.get(
            f"/api/runs/{run_id}/artifact",
            headers=self.headers,
            params={"path": "../manifest.json"},
        )
        self.assertEqual(escape.status_code, 404)
        raw_manifest = self.client.get(
            f"/api/runs/{run_id}/artifact",
            headers=self.headers,
            params={"path": "manifest.json"},
        )
        self.assertEqual(raw_manifest.status_code, 404)

    def test_provider_registry_response_contains_no_keyring_values(self) -> None:
        self.backend.set_password("Video2Notes", "local", "private-value")
        result = self.client.get("/api/providers", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("private-value", result.text)

    def test_unknown_local_probe_is_a_safe_validation_error(self) -> None:
        missing = Path(self.temporary.name) / "missing.mp4"
        result = self.client.post(
            "/api/sources/probe",
            headers=self.headers,
            json={
                "source": SourceInput.local(missing).model_dump(mode="json"),
                "auth": {"kind": "none"},
                "policy": {"mode": "fast"},
            },
        )
        self.assertEqual(result.status_code, 422)
        self.assertNotIn("Traceback", result.text)
